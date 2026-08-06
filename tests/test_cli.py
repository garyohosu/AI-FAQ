import json

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
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("AIFAQ_DB_PATH", str(repo_root / "data" / "aifaq.db"))
    return repo_root


def test_doctor_json(cli_env, capsys):
    code = main(["--json", "doctor"])
    assert code in (0, 1)  # gemini CLI may or may not be present in CI
    out = json.loads(capsys.readouterr().out)
    names = {c["name"] for c in out}
    assert "python_version" in names
    assert "fts5_available" in names


def test_init(cli_env, capsys):
    code = main(["init"])
    assert code == 0
    assert "初期化完了" in capsys.readouterr().out


def test_ask_json_matches_faq_answer_schema(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    code = main(
        ["--json", "--fake-provider", "ask", "Windows 11でネットワークアダプターを再起動する方法は？",
         "--thread-id", "t1"]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["answer_type"] == "INTERNET_RESEARCH"
    assert out["thread_id"] == "t1"


def test_pending_answer_workflow_via_cli(cli_env, capsys, monkeypatch):
    main(["init"])
    capsys.readouterr()
    main(["--fake-provider", "ask", "社内VPNのパスワードを教えてください", "--thread-id", "t2"])
    capsys.readouterr()

    code = main(["--json", "pending", "list"])
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    pending_id = rows[0]["id"]

    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("情報システム部へ連絡してください\n"))
    code = main(
        ["pending", "answer", str(pending_id), "--approved-by", "hantani", "--category", "network"]
    )
    assert code == 0
    assert "KB-" in capsys.readouterr().out

    code = main(["--json", "knowledge", "list"])
    arts = json.loads(capsys.readouterr().out)
    assert len(arts) == 1
    assert arts[0]["status"] == "APPROVED"


def test_knowledge_import_and_search_via_cli(cli_env, capsys):
    (cli_env / "knowledge" / "public" / "sample.csv").write_text(
        "name,desc\nWi-Fi,無線LAN\n", encoding="utf-8"
    )
    main(["init"])
    capsys.readouterr()
    code = main(["knowledge", "import"])
    assert code == 0
    capsys.readouterr()

    code = main(["--json", "knowledge", "search", "Wi-Fi"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["sources"]) >= 1


def test_memory_validate_via_cli(cli_env, capsys):
    code = main(["--json", "memory", "validate"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
