"""SQLiteリポジトリ層。SQLはすべてプレースホルダーを使う。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from aifaq import db as db_module
from aifaq.models import (
    ClarificationSummary,
    ClarificationTurn,
    KnowledgeArticle,
    KnowledgeMatch,
    KnowledgeSourceType,
    KnowledgeStatus,
    MemoryEntry,
    PendingStatus,
    SourceChunk,
    SourceFileRecord,
    ThreadState,
    ThreadStatus,
)
from aifaq.util import (
    extract_search_terms,
    fts_terms,
    is_relevant_match,
    normalize_text,
    now_iso,
    parse_iso,
    relevance_key,
)


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _quote_fts(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _fts_phrase(query: str) -> str:
    """trigramトークナイザー向けに、クエリ全体を1つの引用フレーズにする。"""
    return _quote_fts(query)


def _fts_or(terms: list[str]) -> str:
    """複数の検索語をOR結合したFTS5クエリを作る。"""
    return " OR ".join(_quote_fts(t) for t in terms)


def _rank_by_relevance(rows: list[sqlite3.Row], terms: list[str], fields: tuple[str, ...]):
    """OR検索の結果から無関係な行を落とし、関連の強い順に並べ替える。

    OR検索は語を1つ含むだけで当たるため、そのまま使うと無関係な資料を拾う。
    `is_relevant_match` で足切りしたうえで、一致語数・最長一致語長の順に
    並べ替える(同点はFTSのbm25順=元の並びを保つ)。
    """
    scored = []
    for index, row in enumerate(rows):
        text = " ".join(str(row[f] or "") for f in fields)
        if not is_relevant_match(text, terms):
            continue
        hits, longest = relevance_key(text, terms)
        scored.append((-hits, -longest, index, row))
    scored.sort(key=lambda item: item[:3])
    return [item[3] for item in scored]


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------


class KnowledgeRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._fts = db_module.fts5_available(conn)

    def _row_to_article(self, row: sqlite3.Row) -> KnowledgeArticle:
        return KnowledgeArticle(
            id=row["id"],
            canonical_question=row["canonical_question"],
            answer=row["answer"],
            category=row["category"],
            tags=json.loads(row["tags_json"]),
            status=KnowledgeStatus(row["status"]),
            source_type=KnowledgeSourceType(row["source_type"]),
            version=row["version"],
            approved_by=row["approved_by"],
            created_at=parse_iso(row["created_at"]),
            updated_at=parse_iso(row["updated_at"]),
            valid_until=parse_iso(row["valid_until"]),
            source_urls=json.loads(row["source_urls_json"]),
        )

    def _sync_fts(self, knowledge_id: int) -> None:
        if not self._fts:
            return
        row = self.conn.execute(
            "SELECT * FROM knowledge_articles WHERE id=?", (knowledge_id,)
        ).fetchone()
        variants = self.conn.execute(
            "SELECT variant_question FROM question_variants WHERE knowledge_id=?",
            (knowledge_id,),
        ).fetchall()
        variant_text = " ".join(v["variant_question"] for v in variants)
        self.conn.execute(
            "DELETE FROM knowledge_fts WHERE knowledge_id=?", (knowledge_id,)
        )
        if row is not None:
            self.conn.execute(
                "INSERT INTO knowledge_fts "
                "(knowledge_id, canonical_question, answer, tags, variants) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    knowledge_id,
                    row["canonical_question"],
                    row["answer"],
                    " ".join(json.loads(row["tags_json"])),
                    variant_text,
                ),
            )

    def create(
        self,
        *,
        canonical_question: str,
        answer: str,
        category: str,
        tags: list[str],
        source_type: KnowledgeSourceType,
        approved_by: str | None,
        variants: list[str] | None = None,
        valid_until: str | None = None,
        source_urls: list[str] | None = None,
        change_reason: str = "",
    ) -> int:
        ts = now_iso()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO knowledge_articles
                (canonical_question, canonical_question_norm, answer, category,
                 tags_json, status, source_type, version, approved_by,
                 created_at, updated_at, valid_until, source_urls_json)
                VALUES (?, ?, ?, ?, ?, 'APPROVED', ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    canonical_question,
                    normalize_text(canonical_question),
                    answer,
                    category,
                    _dumps(tags),
                    source_type.value,
                    approved_by,
                    ts,
                    ts,
                    valid_until,
                    _dumps(source_urls or []),
                ),
            )
            knowledge_id = int(cur.lastrowid)
            self.conn.execute(
                """
                INSERT INTO knowledge_revisions
                (knowledge_id, version, canonical_question, answer, changed_by,
                 changed_at, change_reason)
                VALUES (?, 1, ?, ?, ?, ?, ?)
                """,
                (knowledge_id, canonical_question, answer, approved_by, ts, change_reason),
            )
            for variant in variants or []:
                self.conn.execute(
                    """
                    INSERT INTO question_variants
                    (knowledge_id, variant_question, variant_question_norm, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (knowledge_id, variant, normalize_text(variant), ts),
                )
            self._sync_fts(knowledge_id)
        return knowledge_id

    def add_variant(self, knowledge_id: int, variant_question: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO question_variants
                (knowledge_id, variant_question, variant_question_norm, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (knowledge_id, variant_question, normalize_text(variant_question), now_iso()),
            )
            self._sync_fts(knowledge_id)

    def get(self, knowledge_id: int) -> KnowledgeArticle | None:
        row = self.conn.execute(
            "SELECT * FROM knowledge_articles WHERE id=?", (knowledge_id,)
        ).fetchone()
        return self._row_to_article(row) if row else None

    def list(self, status: KnowledgeStatus | None = None) -> list[KnowledgeArticle]:
        if status is not None:
            rows = self.conn.execute(
                "SELECT * FROM knowledge_articles WHERE status=? ORDER BY id",
                (status.value,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM knowledge_articles ORDER BY id"
            ).fetchall()
        return [self._row_to_article(r) for r in rows]

    def retire(self, knowledge_id: int, by: str) -> None:
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                "UPDATE knowledge_articles SET status='RETIRED', updated_at=? WHERE id=?",
                (ts, knowledge_id),
            )
            self._sync_fts(knowledge_id)

    def search_exact(self, question: str) -> KnowledgeMatch | None:
        norm = normalize_text(question)
        row = self.conn.execute(
            """
            SELECT * FROM knowledge_articles
            WHERE status='APPROVED' AND canonical_question_norm=?
            ORDER BY version DESC LIMIT 1
            """,
            (norm,),
        ).fetchone()
        if row is None:
            variant = self.conn.execute(
                """
                SELECT knowledge_id FROM question_variants
                WHERE variant_question_norm=? LIMIT 1
                """,
                (norm,),
            ).fetchone()
            if variant is None:
                return None
            row = self.conn.execute(
                "SELECT * FROM knowledge_articles WHERE id=? AND status='APPROVED'",
                (variant["knowledge_id"],),
            ).fetchone()
            if row is None:
                return None
        return self._to_match(row, score=1.0)

    def _to_match(self, row: sqlite3.Row, score: float) -> KnowledgeMatch:
        is_stale = False
        valid_until = row["valid_until"]
        if valid_until:
            from datetime import datetime as _dt

            is_stale = parse_iso(valid_until) < _dt.now(parse_iso(valid_until).tzinfo)
        return KnowledgeMatch(
            knowledge_id=row["id"],
            score=score,
            canonical_question=row["canonical_question"],
            answer=row["answer"],
            version=row["version"],
            is_stale=is_stale,
        )

    def _fts_search(self, match_expr: str, limit: int) -> list[sqlite3.Row]:
        try:
            return self.conn.execute(
                """
                SELECT k.*, bm25(knowledge_fts) AS rank
                FROM knowledge_fts
                JOIN knowledge_articles k ON k.id = knowledge_fts.knowledge_id
                WHERE knowledge_fts MATCH ? AND k.status='APPROVED'
                ORDER BY rank LIMIT ?
                """,
                (match_expr, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    def search(self, query: str, limit: int = 5) -> list[KnowledgeMatch]:
        """承認済み知識を検索する。

        優先順位は「完全一致 → フレーズ一致 → 語のOR一致 → LIKE」。
        完全一致(score=1.0)とフレーズ一致は従来どおりの扱いを保ち、
        語のOR一致は取りこぼし救済なのでLIKEと同じ低いスコアを与える
        (`knowledge_match_threshold` を越えず、確認質問や調査経路を
        勝手に飛ばさないようにするため)。
        """
        q = query.strip()
        exact = self.search_exact(query)
        results: list[KnowledgeMatch] = [exact] if exact else []
        seen_ids = {m.knowledge_id for m in results}

        def add(rows, score_of) -> None:
            for row in rows:
                if row["id"] in seen_ids:
                    continue
                results.append(self._to_match(row, score=score_of(row)))
                seen_ids.add(row["id"])

        # フレーズ一致: bm25 を 0-1 へ粗く正規化する(小さいほど良い一致)。
        if self._fts and len(q) >= 3:
            add(
                self._fts_search(_fts_phrase(q), limit),
                lambda row: 1.0 / (1.0 + max(row["rank"], 0.0)),
            )

        # 語のOR一致: 自然文の質問でも承認済み知識を取りこぼさないための救済。
        # 関連度は全検索語に対して測る(理由は SourceChunkRepository.search を参照)。
        if self._fts and len(results) < limit:
            or_terms = fts_terms(q)
            if or_terms:
                rows = self._fts_search(_fts_or(or_terms), limit * 5)
                ranked = _rank_by_relevance(
                    rows, extract_search_terms(q), ("canonical_question", "answer")
                )
                add(ranked[:limit], lambda row: 0.5)

        if not self._fts or len(results) < limit:
            like = f"%{q}%"
            rows = self.conn.execute(
                """
                SELECT * FROM knowledge_articles
                WHERE status='APPROVED'
                  AND (canonical_question LIKE ? OR answer LIKE ?)
                LIMIT ?
                """,
                (like, like, limit),
            ).fetchall()
            add(rows, lambda row: 0.5)

        return results[:limit]


# ---------------------------------------------------------------------------
# Pending questions (human hand-off)
# ---------------------------------------------------------------------------


class AlreadyAnsweredError(ValueError):
    """既に回答済みのpendingへ再度回答しようとした。

    既存回答を上書きせず、明確なエラーとして扱う (instruction-006 §4.3)。
    従来 `answer()` は `ValueError` を送出していたため、既存の呼び出し側と
    テストの互換性を保つよう `ValueError` を継承する。
    """


class PendingRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        thread_id: str,
        question: str,
        original_question: str,
        classification: dict,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO pending_questions
                (thread_id, question, original_question, classification_json,
                 status, created_at)
                VALUES (?, ?, ?, ?, 'OPEN', ?)
                """,
                (thread_id, question, original_question, _dumps(classification), now_iso()),
            )
        return int(cur.lastrowid)

    def get(self, pending_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM pending_questions WHERE id=?", (pending_id,)
        ).fetchone()

    def get_open_by_thread(self, thread_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM pending_questions WHERE thread_id=? AND status='OPEN' "
            "ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()

    def list(self, status: PendingStatus | None = None) -> list[sqlite3.Row]:
        if status is not None:
            return self.conn.execute(
                "SELECT * FROM pending_questions WHERE status=? ORDER BY id",
                (status.value,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM pending_questions ORDER BY id"
        ).fetchall()

    def answer(
        self,
        pending_id: int,
        *,
        answer_text: str,
        category: str,
        tags: list[str],
        variants: list[str],
        approved_by: str,
        valid_until: str | None = None,
        change_reason: str = "IT管理者による人間回答",
    ) -> int:
        """人間の回答を保存する。

        次を1つのトランザクションで行う (instruction-006 §4.3)。

        1. 承認済み知識として `knowledge_articles` へ保存(従来どおり)
        2. **元のpendingへ回答本文・回答者・回答日時を保存**
        3. pendingを `ANSWERED` にする
        4. 元のthread_idへ紐付けて `query_history` へ記録

        二重回答は `WHERE id=? AND status='OPEN'` の更新件数で防ぐ。
        SELECTで確認してからUPDATEするだけでは、2つのターミナルから
        同時に実行されたときに両方が通ってしまうため、更新条件自体に
        状態を含めて不可分に判定する。
        """
        row = self.get(pending_id)
        if row is None:
            raise ValueError(f"pending question {pending_id} not found")
        if row["status"] != PendingStatus.OPEN.value:
            raise AlreadyAnsweredError(
                f"pending question {pending_id} は既に {row['status']} です"
            )

        knowledge_repo = KnowledgeRepository(self.conn)
        ts = now_iso()
        with self.conn:
            # 先に「OPEN のものだけを ANSWERED にする」更新を行い、
            # 勝ち取れた場合のみ知識を作る。負けた側は0件更新で検出できる。
            cur = self.conn.execute(
                """
                UPDATE pending_questions
                SET status='ANSWERED', answered_at=?, answered_by=?,
                    answer_text=?, answer_type='HUMAN', updated_at=?
                WHERE id=? AND status='OPEN'
                """,
                (ts, approved_by, answer_text, ts, pending_id),
            )
            if cur.rowcount == 0:
                raise AlreadyAnsweredError(
                    f"pending question {pending_id} は既に回答済みです"
                    "(他のターミナルから先に回答された可能性があります)"
                )

            knowledge_id = knowledge_repo.create(
                canonical_question=row["original_question"],
                answer=answer_text,
                category=category,
                tags=tags,
                source_type=KnowledgeSourceType.HUMAN,
                approved_by=approved_by,
                variants=variants,
                valid_until=valid_until,
                change_reason=change_reason,
            )
            self.conn.execute(
                "UPDATE pending_questions SET resulting_knowledge_id=? WHERE id=?",
                (knowledge_id, pending_id),
            )
            # 元のthreadの履歴にも人間回答を残す。
            self.conn.execute(
                """
                INSERT INTO query_history
                (thread_id, question, classification_json, route, answer_type,
                 knowledge_ids_json, research_run_id, created_at)
                VALUES (?, ?, ?, 'human', 'HUMAN_ANSWER', ?, NULL, ?)
                """,
                (
                    row["thread_id"],
                    row["original_question"],
                    row["classification_json"],
                    _dumps([knowledge_id]),
                    ts,
                ),
            )
        return knowledge_id

    def mark_delivered(self, pending_id: int) -> None:
        """質問者が回答を受け取ったことを記録する。

        「回答の保存」と「質問者が読んだこと」は分離する (§7)。
        受領しても状態は `ANSWERED` のままで、`delivered_at` だけを更新する。
        初回受領の時刻を保ちたいので、既に記録済みなら上書きしない。
        """
        with self.conn:
            self.conn.execute(
                """
                UPDATE pending_questions
                SET delivery_status='DELIVERED', delivered_at=?
                WHERE id=? AND delivered_at IS NULL
                """,
                (now_iso(), pending_id),
            )

    def cancel(self, pending_id: int) -> None:
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                "UPDATE pending_questions "
                "SET status='CANCELLED', answered_at=?, updated_at=? WHERE id=?",
                (ts, ts, pending_id),
            )

    def get_latest_by_thread(self, thread_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM pending_questions WHERE thread_id=? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()


# ---------------------------------------------------------------------------
# History / research runs
# ---------------------------------------------------------------------------


class QueryHistoryRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(
        self,
        *,
        thread_id: str,
        question: str,
        classification: dict,
        route: str,
        answer_type: str,
        knowledge_ids: list[int],
        research_run_id: int | None = None,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO query_history
                (thread_id, question, classification_json, route, answer_type,
                 knowledge_ids_json, research_run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    question,
                    _dumps(classification),
                    route,
                    answer_type,
                    _dumps(knowledge_ids),
                    research_run_id,
                    now_iso(),
                ),
            )
        return int(cur.lastrowid)

    def list_for_thread(self, thread_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM query_history WHERE thread_id=? ORDER BY id",
            (thread_id,),
        ).fetchall()


class ResearchRunRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(
        self,
        *,
        thread_id: str,
        question: str,
        provider: str,
        status: str,
        confidence: float | None,
        sources: list[dict],
        warnings: list[str],
        answer: str = "",
        error_summary: str | None = None,
    ) -> int:
        ts = now_iso()
        # 調査に失敗した run は人間承認の対象にならないので、はじめから
        # レビュー対象外(NOT_APPLICABLE)にしておく。
        review_status = "PENDING" if status == "ok" else "NOT_APPLICABLE"
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO research_runs
                (thread_id, question, provider, status, confidence, sources_json,
                 warnings_json, error_summary, started_at, finished_at,
                 answer, review_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    question,
                    provider,
                    status,
                    confidence,
                    _dumps(sources),
                    _dumps(warnings),
                    error_summary,
                    ts,
                    ts,
                    answer,
                    review_status,
                ),
            )
        return int(cur.lastrowid)

    def get(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM research_runs WHERE id=?", (run_id,)
        ).fetchone()

    def list(self, review_status: str | None = None) -> list[sqlite3.Row]:
        if review_status is None:
            return self.conn.execute(
                "SELECT * FROM research_runs ORDER BY id DESC"
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM research_runs WHERE review_status=? ORDER BY id DESC",
            (review_status,),
        ).fetchall()

    def mark_reviewed(
        self,
        run_id: int,
        *,
        review_status: str,
        reviewed_by: str,
        reason: str = "",
        was_modified: bool = False,
        approved_answer: str | None = None,
        resulting_knowledge_id: int | None = None,
    ) -> None:
        """レビュー結果を記録する。

        ``review_status='PENDING'`` の行だけを更新することで、同じ run の
        二重承認を SQL の条件で防ぐ。更新件数0なら呼び出し側が弾く。
        """
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE research_runs
                SET review_status=?, reviewed_by=?, reviewed_at=?, review_reason=?,
                    was_modified=?, approved_answer=?, resulting_knowledge_id=?
                WHERE id=? AND review_status='PENDING'
                """,
                (
                    review_status,
                    reviewed_by,
                    now_iso(),
                    reason,
                    1 if was_modified else 0,
                    approved_answer,
                    resulting_knowledge_id,
                    run_id,
                ),
            )
        if cur.rowcount == 0:
            raise ValueError(
                f"research run {run_id} はレビュー待ち(PENDING)ではありません"
            )


# ---------------------------------------------------------------------------
# Source import runs
# ---------------------------------------------------------------------------


class SourceImportRunRepository:
    """知識取り込みの実行単位を記録する (instruction-005 §5.2)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def start(self, *, target_path: str, actor: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO source_import_runs (started_at, target_path, actor)
                VALUES (?, ?, ?)
                """,
                (now_iso(), target_path, actor),
            )
        return int(cur.lastrowid)

    def finish(
        self,
        run_id: int,
        *,
        detected: int = 0,
        added: int = 0,
        updated: int = 0,
        skipped_unchanged: int = 0,
        missing: int = 0,
        failed: int = 0,
        warnings: list[str] | None = None,
        error_summary: str = "",
        succeeded: bool = True,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE source_import_runs
                SET finished_at=?, processed=?, imported=?, skipped_unchanged=?,
                    failed=?, warnings_json=?, detected=?, added=?, updated=?,
                    missing=?, error_summary=?, succeeded=?
                WHERE id=?
                """,
                (
                    now_iso(),
                    detected,
                    added + updated,
                    skipped_unchanged,
                    failed,
                    _dumps(warnings or []),
                    detected,
                    added,
                    updated,
                    missing,
                    error_summary,
                    1 if succeeded else 0,
                    run_id,
                ),
            )

    def get(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM source_import_runs WHERE id=?", (run_id,)
        ).fetchone()

    def list(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM source_import_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


# ---------------------------------------------------------------------------
# Clarification turns
# ---------------------------------------------------------------------------


class ClarificationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_turn(
        self, *, thread_id: str, round_no: int, question: str, options: list[str], reason: str
    ) -> int:
        """(thread_id, round_no) が既にあれば再利用する(interrupt前の冪等性)。"""
        existing = self.conn.execute(
            "SELECT id FROM clarification_turns WHERE thread_id=? AND round_no=?",
            (thread_id, round_no),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO clarification_turns
                (thread_id, round_no, question, options_json, reason, asked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (thread_id, round_no, question, _dumps(options), reason, now_iso()),
            )
            if cur.lastrowid and cur.rowcount:
                return int(cur.lastrowid)
        row = self.conn.execute(
            "SELECT id FROM clarification_turns WHERE thread_id=? AND round_no=?",
            (thread_id, round_no),
        ).fetchone()
        return int(row["id"])

    def get_open_turn(self, thread_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM clarification_turns
            WHERE thread_id=? AND answer IS NULL
            ORDER BY round_no DESC LIMIT 1
            """,
            (thread_id,),
        ).fetchone()

    def answer_open_turn(self, thread_id: str, answer_text: str) -> bool:
        open_turn = self.get_open_turn(thread_id)
        if open_turn is None:
            return False
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE clarification_turns SET answer=?, answered_at=?
                WHERE id=? AND answer IS NULL
                """,
                (answer_text, now_iso(), open_turn["id"]),
            )
        return cur.rowcount == 1

    def count_rounds(self, thread_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM clarification_turns WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        return int(row["c"])

    def list_for_thread(self, thread_id: str) -> list[ClarificationTurn]:
        rows = self.conn.execute(
            "SELECT * FROM clarification_turns WHERE thread_id=? ORDER BY round_no",
            (thread_id,),
        ).fetchall()
        return [
            ClarificationTurn(
                round_no=r["round_no"],
                question=r["question"],
                answer=r["answer"],
                options=json.loads(r["options_json"]),
                reason=r["reason"],
                asked_at=parse_iso(r["asked_at"]),
                answered_at=parse_iso(r["answered_at"]),
            )
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Source files / chunks (knowledge ingestion, instruction 003)
# ---------------------------------------------------------------------------


class SourceFileRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get_by_path(self, relative_path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM source_files WHERE relative_path=?", (relative_path,)
        ).fetchone()

    def upsert(self, record: SourceFileRecord) -> int:
        existing = self.get_by_path(record.relative_path)
        with self.conn:
            if existing is None:
                cur = self.conn.execute(
                    """
                    INSERT INTO source_files
                    (relative_path, file_type, scope, category, owner, priority,
                     status, size_bytes, modified_at, sha256, last_imported_at,
                     last_reviewed_at, valid_until, error_summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.relative_path,
                        record.file_type,
                        record.scope.value,
                        record.category,
                        record.owner,
                        record.priority.value,
                        record.status.value,
                        record.size_bytes,
                        record.modified_at.isoformat(),
                        record.sha256,
                        record.last_imported_at.isoformat()
                        if record.last_imported_at
                        else None,
                        record.last_reviewed_at.isoformat()
                        if record.last_reviewed_at
                        else None,
                        record.valid_until.isoformat() if record.valid_until else None,
                        record.error_summary,
                    ),
                )
                return int(cur.lastrowid)
            self.conn.execute(
                """
                UPDATE source_files SET
                    file_type=?, scope=?, category=?, owner=?, priority=?, status=?,
                    size_bytes=?, modified_at=?, sha256=?, last_imported_at=?,
                    last_reviewed_at=?, valid_until=?, error_summary=?
                WHERE relative_path=?
                """,
                (
                    record.file_type,
                    record.scope.value,
                    record.category,
                    record.owner,
                    record.priority.value,
                    record.status.value,
                    record.size_bytes,
                    record.modified_at.isoformat(),
                    record.sha256,
                    record.last_imported_at.isoformat() if record.last_imported_at else None,
                    record.last_reviewed_at.isoformat() if record.last_reviewed_at else None,
                    record.valid_until.isoformat() if record.valid_until else None,
                    record.error_summary,
                    record.relative_path,
                ),
            )
            return int(existing["id"])

    def list(self, status: str | None = None) -> list[sqlite3.Row]:
        if status is not None:
            return self.conn.execute(
                "SELECT * FROM source_files WHERE status=? ORDER BY relative_path",
                (status,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM source_files ORDER BY relative_path"
        ).fetchall()

    def mark_missing(self, relative_path: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE source_files SET status='missing' WHERE relative_path=?",
                (relative_path,),
            )


class SourceChunkRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._fts = db_module.fts5_available(conn)

    def replace_for_file(self, source_file_id: int, chunks: list[SourceChunk]) -> None:
        with self.conn:
            old_ids = [
                r["id"]
                for r in self.conn.execute(
                    "SELECT id FROM source_chunks WHERE source_file_id=?",
                    (source_file_id,),
                ).fetchall()
            ]
            for cid in old_ids:
                self.conn.execute("DELETE FROM source_fts WHERE chunk_id=?", (cid,)) \
                    if self._fts else None
            self.conn.execute(
                "DELETE FROM source_chunks WHERE source_file_id=?", (source_file_id,)
            )
            for chunk in chunks:
                cur = self.conn.execute(
                    """
                    INSERT INTO source_chunks
                    (source_file_id, location_type, sheet_name, row_start, row_end,
                     heading, content, content_hash, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        source_file_id,
                        chunk.location_type.value,
                        chunk.sheet_name,
                        chunk.row_start,
                        chunk.row_end,
                        chunk.heading,
                        chunk.content,
                        chunk.content_hash,
                    ),
                )
                if self._fts:
                    self.conn.execute(
                        """
                        INSERT INTO source_fts (chunk_id, source_file_id, heading, content)
                        VALUES (?, ?, ?, ?)
                        """,
                        (cur.lastrowid, source_file_id, chunk.heading or "", chunk.content),
                    )

    _EXCLUDED_STATUS = ("retired", "forbidden", "missing")
    #: OR検索では足切り前に多めに取り、関連順に並べ替えてから絞る。
    _OVERFETCH = 5

    def _fts_query(self, match_expr: str, limit: int) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in self._EXCLUDED_STATUS)
        try:
            return self.conn.execute(
                f"""
                SELECT c.*, f.relative_path, f.scope, f.status AS file_status,
                       f.priority, f.owner, bm25(source_fts) AS rank
                FROM source_fts
                JOIN source_chunks c ON c.id = source_fts.chunk_id
                JOIN source_files f ON f.id = c.source_file_id
                WHERE source_fts MATCH ? AND f.status NOT IN ({placeholders})
                ORDER BY rank LIMIT ?
                """,
                (match_expr, *self._EXCLUDED_STATUS, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # 不正なFTS式や索引未作成でも検索全体を落とさない。
            return []

    def _like_query(self, terms: list[str], limit: int) -> list[sqlite3.Row]:
        status_placeholders = ",".join("?" for _ in self._EXCLUDED_STATUS)
        like_clause = " OR ".join("c.content LIKE ?" for _ in terms)
        return self.conn.execute(
            f"""
            SELECT c.*, f.relative_path, f.scope, f.status AS file_status,
                   f.priority, f.owner
            FROM source_chunks c
            JOIN source_files f ON f.id = c.source_file_id
            WHERE ({like_clause}) AND f.status NOT IN ({status_placeholders})
            LIMIT ?
            """,
            (*[f"%{t}%" for t in terms], *self._EXCLUDED_STATUS, limit),
        ).fetchall()

    def search(self, query: str, limit: int = 5) -> list[sqlite3.Row]:
        """取り込み済み資料を検索する。

        自然文の質問をそのまま1フレーズとして検索すると、文全体が資料に
        現れることはまずないため、ほぼ必ず0件になる。そこで段階的に
        フォールバックする:

        1. クエリ全体のフレーズ検索 (最も精度が高い。完全一致を優先)
        2. 抽出した検索語のOR検索 (FTS5、3文字以上の語のみ)
        3. 抽出した検索語のLIKE検索 (2文字語やFTS5非対応環境向け)
        4. クエリ全体のLIKE検索 (最後の手段)

        2と3は足切り(`is_relevant_match`)と関連順の並べ替えを行い、
        語を1つ含むだけの無関係な資料を拾わないようにする。
        """
        q = query.strip()
        if not q:
            return []

        # 1. フレーズ検索
        if self._fts and len(q) >= 3:
            rows = self._fts_query(_fts_phrase(q), limit)
            if rows:
                return rows

        # 関連度は必ず「抽出した全検索語」に対して測る。FTS5へ渡せる3文字以上の
        # 語だけで測ると、短い語が分母から消えて関連度が過大評価され、
        # 「Windows」のような一般語ひとつで無関係な資料を拾ってしまう。
        all_terms = extract_search_terms(q)

        # 2. 検索語のOR検索 (FTS5)
        or_terms = fts_terms(q)
        if self._fts and or_terms:
            rows = self._fts_query(_fts_or(or_terms), limit * self._OVERFETCH)
            ranked = _rank_by_relevance(rows, all_terms, ("content", "heading"))
            if ranked:
                return ranked[:limit]

        # 3. 検索語のLIKE検索 (2文字語・FTS5非対応環境)
        if all_terms:
            rows = self._like_query(all_terms, limit * self._OVERFETCH)
            ranked = _rank_by_relevance(rows, all_terms, ("content", "heading"))
            if ranked:
                return ranked[:limit]

        # 4. クエリ全体のLIKE検索
        return self._like_query([q], limit)


class MemoryEntryRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace_all(self, entries: list[MemoryEntry]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM memory_fts")
            self.conn.execute("DELETE FROM memory_entries")
            for entry in entries:
                cur = self.conn.execute(
                    """
                    INSERT INTO memory_entries
                    (memory_type, key, value, source_markdown_path, heading, status,
                     approved_by, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.memory_type.value,
                        entry.key,
                        entry.value,
                        entry.source_markdown_path,
                        entry.heading,
                        entry.status,
                        entry.approved_by,
                        entry.updated_at.isoformat(),
                    ),
                )
                if db_module.fts5_available(self.conn):
                    self.conn.execute(
                        "INSERT INTO memory_fts (memory_id, key, value) VALUES (?, ?, ?)",
                        (cur.lastrowid, entry.key, entry.value),
                    )

    def list(self, memory_type: str | None = None) -> list[sqlite3.Row]:
        if memory_type is not None:
            return self.conn.execute(
                "SELECT * FROM memory_entries WHERE memory_type=? ORDER BY id",
                (memory_type,),
            ).fetchall()
        return self.conn.execute("SELECT * FROM memory_entries ORDER BY id").fetchall()


@dataclass
class Repositories:
    conn: sqlite3.Connection
    knowledge: KnowledgeRepository
    pending: PendingRepository
    history: QueryHistoryRepository
    research_runs: ResearchRunRepository
    clarifications: ClarificationRepository
    source_files: SourceFileRepository
    source_chunks: SourceChunkRepository
    memory_entries: MemoryEntryRepository
    import_runs: SourceImportRunRepository

    @classmethod
    def build(cls, conn: sqlite3.Connection) -> "Repositories":
        return cls(
            conn=conn,
            knowledge=KnowledgeRepository(conn),
            pending=PendingRepository(conn),
            history=QueryHistoryRepository(conn),
            research_runs=ResearchRunRepository(conn),
            clarifications=ClarificationRepository(conn),
            source_files=SourceFileRepository(conn),
            source_chunks=SourceChunkRepository(conn),
            memory_entries=MemoryEntryRepository(conn),
            import_runs=SourceImportRunRepository(conn),
        )

    # -- 状態取得 (instruction-006 §4.1 / §5) -------------------------------

    def thread_status(
        self, *, thread_id: str | None = None, pending_id: int | None = None
    ) -> ThreadStatus:
        """スレッドの現在状態をまとめて返す。

        CLIが複数テーブルを場当たり的に読まずに済むよう、状態の組み立ては
        すべてここで行う。`status` と `watch` の両方がこれを使う。

        判定順は「pendingの状態 → 未回答の確認質問 → 履歴の完了」。
        pendingが最優先なのは、人間引き継ぎが最も強い状態だから。
        """
        pending = None
        if pending_id is not None:
            pending = self.pending.get(pending_id)
            if pending is None:
                return ThreadStatus(thread_id=thread_id or "", state=ThreadState.NOT_FOUND)
            thread_id = pending["thread_id"]
        elif thread_id is not None:
            pending = self.pending.get_latest_by_thread(thread_id)

        if not thread_id:
            return ThreadStatus(thread_id="", state=ThreadState.NOT_FOUND)

        clarifications = [
            ClarificationSummary(round_no=t.round_no, question=t.question, answer=t.answer)
            for t in self.clarifications.list_for_thread(thread_id)
        ]
        history = self.history.list_for_thread(thread_id)

        if pending is None and not clarifications and not history:
            return ThreadStatus(thread_id=thread_id, state=ThreadState.NOT_FOUND)

        base = {
            "thread_id": thread_id,
            "clarifications": clarifications,
        }

        if pending is not None:
            base["pending_id"] = int(pending["id"])
            base["original_question"] = pending["original_question"]
            base["created_at"] = parse_iso(pending["created_at"])

            status = pending["status"]
            if status == PendingStatus.ANSWERED.value:
                return ThreadStatus(
                    **base,
                    state=ThreadState.ANSWERED,
                    answer=pending["answer_text"],
                    answer_type=pending["answer_type"] or "HUMAN",
                    answered_by=pending["answered_by"],
                    answered_at=parse_iso(pending["answered_at"]),
                    delivery_status=pending["delivery_status"],
                    delivered_at=parse_iso(pending["delivered_at"]),
                    knowledge_id=pending["resulting_knowledge_id"],
                )
            if status == PendingStatus.CANCELLED.value:
                return ThreadStatus(
                    **base,
                    state=ThreadState.CANCELLED,
                    answered_at=parse_iso(pending["answered_at"]),
                )
            return ThreadStatus(**base, state=ThreadState.PENDING_HUMAN)

        open_turn = self.clarifications.get_open_turn(thread_id)
        if open_turn is not None:
            return ThreadStatus(
                **base,
                state=ThreadState.NEEDS_CLARIFICATION,
                original_question=history[0]["question"] if history else None,
                next_question=open_turn["question"],
                options=json.loads(open_turn["options_json"] or "[]"),
                created_at=parse_iso(open_turn["asked_at"]),
            )

        if history:
            last = history[-1]
            return ThreadStatus(
                **base,
                state=ThreadState.COMPLETED,
                original_question=history[0]["question"],
                answer_type=last["answer_type"],
                created_at=parse_iso(last["created_at"]),
            )

        return ThreadStatus(thread_id=thread_id, state=ThreadState.NOT_FOUND)
