import json

import pytest

from aifaq.admin_cli import main as admin_main
from aifaq.cli import main as aifaq_main


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
    monkeypatch.setenv("USERNAME", "hantani")
    return repo_root


def _create_pending(capsys, thread_id="admin-test") -> int:
    code = aifaq_main(
        [
            "--fake-provider",
            "ask",
            "社内VPNのパスワードを教えてください",
            "--thread-id",
            thread_id,
        ]
    )
    assert code == 0
    capsys.readouterr()

    code = aifaq_main(["--json", "pending", "list", "--status", "OPEN"])
    assert code == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    return rows[0]["id"]


def test_admin_interactive_answers_single_pending(cli_env, capsys, monkeypatch):
    pending_id = _create_pending(capsys)
    responses = iter(["情報システム担当へ電話してください。", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))

    code = admin_main([])
    assert code == 0
    output = capsys.readouterr().out
    assert f"受付番号 #{pending_id} を選択しました" in output
    assert "回答を登録しました" in output
    assert "KB-" in output

    code = aifaq_main(["--json", "pending", "list"])
    assert code == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["status"] == "ANSWERED"


def test_admin_one_command_answer(cli_env, capsys):
    pending_id = _create_pending(capsys, thread_id="admin-direct")

    code = admin_main([str(pending_id), "わたしに電話で連絡してください。"]) 
    assert code == 0
    output = capsys.readouterr().out
    assert "質問者へ回答済みです" in output

    code = aifaq_main(["--json", "knowledge", "list"])
    assert code == 0
    knowledge = json.loads(capsys.readouterr().out)
    assert len(knowledge) == 1
    assert knowledge[0]["status"] == "APPROVED"


def test_admin_list_when_empty(cli_env, capsys):
    code = admin_main(["--list"])
    assert code == 0
    assert "未回答の質問はありません" in capsys.readouterr().out
