from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aifaq.models import (
    ClarificationDecision,
    ClarificationTurn,
    FAQAnswer,
    AnswerType,
    QuestionClassification,
    QuestionRequest,
    QuestionScope,
    SourceFileRecord,
    SourceIndexEntry,
    SourcePriority,
    SourceScope,
    SourceStatus,
)


def now():
    return datetime.now(UTC)


def test_question_request_valid():
    req = QuestionRequest(
        question="Wi-Fiにつながりません",
        original_question="Wi-Fiにつながりません",
        thread_id="t1",
        created_at=now(),
    )
    assert req.clarification_round == 0


def test_question_request_round_out_of_range():
    with pytest.raises(ValidationError):
        QuestionRequest(
            question="q", original_question="q", thread_id="t1",
            created_at=now(), clarification_round=4,
        )


def test_clarification_turn_options_must_be_0_or_2_to_4():
    ClarificationTurn(round_no=1, question="q?", options=[], asked_at=now())
    ClarificationTurn(round_no=1, question="q?", options=["a", "b"], asked_at=now())
    with pytest.raises(ValidationError):
        ClarificationTurn(round_no=1, question="q?", options=["a"], asked_at=now())
    with pytest.raises(ValidationError):
        ClarificationTurn(
            round_no=1, question="q?", options=["a", "b", "c", "d", "e"], asked_at=now()
        )


def test_clarification_decision_requires_next_question_when_needed():
    with pytest.raises(ValidationError):
        ClarificationDecision(needs_clarification=True, next_question=None)
    ok = ClarificationDecision(needs_clarification=True, next_question="OSは何ですか？")
    assert ok.next_question


def test_question_classification_safe_flag_requires_public_scope():
    with pytest.raises(ValidationError):
        QuestionClassification(
            scope=QuestionScope.INTERNAL, safe_for_external_research=True
        )
    QuestionClassification(scope=QuestionScope.PUBLIC_GENERAL, safe_for_external_research=True)


def test_faq_answer_needs_clarification_requires_fields():
    with pytest.raises(ValidationError):
        FAQAnswer(answer="", answer_type=AnswerType.NEEDS_CLARIFICATION, thread_id="t1")
    ans = FAQAnswer(
        answer="", answer_type=AnswerType.NEEDS_CLARIFICATION, thread_id="t1",
        clarification_round=1, question="OSは何ですか？",
    )
    assert ans.question


@pytest.mark.parametrize(
    "bad_path",
    ["../secret.xlsx", "/etc/passwd", "C:\\secret.xlsx", "\\\\server\\share\\file.xlsx"],
)
def test_source_file_record_rejects_unsafe_paths(bad_path):
    with pytest.raises(ValidationError):
        SourceFileRecord(
            relative_path=bad_path,
            file_type="xlsx",
            scope=SourceScope.INTERNAL,
            size_bytes=1,
            modified_at=now(),
            sha256="a" * 64,
        )


def test_source_index_entry_rejects_unsafe_path():
    with pytest.raises(ValidationError):
        SourceIndexEntry(
            entry_id="SRC-001",
            path="../outside.xlsx",
            contains="x",
            scope=SourceScope.INTERNAL,
        )
    ok = SourceIndexEntry(
        entry_id="SRC-001", path="knowledge/public/a.md", contains="x",
        scope=SourceScope.PUBLIC, priority=SourcePriority.HIGH, status=SourceStatus.ACTIVE,
    )
    assert ok.entry_id == "SRC-001"
