"""source_import_runs の実書き込み (instruction-005 §5.2) のテスト。"""

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


def _import_runs(repo_root):
    from aifaq import db as db_module
    from aifaq.config import Settings
    from aifaq.repositories import Repositories

    conn = db_module.connect(Settings.from_env())
    db_module.init_db(conn)
    rows = [dict(r) for r in Repositories.build(conn).import_runs.list()]
    conn.close()
    return rows


def test_import_run_is_recorded_with_counts(cli_env, capsys):
    (cli_env / "knowledge" / "public" / "faq.md").write_text(
        "# 手順\n\nネットワークアダプターの再起動手順です。\n", encoding="utf-8"
    )
    main(["init"])
    capsys.readouterr()

    code = main(["--json", "knowledge", "import", "--actor", "hantani"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["added"] == 1
    assert payload["counts"]["detected"] == 1

    runs = _import_runs(cli_env)
    assert len(runs) == 1
    run = runs[0]
    assert run["started_at"] and run["finished_at"]
    assert run["actor"] == "hantani"
    assert run["target_path"]
    assert run["detected"] == 1
    assert run["added"] == 1
    assert run["updated"] == 0
    assert run["skipped_unchanged"] == 0
    assert run["missing"] == 0
    assert run["failed"] == 0
    assert run["succeeded"] == 1
    assert run["error_summary"] == ""


def test_second_import_records_skipped_unchanged(cli_env, capsys):
    (cli_env / "knowledge" / "public" / "faq.md").write_text(
        "# 手順\n\n内容です。\n", encoding="utf-8"
    )
    main(["init"])
    main(["knowledge", "import"])
    capsys.readouterr()

    main(["knowledge", "import"])
    capsys.readouterr()

    runs = _import_runs(cli_env)
    assert len(runs) == 2
    latest = runs[0]
    assert latest["skipped_unchanged"] == 1
    assert latest["added"] == 0
    assert latest["succeeded"] == 1


def test_changed_file_counts_as_updated(cli_env, capsys):
    path = cli_env / "knowledge" / "public" / "faq.md"
    path.write_text("# 手順\n\n最初の内容。\n", encoding="utf-8")
    main(["init"])
    main(["knowledge", "import"])
    capsys.readouterr()

    path.write_text("# 手順\n\n更新された内容。\n", encoding="utf-8")
    main(["knowledge", "import"])
    capsys.readouterr()

    latest = _import_runs(cli_env)[0]
    assert latest["updated"] == 1
    assert latest["added"] == 0


def test_missing_file_is_counted(cli_env, capsys):
    path = cli_env / "knowledge" / "public" / "faq.md"
    path.write_text("# 手順\n\n内容。\n", encoding="utf-8")
    main(["init"])
    main(["knowledge", "import"])
    capsys.readouterr()

    path.unlink()
    main(["knowledge", "import"])
    capsys.readouterr()

    latest = _import_runs(cli_env)[0]
    assert latest["missing"] == 1


def test_default_actor_is_cli(cli_env, capsys):
    (cli_env / "knowledge" / "public" / "faq.md").write_text("# a\n\nb\n", encoding="utf-8")
    main(["init"])
    main(["knowledge", "import"])
    capsys.readouterr()
    assert _import_runs(cli_env)[0]["actor"] == "cli"


def test_import_run_records_failure(cli_env, capsys, monkeypatch):
    """取り込み中に例外が出ても実行記録を残し、失敗として記録する。"""
    (cli_env / "knowledge" / "public" / "faq.md").write_text("# a\n\nb\n", encoding="utf-8")
    main(["init"])
    capsys.readouterr()

    def boom(*args, **kwargs):
        raise RuntimeError("取り込み中の想定外エラー")

    monkeypatch.setattr("aifaq.ingestion.import_all", boom)
    with pytest.raises(RuntimeError):
        main(["knowledge", "import"])

    runs = _import_runs(cli_env)
    assert len(runs) == 1
    assert runs[0]["succeeded"] == 0
    assert runs[0]["finished_at"]
    assert "想定外エラー" in runs[0]["error_summary"]


def test_sync_alias_also_records_a_run(cli_env, capsys):
    (cli_env / "knowledge" / "public" / "faq.md").write_text("# a\n\nb\n", encoding="utf-8")
    main(["init"])
    capsys.readouterr()
    main(["knowledge", "sync"])
    capsys.readouterr()
    assert len(_import_runs(cli_env)) == 1
