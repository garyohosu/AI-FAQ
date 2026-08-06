from aifaq.config import Settings
from aifaq.routing import (
    check_escalation,
    count_consecutive_unknown,
    decide_route,
    is_vague_public_question,
    pick_clarification_template,
)


def _classification(scope, category="other", safe=False, reason=""):
    return {"scope": scope, "category": category, "safe_for_external_research": safe, "reason": reason}


def test_decide_route_prefers_knowledge_match():
    settings = Settings()
    state = {
        "classification": _classification("PUBLIC_GENERAL", safe=True),
        "clarification_history": [],
        "clarification_round": 0,
        "knowledge_matches": [
            {"knowledge_id": 1, "score": 1.0, "canonical_question": "q", "answer": "a",
             "version": 1, "is_stale": False}
        ],
        "source_chunks": [],
        "conflict": False,
    }
    decision = decide_route(state, settings)
    assert decision.route == "knowledge"


def test_decide_route_security_sensitive_escalates_immediately():
    settings = Settings()
    state = {
        "classification": _classification(
            "SECURITY_SENSITIVE", reason="秘密情報らしき語を検出"
        ),
        "clarification_history": [],
        "clarification_round": 0,
        "knowledge_matches": [],
        "source_chunks": [],
        "conflict": False,
    }
    decision = decide_route(state, settings)
    assert decision.route == "human"


def test_decide_route_internal_question_asks_clarification():
    settings = Settings()
    state = {
        "classification": _classification("INTERNAL", category="windows"),
        "clarification_history": [],
        "clarification_round": 0,
        "knowledge_matches": [],
        "source_chunks": [],
        "conflict": False,
        "question": "社内PCの調子が悪い",
    }
    decision = decide_route(state, settings)
    assert decision.route == "clarify"
    assert decision.clarification is not None


def test_decide_route_escalates_after_max_rounds():
    settings = Settings()
    state = {
        "classification": _classification("INTERNAL", category="windows"),
        "clarification_history": [{"question": f"q{i}", "answer": "a"} for i in range(3)],
        "clarification_round": 3,
        "knowledge_matches": [],
        "source_chunks": [],
        "conflict": False,
        "question": "社内PCの調子が悪い",
    }
    decision = decide_route(state, settings)
    assert decision.route == "human"


def test_decide_route_public_specific_goes_to_research():
    settings = Settings()
    state = {
        "classification": _classification("PUBLIC_GENERAL", category="windows", safe=True),
        "clarification_history": [],
        "clarification_round": 0,
        "knowledge_matches": [],
        "source_chunks": [],
        "conflict": False,
        "question": "Windows 11でネットワークアダプターを再起動する方法は？",
    }
    decision = decide_route(state, settings)
    assert decision.route == "research"


def test_conflict_forces_human():
    settings = Settings()
    state = {
        "classification": _classification("PUBLIC_GENERAL", safe=True),
        "clarification_history": [],
        "clarification_round": 0,
        "knowledge_matches": [],
        "source_chunks": [{"priority": "authoritative", "source_file_id": 1, "content": "A"}],
        "conflict": True,
    }
    decision = decide_route(state, settings)
    assert decision.route == "human"


def test_count_consecutive_unknown():
    history = [
        {"answer": "わかる情報です"},
        {"answer": "わからない"},
        {"answer": "不明"},
    ]
    assert count_consecutive_unknown(history) == 2


def test_check_escalation_two_unknowns():
    history = [{"answer": "わからない"}, {"answer": "不明"}]
    reason = check_escalation(
        classification=_classification("INTERNAL"), clarification_history=history, last_answer="不明"
    )
    assert reason is not None


def test_pick_clarification_template_avoids_repeats():
    first = pick_clarification_template("windows", [])
    assert first is not None
    asked = [first[0]]
    second = pick_clarification_template("windows", asked)
    assert second is not None
    assert second[0] != first[0]


def test_is_vague_public_question():
    assert is_vague_public_question("ネットワークがおかしい", "network")
    assert not is_vague_public_question(
        "Windows 11でネットワークアダプターを再起動する方法は？", "windows"
    )
