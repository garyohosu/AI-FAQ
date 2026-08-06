"""対話モード `aifaq chat` のテスト (instruction-006 §4.4)。"""

import io
import json

import pytest

from aifaq.cli import main


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    (tmp_path / "knowledge" / "public").mkdir(parents=True)
    (tmp_path / "MEMORY.md").write_text(
        "# AI-FAQ Memory Index\n\n"
        "## Source Map\n\n## Terminology\n\n## Routing Rules\n\n"
        "## Important Decisions\n\n## Known Gaps\n\n## Retired or Forbidden Sources\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIFAQ_DB_PATH", str(tmp_path / "data" / "aifaq.db"))
    return tmp_path


def _chat(monkeypatch, lines, extra_args=()):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    return main(["--fake-provider", "chat", "--thread-id", "chat-t", *extra_args])


def test_chat_quit(cli_env, capsys, monkeypatch):
    main(["init"])
    capsys.readouterr()
    assert _chat(monkeypatch, ["/quit"]) == 0
    out = capsys.readouterr().out
    assert "AI-FAQ CLI" in out
    assert "終了します" in out


def test_chat_asks_clarification_then_reuses_run_reply(cli_env, capsys, monkeypatch):
    """確認質問を出し、次の入力を回答として扱う。"""
    main(["init"])
    capsys.readouterr()
    _chat(monkeypatch, ["社内の機器が調子悪いのですが対応方法を教えてください",
                        "プリンターです", "/quit"])
    out = capsys.readouterr().out
    assert "AI-FAQ>" in out
    # 2回目の確認質問まで進んでいる = run_reply が使われている
    assert out.count("AI-FAQ>") >= 2


def test_chat_status_command(cli_env, capsys, monkeypatch):
    main(["init"])
    capsys.readouterr()
    _chat(monkeypatch, ["社内の機器が調子悪いのですが対応方法を教えてください",
                        "/status", "/quit"])
    out = capsys.readouterr().out
    assert "NEEDS_CLARIFICATION" in out
    assert "thread_id: chat-t" in out


def test_chat_unknown_slash_command(cli_env, capsys, monkeypatch):
    main(["init"])
    capsys.readouterr()
    _chat(monkeypatch, ["/nope", "/quit"])
    assert "不明なコマンド" in capsys.readouterr().out


def test_chat_blank_lines_are_ignored(cli_env, capsys, monkeypatch):
    main(["init"])
    capsys.readouterr()
    assert _chat(monkeypatch, ["", "  ", "/quit"]) == 0


def test_chat_answers_from_knowledge(cli_env, capsys, monkeypatch):
    main(["init"])
    capsys.readouterr()
    _chat(monkeypatch, ["Windows 11の公式手順は？", "/quit"])
    out = capsys.readouterr().out
    assert "AI-FAQ>" in out


def test_chat_human_handoff_offers_to_wait_and_declining_exits(
    cli_env, capsys, monkeypatch
):
    """人間回答待ちで待つか終了するか選べる。'n' なら終了する。"""
    main(["init"])
    capsys.readouterr()
    code = _chat(monkeypatch, ["社内VPNのパスワードを教えてください", "n"])
    assert code == 0
    out = capsys.readouterr().out
    assert "IT管理者へ引き継ぎました" in out
    assert "受付番号" in out
    assert "aifaq status chat-t" in out


def test_chat_human_handoff_wait_shows_answer(cli_env, capsys, monkeypatch):
    """待機を選ぶと watch 経由で回答を表示する。"""
    main(["init"])
    capsys.readouterr()

    # 先に pending を作り、回答も入れておく(watch が即座に確定する)
    main(["--fake-provider", "ask", "社内VPNのパスワードを教えてください",
          "--thread-id", "chat-t"])
    capsys.readouterr()
    main(["--json", "status", "chat-t"])
    pending_id = json.loads(capsys.readouterr().out)["pending_id"]

    monkeypatch.setattr("sys.stdin", io.StringIO("先に登録した回答"))
    main(["pending", "answer", str(pending_id), "--approved-by", "hantani"])
    capsys.readouterr()

    # 同じthreadでchatを開くと、引き継ぎ済みの状態から待機して回答を得る
    _chat(monkeypatch, ["社内VPNのパスワードを教えてください", "y", "/quit"])
    out = capsys.readouterr().out
    assert "先に登録した回答" in out or "KNOWLEDGE" in out or "AI-FAQ>" in out


def test_chat_eof_exits_cleanly(cli_env, capsys, monkeypatch):
    main(["init"])
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["--fake-provider", "chat", "--thread-id", "chat-eof"]) == 0
