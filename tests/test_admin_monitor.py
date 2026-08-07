from argparse import Namespace
from types import SimpleNamespace

from aifaq import admin_monitor


def test_monitor_waits_for_new_question_and_keeps_running(monkeypatch, capsys):
    row = {
        "id": 2,
        "thread_id": "thread-2",
        "original_question": "社内Wi-Fiにつながりません",
    }
    rows = iter([[], [row]])
    answers = iter(["情報システム担当へ電話してください。", "y"])
    recorded = []

    def fake_fetch_open_rows(settings):
        try:
            return next(rows)
        except StopIteration as exc:
            raise KeyboardInterrupt from exc

    monkeypatch.setattr(admin_monitor, "_fetch_open_rows", fake_fetch_open_rows)
    monkeypatch.setattr(admin_monitor, "_show_question", lambda settings, selected: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(admin_monitor.time, "sleep", lambda seconds: None)

    def fake_answer_pending(settings, *, pending_id, answer, approved_by):
        recorded.append((pending_id, answer, approved_by))
        return 10, 0

    monkeypatch.setattr(admin_monitor, "_answer_pending", fake_answer_pending)

    args = Namespace(approved_by="hantani", interval=0.5)
    settings = SimpleNamespace(
        watch_interval_seconds=2.0,
        watch_min_interval_seconds=0.5,
    )

    assert admin_monitor.monitor(args, settings) == 0
    assert recorded == [
        (2, "情報システム担当へ電話してください。", "hantani")
    ]

    output = capsys.readouterr().out
    assert "新しい質問を待っています" in output
    assert "新しい質問が届きました" in output
    assert "監視を続けます" in output
    assert "IT管理者モニターを終了します" in output


def test_monitor_can_quit_from_answer_prompt(monkeypatch, capsys):
    row = {
        "id": 3,
        "thread_id": "thread-3",
        "original_question": "プリンタが使えません",
    }

    monkeypatch.setattr(admin_monitor, "_fetch_open_rows", lambda settings: [row])
    monkeypatch.setattr(admin_monitor, "_show_question", lambda settings, selected: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "/quit")

    args = Namespace(approved_by="hantani", interval=1.0)
    settings = SimpleNamespace(
        watch_interval_seconds=2.0,
        watch_min_interval_seconds=0.5,
    )

    assert admin_monitor.monitor(args, settings) == 0
    assert "IT管理者モニターを終了します" in capsys.readouterr().out
