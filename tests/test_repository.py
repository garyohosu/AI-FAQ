import pytest

from aifaq import db as db_module
from aifaq.models import (
    KnowledgeSourceType,
    LocationType,
    SourceChunk,
    SourceFileRecord,
    SourceScope,
    SourceStatus,
)
from aifaq.repositories import Repositories


def test_init_db_is_idempotent(conn):
    fts_first = db_module.init_db(conn)
    fts_second = db_module.init_db(conn)
    assert fts_first == fts_second


def test_knowledge_create_and_exact_search(repos):
    kid = repos.knowledge.create(
        canonical_question="社内Wi-Fiのパスワードを忘れた場合は？",
        answer="情報システム部へ連絡してください。",
        category="network",
        tags=["wifi"],
        source_type=KnowledgeSourceType.HUMAN,
        approved_by="hantani",
        variants=["Wi-Fiのパスワードがわからない"],
    )
    match = repos.knowledge.search_exact("社内Wi-Fiのパスワードを忘れた場合は？")
    assert match is not None
    assert match.knowledge_id == kid
    assert match.score == 1.0

    variant_match = repos.knowledge.search_exact("Wi-Fiのパスワードがわからない")
    assert variant_match is not None
    assert variant_match.knowledge_id == kid


def test_knowledge_fts_search_japanese_substring(repos):
    repos.knowledge.create(
        canonical_question="社内Wi-Fiのパスワードを忘れた場合の対処法",
        answer="情報システム部へ連絡してください。",
        category="network",
        tags=[],
        source_type=KnowledgeSourceType.HUMAN,
        approved_by="hantani",
    )
    results = repos.knowledge.search("パスワード")
    assert len(results) == 1


def test_knowledge_retire(repos):
    kid = repos.knowledge.create(
        canonical_question="古い手順", answer="旧回答", category="other", tags=[],
        source_type=KnowledgeSourceType.HUMAN, approved_by="hantani",
    )
    repos.knowledge.retire(kid, "hantani")
    art = repos.knowledge.get(kid)
    assert art.status.value == "RETIRED"
    assert repos.knowledge.search_exact("古い手順") is None


def test_pending_create_and_answer_creates_knowledge(repos):
    pid = repos.pending.create(
        thread_id="t1", question="申請先は？", original_question="申請先は？",
        classification={"scope": "INTERNAL"},
    )
    kid = repos.pending.answer(
        pid, answer_text="情報システム部です", category="policy", tags=[],
        variants=["どこに申請すればいい？"], approved_by="hantani",
    )
    row = repos.pending.get(pid)
    assert row["status"] == "ANSWERED"
    assert row["resulting_knowledge_id"] == kid

    art = repos.knowledge.get(kid)
    assert art.source_type.value == "HUMAN"
    assert art.status.value == "APPROVED"

    match = repos.knowledge.search_exact("どこに申請すればいい？")
    assert match is not None and match.knowledge_id == kid


def test_pending_cannot_answer_twice(repos):
    pid = repos.pending.create(
        thread_id="t1", question="q", original_question="q", classification={},
    )
    repos.pending.answer(pid, answer_text="a", category="other", tags=[], variants=[], approved_by="x")
    with pytest.raises(ValueError):
        repos.pending.answer(pid, answer_text="a2", category="other", tags=[], variants=[], approved_by="x")


def test_clarification_turn_idempotent_create(repos):
    id1 = repos.clarifications.create_turn(
        thread_id="t1", round_no=1, question="OSは？", options=[], reason="r"
    )
    id2 = repos.clarifications.create_turn(
        thread_id="t1", round_no=1, question="OSは？(retry)", options=[], reason="r"
    )
    assert id1 == id2
    assert repos.clarifications.count_rounds("t1") == 1


def test_clarification_answer_open_turn_flow(repos):
    repos.clarifications.create_turn(thread_id="t1", round_no=1, question="Q1", options=[], reason="r")
    assert repos.clarifications.answer_open_turn("t1", "A1") is True
    assert repos.clarifications.get_open_turn("t1") is None
    # 既に回答済みなので再度answerしても対象が無い
    assert repos.clarifications.answer_open_turn("t1", "A1-again") is False


def test_source_file_upsert_and_sha_diff(repos):
    from datetime import UTC, datetime

    rec = SourceFileRecord(
        relative_path="knowledge/public/a.csv", file_type="csv", scope=SourceScope.PUBLIC,
        size_bytes=10, modified_at=datetime.now(UTC), sha256="a" * 64,
    )
    fid = repos.source_files.upsert(rec)
    row = repos.source_files.get_by_path("knowledge/public/a.csv")
    assert row["id"] == fid
    assert row["sha256"] == "a" * 64

    repos.source_files.mark_missing("knowledge/public/a.csv")
    row2 = repos.source_files.get_by_path("knowledge/public/a.csv")
    assert row2["status"] == "missing"


def test_source_chunks_replace_and_search_excludes_forbidden(repos):
    from datetime import UTC, datetime

    rec = SourceFileRecord(
        relative_path="knowledge/public/a.md", file_type="md", scope=SourceScope.PUBLIC,
        size_bytes=10, modified_at=datetime.now(UTC), sha256="b" * 64,
        status=SourceStatus.ACTIVE,
    )
    fid = repos.source_files.upsert(rec)
    chunks = [
        SourceChunk(
            source_file_id=fid, location_type=LocationType.MARKDOWN_HEADING,
            heading="見出し", content="Wi-Fiの再接続手順について", content_hash="h1",
        )
    ]
    repos.source_chunks.replace_for_file(fid, chunks)
    results = repos.source_chunks.search("Wi-Fi")
    assert len(results) == 1

    # forbidden化すると検索から除外される
    rec2 = rec.model_copy(update={"status": SourceStatus.FORBIDDEN})
    repos.source_files.upsert(rec2)
    results2 = repos.source_chunks.search("Wi-Fi")
    assert len(results2) == 0
