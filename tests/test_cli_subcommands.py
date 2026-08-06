"""CLIの残りサブコマンドと異常系のテスト (instruction-005 §5.4)。"""

import json
import sqlite3

import pytest

from aifaq.cli import main


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
    (repo_root / "memory").mkdir()
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("AIFAQ_DB_PATH", str(repo_root / "data" / "aifaq.db"))
    return repo_root


def _make_knowledge(capsys) -> int:
    """人間回答経由で承認済みナレッジを1件作り、そのIDを返す。"""
    import io
    import sys as _sys

    main(["init"])
    capsys.readouterr()
    # 秘密情報を含む質問は確認質問を挟まず即座に人間引き継ぎになる
    main(["--fake-provider", "ask", "社内VPNのパスワードを教えてください", "--thread-id", "tk"])
    capsys.readouterr()
    main(["--json", "pending", "list"])
    pending_id = json.loads(capsys.readouterr().out)[0]["id"]

    original = _sys.stdin
    _sys.stdin = io.StringIO("情報システム部の申請フォームから申請してください\n")
    try:
        main(["pending", "answer", str(pending_id), "--approved-by", "hantani",
              "--category", "network", "--tags", "vpn,申請"])
    finally:
        _sys.stdin = original
    capsys.readouterr()

    main(["--json", "knowledge", "list"])
    return json.loads(capsys.readouterr().out)[0]["id"]


# ---------------------------------------------------------------------------
# knowledge サブコマンド
# ---------------------------------------------------------------------------


def test_knowledge_show(cli_env, capsys):
    kid = _make_knowledge(capsys)
    assert main(["knowledge", "show", str(kid)]) == 0
    assert "申請" in capsys.readouterr().out


def test_knowledge_show_invalid_id(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["knowledge", "show", "999"]) == 1
    assert "見つかりません" in capsys.readouterr().out


def test_knowledge_retire(cli_env, capsys):
    kid = _make_knowledge(capsys)
    assert main(["knowledge", "retire", str(kid), "--by", "hantani"]) == 0
    capsys.readouterr()

    main(["--json", "knowledge", "list", "--status", "RETIRED"])
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_knowledge_status(cli_env, capsys):
    _make_knowledge(capsys)
    assert main(["--json", "knowledge", "status"]) == 0
    # 取り込み資料の一覧(この環境では0件)
    assert json.loads(capsys.readouterr().out) == []


def test_knowledge_rebuild_index(cli_env, capsys):
    _make_knowledge(capsys)
    assert main(["knowledge", "rebuild-index"]) == 0
    assert "索引" in capsys.readouterr().out


def test_knowledge_search(cli_env, capsys):
    _make_knowledge(capsys)
    assert main(["--json", "knowledge", "search", "VPN"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["knowledge"][0]["knowledge_id"] == 1
    assert out["sources"] == []


def test_knowledge_scan(cli_env, capsys):
    (cli_env / "knowledge" / "public" / "a.md").write_text("# a\n\nb\n", encoding="utf-8")
    main(["init"])
    capsys.readouterr()
    assert main(["--json", "knowledge", "scan"]) == 0
    capsys.readouterr()


# ---------------------------------------------------------------------------
# memory / history
# ---------------------------------------------------------------------------


def test_memory_validate_show_sync(cli_env, capsys):
    main(["init"])
    capsys.readouterr()

    assert main(["--json", "memory", "validate"]) == 0
    capsys.readouterr()

    assert main(["memory", "show"]) == 0
    assert "Source Map" in capsys.readouterr().out

    assert main(["memory", "sync"]) == 0
    assert "同期" in capsys.readouterr().out


def test_memory_show_missing_index(cli_env, capsys):
    (cli_env / "MEMORY.md").unlink()
    main(["init"])
    capsys.readouterr()
    assert main(["memory", "show"]) == 1
    assert "見つかりません" in capsys.readouterr().out


def test_history(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    main(["--fake-provider", "ask", "Windows 11の公式手順は？", "--thread-id", "th"])
    capsys.readouterr()

    assert main(["--json", "history", "th"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["history"]


# ---------------------------------------------------------------------------
# 異常系
# ---------------------------------------------------------------------------


def test_pending_show_invalid_id(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["pending", "show", "999"]) == 1
    assert "見つかりません" in capsys.readouterr().out


def test_reply_to_unknown_thread(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["--fake-provider", "reply", "no-such-thread", "答え"]) == 2
    assert "エラー" in capsys.readouterr().out


def test_database_locked_gives_actionable_message(cli_env, capsys, monkeypatch):
    """`database is locked` をそのまま出さず、対処方法を示す。"""
    main(["init"])
    capsys.readouterr()

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("aifaq.cli._open", locked)
    code = main(["knowledge", "list"])
    assert code == 3
    err = capsys.readouterr().err
    assert "ロックされています" in err
    assert "再実行" in err


def test_other_operational_errors_are_not_swallowed(cli_env, capsys, monkeypatch):
    main(["init"])
    capsys.readouterr()

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: nope")

    monkeypatch.setattr("aifaq.cli._open", boom)
    with pytest.raises(sqlite3.OperationalError):
        main(["knowledge", "list"])


def test_invalid_provider_env_is_rejected_at_startup(cli_env, capsys, monkeypatch):
    monkeypatch.setenv("AIFAQ_RESEARCH_PROVIDER", "not-a-provider")
    assert main(["knowledge", "list"]) == 2
    assert "設定エラー" in capsys.readouterr().err
