"""人間回答ループと status / watch のテスト (instruction-006 §9)。

「IT管理者が回答を登録するだけで終わらず、元の質問者が同じ質問スレッドから
その回答を受け取れる」ことを検証する。
"""

import json
import sqlite3

import pytest

from aifaq import db as db_module
from aifaq.cli import (
    INTERRUPTED_EXIT_CODE,
    NOT_FOUND_EXIT_CODE,
    WATCH_TIMEOUT_EXIT_CODE,
    main,
)
from aifaq.config import Settings
from aifaq.models import ThreadState
from aifaq.repositories import AlreadyAnsweredError, Repositories


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    repo_root = tmp_path
    (repo_root / "knowledge" / "public").mkdir(parents=True)
    (repo_root / "MEMORY.md").write_text(
        "# AI-FAQ Memory Index\n\n"
        "## Source Map\n\n## Terminology\n\n## Routing Rules\n\n"
        "## Important Decisions\n\n## Known Gaps\n\n## Retired or Forbidden Sources\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("AIFAQ_DB_PATH", str(repo_root / "data" / "aifaq.db"))
    return repo_root


def _handoff(capsys, thread_id="t-human", question="社内VPNのパスワードを教えてください"):
    """人間引き継ぎを1件作り、pending_id を返す。"""
    main(["init"])
    capsys.readouterr()
    main(["--fake-provider", "ask", question, "--thread-id", thread_id])
    capsys.readouterr()
    main(["--json", "status", thread_id])
    return json.loads(capsys.readouterr().out)["pending_id"]


def _answer(pending_id, text="情報システム部の申請フォームから依頼してください", **kw):
    import io
    import sys as _sys

    original = _sys.stdin
    _sys.stdin = io.StringIO(text)
    try:
        args = ["pending", "answer", str(pending_id), "--approved-by", "hantani"]
        for key, value in kw.items():
            args += [f"--{key.replace('_', '-')}", value]
        return main(args)
    finally:
        _sys.stdin = original


# ---------------------------------------------------------------------------
# 9.1 人間回答ループ
# ---------------------------------------------------------------------------


def test_full_human_answer_loop(cli_env, capsys):
    """質問 → 引き継ぎ → 回答 → 元threadで受領 → 次回再利用。"""
    pending_id = _handoff(capsys)
    assert pending_id is not None

    # 回答前: 回答待ち
    main(["--json", "status", "t-human"])
    before = json.loads(capsys.readouterr().out)
    assert before["state"] == "PENDING_HUMAN"
    assert before["answer"] is None

    assert _answer(pending_id, category="network") == 0
    capsys.readouterr()

    # 元のthreadから人間回答が返る
    main(["--json", "status", "t-human"])
    after = json.loads(capsys.readouterr().out)
    assert after["state"] == "ANSWERED"
    assert after["answer"] == "情報システム部の申請フォームから依頼してください"
    assert after["answered_by"] == "hantani"
    assert after["answered_at"]
    assert after["answer_type"] == "HUMAN"
    assert after["knowledge_id"]

    # 承認済み知識として保存され、次回の類似質問で再利用される
    main(["--json", "--fake-provider", "ask", "社内VPNのパスワードを教えてください",
          "--thread-id", "t-reuse"])
    reuse = json.loads(capsys.readouterr().out)
    assert reuse["answer_type"] == "KNOWLEDGE"
    assert "情報システム部" in reuse["answer"]


def test_answer_is_saved_on_the_pending_row(cli_env, capsys):
    """回答本文が元のpendingへ保存される(承認済み知識だけでなく)。"""
    pending_id = _handoff(capsys)
    _answer(pending_id)
    capsys.readouterr()

    conn = db_module.connect(Settings.from_env())
    row = conn.execute(
        "SELECT * FROM pending_questions WHERE id=?", (pending_id,)
    ).fetchone()
    conn.close()
    assert row["status"] == "ANSWERED"
    assert row["answer_text"] == "情報システム部の申請フォームから依頼してください"
    assert row["answered_by"] == "hantani"
    assert row["answered_at"]
    assert row["updated_at"]
    assert row["answer_type"] == "HUMAN"


def test_human_answer_is_recorded_in_history(cli_env, capsys):
    pending_id = _handoff(capsys)
    _answer(pending_id)
    capsys.readouterr()

    main(["--json", "history", "t-human"])
    history = json.loads(capsys.readouterr().out)["history"]
    types = [h["answer_type"] for h in history]
    assert "PENDING_HUMAN" in types
    assert "HUMAN_ANSWER" in types


def test_double_answer_is_rejected_and_does_not_overwrite(cli_env, capsys):
    pending_id = _handoff(capsys)
    assert _answer(pending_id, text="最初の回答") == 0
    capsys.readouterr()

    assert _answer(pending_id, text="上書きしようとした回答") == 2
    assert "既に" in capsys.readouterr().err

    main(["--json", "status", "t-human"])
    assert json.loads(capsys.readouterr().out)["answer"] == "最初の回答"


def test_delivery_is_separate_from_answer(cli_env, capsys):
    """受領しても状態は ANSWERED のまま。回答は消えない (§7)。"""
    pending_id = _handoff(capsys)
    _answer(pending_id)
    capsys.readouterr()

    main(["--json", "status", "t-human"])
    first = json.loads(capsys.readouterr().out)
    assert first["state"] == "ANSWERED"
    assert first["delivery_status"] == "DELIVERED"
    assert first["delivered_at"]

    # 2回目も同じ回答が取得でき、初回受領時刻は変わらない
    main(["--json", "status", "t-human"])
    second = json.loads(capsys.readouterr().out)
    assert second["state"] == "ANSWERED"
    assert second["answer"] == first["answer"]
    assert second["delivered_at"] == first["delivered_at"]


# ---------------------------------------------------------------------------
# 9.3 status
# ---------------------------------------------------------------------------


def test_status_needs_clarification(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    main(["--fake-provider", "ask", "社内の機器が調子悪いのですが対応方法を教えてください",
          "--thread-id", "t-clar"])
    capsys.readouterr()

    main(["--json", "status", "t-clar"])
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "NEEDS_CLARIFICATION"
    assert data["next_question"]
    assert data["clarifications"]


def test_status_completed_without_human(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    main(["--fake-provider", "ask", "Windows 11の公式手順は？", "--thread-id", "t-done"])
    capsys.readouterr()

    main(["--json", "status", "t-done"])
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "COMPLETED"


def test_status_cancelled(cli_env, capsys):
    pending_id = _handoff(capsys)
    conn = db_module.connect(Settings.from_env())
    Repositories.build(conn).pending.cancel(pending_id)
    conn.close()

    main(["--json", "status", "t-human"])
    assert json.loads(capsys.readouterr().out)["state"] == "CANCELLED"


def test_status_by_pending_id(cli_env, capsys):
    pending_id = _handoff(capsys)
    main(["--json", "status", "--pending-id", str(pending_id)])
    data = json.loads(capsys.readouterr().out)
    assert data["thread_id"] == "t-human"
    assert data["pending_id"] == pending_id


def test_status_thread_not_found(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["status", "no-such-thread"]) == NOT_FOUND_EXIT_CODE
    assert "見つかりません" in capsys.readouterr().out


def test_status_pending_id_not_found(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["status", "--pending-id", "999"]) == NOT_FOUND_EXIT_CODE


def test_status_requires_an_argument(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["status"]) == 2
    assert "指定してください" in capsys.readouterr().err


def test_status_shows_clarification_history_and_answer(cli_env, capsys):
    """確認質問履歴と人間回答が同時に表示される。"""
    main(["init"])
    capsys.readouterr()
    main(["--fake-provider", "ask", "社内の機器が調子悪いのですが対応方法を教えてください",
          "--thread-id", "t-mix"])
    capsys.readouterr()
    main(["--fake-provider", "reply", "t-mix", "プリンターです"])
    capsys.readouterr()
    main(["--fake-provider", "reply", "t-mix", "電源は入ります"])
    capsys.readouterr()
    main(["--fake-provider", "reply", "t-mix", "会議室のものです"])
    capsys.readouterr()

    main(["--json", "status", "t-mix"])
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "PENDING_HUMAN"
    assert len(data["clarifications"]) == 3

    _answer(data["pending_id"], text="設置担当へ連絡してください")
    capsys.readouterr()

    main(["status", "t-mix"])
    out = capsys.readouterr().out
    assert "設置担当へ連絡してください" in out
    assert "確認質問履歴" in out
    assert "回答者: hantani" in out


# ---------------------------------------------------------------------------
# 9.4 watch
# ---------------------------------------------------------------------------


def test_watch_returns_immediately_when_already_answered(cli_env, capsys):
    pending_id = _handoff(capsys)
    _answer(pending_id, text="すでに回答済みです")
    capsys.readouterr()

    assert main(["watch", "t-human", "--interval", "0.5", "--timeout", "5"]) == 0
    assert "すでに回答済みです" in capsys.readouterr().out


def test_watch_times_out_while_pending(cli_env, capsys):
    _handoff(capsys)
    code = main(["watch", "t-human", "--interval", "0.5", "--timeout", "1"])
    assert code == WATCH_TIMEOUT_EXIT_CODE
    out = capsys.readouterr().out
    assert "タイムアウト" in out


def test_watch_rejects_too_small_interval(cli_env, capsys):
    _handoff(capsys)
    assert main(["watch", "t-human", "--interval", "0.1"]) == 2
    assert "0.5 秒以上" in capsys.readouterr().err


def test_watch_not_found(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["watch", "no-such-thread", "--interval", "0.5"]) == NOT_FOUND_EXIT_CODE


def test_watch_requires_an_argument(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["watch"]) == 2


def test_watch_json_output_is_parseable(cli_env, capsys):
    """進捗表示は stderr。--json の標準出力は壊れない (§4.2)。"""
    pending_id = _handoff(capsys)
    _answer(pending_id, text="JSON確認用の回答")
    capsys.readouterr()

    assert main(["--json", "watch", "t-human", "--interval", "0.5", "--timeout", "5"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["state"] == "ANSWERED"
    assert data["answer"] == "JSON確認用の回答"


def test_watch_timeout_json_output_is_parseable(cli_env, capsys):
    _handoff(capsys)
    code = main(["--json", "watch", "t-human", "--interval", "0.5", "--timeout", "1"])
    assert code == WATCH_TIMEOUT_EXIT_CODE
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "PENDING_HUMAN"


def test_watch_handles_keyboard_interrupt(cli_env, capsys, monkeypatch):
    """Ctrl+C は状態を壊さずに終了する。"""
    _handoff(capsys)

    def fake_sleep(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("aifaq.cli.time.sleep", fake_sleep)
    assert main(["watch", "t-human", "--interval", "0.5"]) == INTERRUPTED_EXIT_CODE
    assert "中止" in capsys.readouterr().err

    # 状態は保たれている
    main(["--json", "status", "t-human"])
    assert json.loads(capsys.readouterr().out)["state"] == "PENDING_HUMAN"


# ---------------------------------------------------------------------------
# 9.2 2接続・同時利用
# ---------------------------------------------------------------------------


def test_two_connections_see_the_same_answer(cli_env, capsys):
    """別接続(質問者側)から、書き込んだ回答が読める。"""
    pending_id = _handoff(capsys)

    settings = Settings.from_env()
    writer = db_module.connect(settings)
    reader = db_module.connect(settings)
    try:
        Repositories.build(writer).pending.answer(
            pending_id,
            answer_text="別接続から書き込んだ回答",
            category="other", tags=[], variants=[], approved_by="hantani",
        )
        status = Repositories.build(reader).thread_status(thread_id="t-human")
        assert status.state == ThreadState.ANSWERED
        assert status.answer == "別接続から書き込んだ回答"
    finally:
        writer.close()
        reader.close()


def test_second_connection_cannot_double_answer(cli_env, capsys):
    """2接続から同じpendingへ回答しても、後勝ちで上書きされない。"""
    pending_id = _handoff(capsys)

    settings = Settings.from_env()
    conn_a = db_module.connect(settings)
    conn_b = db_module.connect(settings)
    try:
        Repositories.build(conn_a).pending.answer(
            pending_id, answer_text="Aの回答",
            category="other", tags=[], variants=[], approved_by="a",
        )
        with pytest.raises(AlreadyAnsweredError):
            Repositories.build(conn_b).pending.answer(
                pending_id, answer_text="Bの回答",
                category="other", tags=[], variants=[], approved_by="b",
            )
        status = Repositories.build(conn_b).thread_status(thread_id="t-human")
        assert status.answer == "Aの回答"
    finally:
        conn_a.close()
        conn_b.close()


def test_wal_and_busy_timeout_are_configured(cli_env):
    settings = Settings.from_env()
    conn = db_module.connect(settings)
    db_module.init_db(conn)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == settings.busy_timeout_ms
    finally:
        conn.close()


def test_reader_is_not_blocked_by_an_open_write_transaction(cli_env, capsys):
    """WALにより、書き込み中でも質問者側の status が読める (§6)。"""
    pending_id = _handoff(capsys)
    settings = Settings.from_env()
    writer = db_module.connect(settings)
    reader = db_module.connect(settings)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "UPDATE pending_questions SET answer_text='書きかけ' WHERE id=?",
            (pending_id,),
        )
        # 書き込みトランザクションが開いたままでも読める
        status = Repositories.build(reader).thread_status(thread_id="t-human")
        assert status.state == ThreadState.PENDING_HUMAN
        writer.rollback()
    finally:
        writer.close()
        reader.close()


def test_lock_retry_gives_up_with_a_clear_error():
    """無限再試行せず、利用者向けの説明を出す (§6)。"""
    calls = {"n": 0}

    def always_locked():
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(db_module.DatabaseBusyError, match="ロックされています"):
        db_module.with_lock_retry(always_locked, attempts=3, base_delay=0.001)
    assert calls["n"] == 3


def test_lock_retry_succeeds_after_transient_lock():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert db_module.with_lock_retry(flaky, attempts=5, base_delay=0.001) == "ok"


def test_lock_retry_does_not_swallow_other_errors():
    def broken():
        raise sqlite3.OperationalError("no such table: nope")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        db_module.with_lock_retry(broken, attempts=3, base_delay=0.001)


# ---------------------------------------------------------------------------
# 回答本文の検証 (§8)
# ---------------------------------------------------------------------------


def test_answer_rejects_control_characters(cli_env, capsys):
    pending_id = _handoff(capsys)
    assert _answer(pending_id, text="不正な\x1b[31m制御文字") == 2
    assert "制御文字" in capsys.readouterr().err


def test_answer_allows_newlines_and_tabs(cli_env, capsys):
    pending_id = _handoff(capsys)
    assert _answer(pending_id, text="1行目\n2行目\tタブ") == 0


def test_answer_rejects_excessive_length(cli_env, capsys, monkeypatch):
    monkeypatch.setenv("AIFAQ_MAX_ANSWER_CHARS", "50")
    pending_id = _handoff(capsys)
    assert _answer(pending_id, text="あ" * 100) == 2
    assert "長すぎます" in capsys.readouterr().err


def test_answer_strips_leading_bom_from_stdin(cli_env, capsys):
    """PowerShell のパイプが付ける BOM を回答本文へ混入させない。"""
    pending_id = _handoff(capsys)
    assert _answer(pending_id, text="﻿BOM付きの回答") == 0
    capsys.readouterr()

    main(["--json", "status", "t-human"])
    assert json.loads(capsys.readouterr().out)["answer"] == "BOM付きの回答"


def test_answer_to_unknown_pending_id(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert _answer(999) == 1
    assert "not found" in capsys.readouterr().err
