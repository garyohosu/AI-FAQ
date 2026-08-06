import pytest

from aifaq import db as db_module
from aifaq.graph import ReplyError, run_ask, run_reply
from aifaq.models import KnowledgeSourceType
from aifaq.providers.base import ProviderTimeoutError
from aifaq.providers.fake import FakeResearchProvider
from aifaq.repositories import Repositories


def test_sufficient_knowledge_answers_directly_without_provider(repos, settings):
    repos.knowledge.create(
        canonical_question="社内Wi-Fiのパスワードを忘れた場合は？",
        answer="情報システム部へ連絡してください。", category="network", tags=[],
        source_type=KnowledgeSourceType.HUMAN,
        approved_by="hantani",
    )
    provider = FakeResearchProvider()
    ans = run_ask(
        repos, settings, provider, thread_id="t-kb",
        question="社内Wi-Fiのパスワードを忘れた場合は？", requester="u",
    )
    assert ans.answer_type.value == "KNOWLEDGE"
    assert provider.call_count == 0


def test_secret_question_never_reaches_provider_and_becomes_pending(repos, settings):
    provider = FakeResearchProvider()
    ans = run_ask(
        repos, settings, provider, thread_id="t-secret",
        question="社内VPNのパスワードを教えてください", requester="u",
    )
    assert ans.answer_type.value == "PENDING_HUMAN"
    assert provider.call_count == 0
    assert "秘密情報" in (ans.notice or "")


def test_internal_question_never_reaches_provider(repos, settings):
    provider = FakeResearchProvider()
    ans = run_ask(
        repos, settings, provider, thread_id="t-internal",
        question="第2工場の検査PCを交換するときの申請先は？", requester="u",
    )
    # 内部質問はまず確認質問に進む(社内固有情報のためprovider未使用)
    assert ans.answer_type.value in ("NEEDS_CLARIFICATION", "PENDING_HUMAN")
    assert provider.call_count == 0


def test_public_vague_question_clarifies_then_researches(repos, settings):
    provider = FakeResearchProvider(answer="回答です", confidence=0.9)
    ans1 = run_ask(
        repos, settings, provider, thread_id="t-pub",
        question="ネットワークがおかしい", requester="u",
    )
    assert ans1.answer_type.value == "NEEDS_CLARIFICATION"
    assert provider.call_count == 0

    ans2 = run_reply(repos, settings, provider, thread_id="t-pub", answer_text="無線(Wi-Fi)")
    assert ans2.answer_type.value == "INTERNET_RESEARCH"
    assert provider.call_count == 1


def test_public_specific_question_goes_straight_to_research(repos, settings):
    provider = FakeResearchProvider(answer="再起動手順です", confidence=0.9)
    ans = run_ask(
        repos, settings, provider, thread_id="t-pub2",
        question="Windows 11でネットワークアダプターを再起動する方法は？", requester="u",
    )
    assert ans.answer_type.value == "INTERNET_RESEARCH"
    assert provider.call_count == 1


def test_research_answer_never_auto_becomes_knowledge(repos, settings):
    provider = FakeResearchProvider(answer="回答", confidence=0.95)
    run_ask(
        repos, settings, provider, thread_id="t-noauto",
        question="Windows 11でネットワークアダプターを再起動する方法は？", requester="u",
    )
    assert repos.knowledge.list() == []


def test_low_confidence_research_escalates_to_human(repos, settings):
    provider = FakeResearchProvider(answer="不明", confidence=0.1)
    ans = run_ask(
        repos, settings, provider, thread_id="t-lowconf",
        question="Windows 11でネットワークアダプターを再起動する方法は？", requester="u",
    )
    assert ans.answer_type.value == "PENDING_HUMAN"


def test_provider_error_escalates_to_human(repos, settings):
    provider = FakeResearchProvider(raise_error=ProviderTimeoutError("timeout"))
    ans = run_ask(
        repos, settings, provider, thread_id="t-timeout",
        question="Windows 11でネットワークアダプターを再起動する方法は？", requester="u",
    )
    assert ans.answer_type.value == "PENDING_HUMAN"


def test_three_rounds_max_no_fourth_question(repos, settings):
    provider = FakeResearchProvider()
    tid = "t-3round"
    a = run_ask(repos, settings, provider, thread_id=tid, question="弊社windowsが調子悪い", requester="u")
    assert a.answer_type.value == "NEEDS_CLARIFICATION"
    assert a.clarification_round == 1
    a = run_reply(repos, settings, provider, thread_id=tid, answer_text="Windows 11")
    assert a.clarification_round == 2
    a = run_reply(repos, settings, provider, thread_id=tid, answer_text="サインイン後")
    assert a.clarification_round == 3
    a = run_reply(repos, settings, provider, thread_id=tid, answer_text="1台だけ")
    assert a.answer_type.value == "PENDING_HUMAN"
    assert repos.clarifications.count_rounds(tid) == 3

    with pytest.raises(ReplyError):
        run_reply(repos, settings, provider, thread_id=tid, answer_text="もう一声")


def test_two_consecutive_unknown_escalates_before_round_limit(repos, settings):
    provider = FakeResearchProvider()
    tid = "t-unknown"
    run_ask(repos, settings, provider, thread_id=tid, question="弊社windowsが調子悪い", requester="u")
    run_reply(repos, settings, provider, thread_id=tid, answer_text="わからない")
    a = run_reply(repos, settings, provider, thread_id=tid, answer_text="わからない")
    assert a.answer_type.value == "PENDING_HUMAN"
    assert repos.clarifications.count_rounds(tid) == 2


def test_does_not_reask_already_answered_information(repos, settings):
    provider = FakeResearchProvider()
    tid = "t-noreask"
    a = run_ask(repos, settings, provider, thread_id=tid, question="弊社windowsが調子悪い", requester="u")
    first_question = a.question
    a = run_reply(repos, settings, provider, thread_id=tid, answer_text="Windows 11")
    assert a.question != first_question


def test_reply_without_open_turn_raises_clear_error(repos, settings):
    provider = FakeResearchProvider()
    with pytest.raises(ReplyError):
        run_reply(repos, settings, provider, thread_id="never-asked", answer_text="x")


def test_thread_resumes_across_fresh_connections(settings, tmp_path):
    conn1 = db_module.connect(settings)
    db_module.init_db(conn1)
    repos1 = Repositories.build(conn1)
    provider1 = FakeResearchProvider()
    a1 = run_ask(
        repos1, settings, provider1, thread_id="t-restart",
        question="弊社windowsが調子悪い", requester="u",
    )
    assert a1.answer_type.value == "NEEDS_CLARIFICATION"
    conn1.close()

    conn2 = db_module.connect(settings)
    db_module.init_db(conn2)
    repos2 = Repositories.build(conn2)
    provider2 = FakeResearchProvider()
    a2 = run_reply(repos2, settings, provider2, thread_id="t-restart", answer_text="Windows 11")
    assert a2.answer_type.value == "NEEDS_CLARIFICATION"
    assert a2.clarification_round == 2
    conn2.close()
