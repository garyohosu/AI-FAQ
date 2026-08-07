from argparse import Namespace

from aifaq.graph import ReplyError
from aifaq.models import AnswerType, FAQAnswer
from aifaq import chat_session


class DummyConnection:
    def close(self):
        pass


class DummyRepositories:
    def thread_status(self, *, thread_id):  # pragma: no cover - /status用
        raise AssertionError(f"unexpected status lookup: {thread_id}")


def _args():
    return Namespace(thread_id=None, requester="山田", fake_provider=True)


def _final(thread_id: str, answer: str = "回答です") -> FAQAnswer:
    return FAQAnswer(
        answer=answer,
        answer_type=AnswerType.INTERNET_RESEARCH,
        confidence=0.8,
        thread_id=thread_id,
    )


def _clarification(thread_id: str) -> FAQAnswer:
    return FAQAnswer(
        answer="",
        answer_type=AnswerType.NEEDS_CLARIFICATION,
        confidence=0.0,
        thread_id=thread_id,
        clarification_round=1,
        question="どの部分が分かりませんか？",
        options=["最初の手順", "接続方法"],
        remaining_rounds=2,
    )


def test_chat_uses_new_thread_after_final_answer(monkeypatch, capsys):
    thread_ids = iter(["thread-1", "thread-2"])
    inputs = iter(
        [
            "プリンタが繋がりません",
            "良くわかりません",
            "最初に何をするのかわかりません",
            "/quit",
        ]
    )
    ask_calls = []
    reply_calls = []

    monkeypatch.setattr(chat_session, "_new_thread_id", lambda: next(thread_ids))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr(
        chat_session,
        "_open",
        lambda settings: (DummyConnection(), DummyRepositories()),
    )
    monkeypatch.setattr(chat_session, "_get_provider", lambda settings, use_fake: object())

    def fake_ask(repos, settings, provider, *, thread_id, question, requester):
        ask_calls.append((thread_id, question))
        if len(ask_calls) == 1:
            return _final(thread_id, "プリンタの接続手順です")
        return _clarification(thread_id)

    def fake_reply(repos, settings, provider, *, thread_id, answer_text):
        reply_calls.append((thread_id, answer_text))
        return _final(thread_id, "まずLANケーブルを接続してください")

    monkeypatch.setattr(chat_session, "run_ask", fake_ask)
    monkeypatch.setattr(chat_session, "run_reply", fake_reply)

    assert chat_session.cmd_chat(_args(), object()) == 0
    assert ask_calls == [
        ("thread-1", "プリンタが繋がりません"),
        ("thread-2", "良くわかりません"),
    ]
    assert reply_calls == [
        ("thread-2", "最初に何をするのかわかりません"),
    ]

    output = capsys.readouterr().out
    assert "新しい質問を開始します。thread_id: thread-2" in output
    assert "未回答の確認質問が見つかりません" not in output


def test_chat_recovers_when_clarification_state_is_lost(monkeypatch, capsys):
    thread_ids = iter(["thread-1", "thread-2"])
    inputs = iter(["最初の質問", "確認への回答", "/quit"])
    ask_calls = []

    monkeypatch.setattr(chat_session, "_new_thread_id", lambda: next(thread_ids))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr(
        chat_session,
        "_open",
        lambda settings: (DummyConnection(), DummyRepositories()),
    )
    monkeypatch.setattr(chat_session, "_get_provider", lambda settings, use_fake: object())

    def fake_ask(repos, settings, provider, *, thread_id, question, requester):
        ask_calls.append((thread_id, question))
        if len(ask_calls) == 1:
            return _clarification(thread_id)
        return _final(thread_id, "新しい質問として回答しました")

    def broken_reply(*args, **kwargs):
        raise ReplyError("確認状態が見つかりません")

    monkeypatch.setattr(chat_session, "run_ask", fake_ask)
    monkeypatch.setattr(chat_session, "run_reply", broken_reply)

    assert chat_session.cmd_chat(_args(), object()) == 0
    assert ask_calls == [
        ("thread-1", "最初の質問"),
        ("thread-2", "確認への回答"),
    ]

    output = capsys.readouterr().out
    assert "入力内容を新しい質問として続けます" in output
    assert "新thread_id: thread-2" in output
