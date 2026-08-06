"""SQLiteリポジトリ層。SQLはすべてプレースホルダーを使う。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from aifaq import db as db_module
from aifaq.models import (
    ClarificationTurn,
    KnowledgeArticle,
    KnowledgeMatch,
    KnowledgeSourceType,
    KnowledgeStatus,
    MemoryEntry,
    PendingStatus,
    SourceChunk,
    SourceFileRecord,
)
from aifaq.util import normalize_text, now_iso, parse_iso


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _fts_phrase(query: str) -> str:
    """trigramトークナイザー向けに、クエリ全体を1つの引用フレーズにする。"""
    return '"' + query.replace('"', '""') + '"'


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

    def search(self, query: str, limit: int = 5) -> list[KnowledgeMatch]:
        exact = self.search_exact(query)
        results: list[KnowledgeMatch] = [exact] if exact else []
        seen_ids = {m.knowledge_id for m in results}

        if self._fts and len(query.strip()) >= 3:
            try:
                rows = self.conn.execute(
                    """
                    SELECT k.*, bm25(knowledge_fts) AS rank
                    FROM knowledge_fts
                    JOIN knowledge_articles k ON k.id = knowledge_fts.knowledge_id
                    WHERE knowledge_fts MATCH ? AND k.status='APPROVED'
                    ORDER BY rank LIMIT ?
                    """,
                    (_fts_phrase(query.strip()), limit),
                ).fetchall()
                for row in rows:
                    if row["id"] in seen_ids:
                        continue
                    # bm25: 小さいほど良い一致。0-1へ粗く正規化する。
                    score = 1.0 / (1.0 + max(row["rank"], 0.0))
                    results.append(self._to_match(row, score=score))
                    seen_ids.add(row["id"])
            except sqlite3.OperationalError:
                pass
        if not self._fts or len(results) < limit:
            like = f"%{query}%"
            rows = self.conn.execute(
                """
                SELECT * FROM knowledge_articles
                WHERE status='APPROVED'
                  AND (canonical_question LIKE ? OR answer LIKE ?)
                LIMIT ?
                """,
                (like, like, limit),
            ).fetchall()
            for row in rows:
                if row["id"] in seen_ids:
                    continue
                results.append(self._to_match(row, score=0.5))
                seen_ids.add(row["id"])

        return results[:limit]


# ---------------------------------------------------------------------------
# Pending questions (human hand-off)
# ---------------------------------------------------------------------------


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
        """人間の回答を承認済み知識として保存し、pendingをANSWEREDにする。"""
        row = self.get(pending_id)
        if row is None:
            raise ValueError(f"pending question {pending_id} not found")
        if row["status"] != PendingStatus.OPEN.value:
            raise ValueError(f"pending question {pending_id} is not OPEN")

        knowledge_repo = KnowledgeRepository(self.conn)
        with self.conn:
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
                """
                UPDATE pending_questions
                SET status='ANSWERED', answered_at=?, answered_by=?,
                    resulting_knowledge_id=?
                WHERE id=?
                """,
                (now_iso(), approved_by, knowledge_id, pending_id),
            )
        return knowledge_id

    def cancel(self, pending_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE pending_questions SET status='CANCELLED', answered_at=? WHERE id=?",
                (now_iso(), pending_id),
            )


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
        error_summary: str | None = None,
    ) -> int:
        ts = now_iso()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO research_runs
                (thread_id, question, provider, status, confidence, sources_json,
                 warnings_json, error_summary, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
        return int(cur.lastrowid)


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

    def search(self, query: str, limit: int = 5) -> list[sqlite3.Row]:
        excluded_status = ("retired", "forbidden", "missing")
        placeholders = ",".join("?" for _ in excluded_status)
        results: list[sqlite3.Row] = []

        if self._fts and len(query.strip()) >= 3:
            try:
                results = self.conn.execute(
                    f"""
                    SELECT c.*, f.relative_path, f.scope, f.status AS file_status,
                           f.priority, f.owner, bm25(source_fts) AS rank
                    FROM source_fts
                    JOIN source_chunks c ON c.id = source_fts.chunk_id
                    JOIN source_files f ON f.id = c.source_file_id
                    WHERE source_fts MATCH ? AND f.status NOT IN ({placeholders})
                    ORDER BY rank LIMIT ?
                    """,
                    (_fts_phrase(query.strip()), *excluded_status, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                results = []

        if results:
            return results

        like = f"%{query}%"
        return self.conn.execute(
            f"""
            SELECT c.*, f.relative_path, f.scope, f.status AS file_status,
                   f.priority, f.owner
            FROM source_chunks c
            JOIN source_files f ON f.id = c.source_file_id
            WHERE c.content LIKE ? AND f.status NOT IN ({placeholders})
            LIMIT ?
            """,
            (like, *excluded_status, limit),
        ).fetchall()


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
        )
