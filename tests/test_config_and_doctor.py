"""設定 (instruction-005 §2.3)、doctor、スキーマ移行のテスト。"""

import json
import sqlite3

import pytest

from aifaq.cli import main
from aifaq.config import Settings


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


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


def test_defaults_are_antigravity_and_arg_transport(monkeypatch):
    for name in (
        "AIFAQ_RESEARCH_PROVIDER", "AIFAQ_RESEARCH_BIN", "AIFAQ_RESEARCH_TRANSPORT",
        "AIFAQ_GEMINI_BIN", "AIFAQ_RESEARCH_MODEL", "AIFAQ_RESEARCH_WORKDIR",
    ):
        monkeypatch.delenv(name, raising=False)
    s = Settings.from_env()
    assert s.research_provider == "antigravity"
    assert s.research_bin == "agy"
    assert s.research_transport == "arg"
    assert s.research_workdir is None


def test_research_env_vars_are_read(monkeypatch):
    monkeypatch.setenv("AIFAQ_RESEARCH_PROVIDER", "fake")
    monkeypatch.setenv("AIFAQ_RESEARCH_BIN", "my-agy")
    monkeypatch.setenv("AIFAQ_RESEARCH_TRANSPORT", "file")
    monkeypatch.setenv("AIFAQ_RESEARCH_TIMEOUT", "42")
    monkeypatch.setenv("AIFAQ_RESEARCH_WORKDIR", "C:/tmp/work")
    s = Settings.from_env()
    assert s.research_provider == "fake"
    assert s.research_bin == "my-agy"
    assert s.research_transport == "file"
    assert s.research_timeout_seconds == 42.0
    assert str(s.research_workdir).replace("\\", "/") == "C:/tmp/work"


def test_legacy_gemini_bin_warns_but_still_works(monkeypatch):
    monkeypatch.delenv("AIFAQ_RESEARCH_BIN", raising=False)
    monkeypatch.setenv("AIFAQ_GEMINI_BIN", "legacy-bin")
    with pytest.warns(DeprecationWarning, match="AIFAQ_GEMINI_BIN"):
        s = Settings.from_env()
    assert s.research_bin == "legacy-bin"


def test_new_env_var_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("AIFAQ_RESEARCH_BIN", "new-bin")
    monkeypatch.setenv("AIFAQ_GEMINI_BIN", "legacy-bin")
    assert Settings.from_env().research_bin == "new-bin"


@pytest.mark.parametrize(
    "name,value",
    [("AIFAQ_RESEARCH_PROVIDER", "gemini"), ("AIFAQ_RESEARCH_TRANSPORT", "stdin")],
)
def test_invalid_values_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_provider_selection_honours_env(monkeypatch, tmp_path):
    from aifaq.cli import _get_provider
    from aifaq.providers.antigravity import AntigravityProvider
    from aifaq.providers.fake import FakeResearchProvider

    s = Settings(db_path=tmp_path / "a.db", research_provider="fake")
    assert isinstance(_get_provider(s, False), FakeResearchProvider)

    s = Settings(db_path=tmp_path / "a.db", research_provider="antigravity")
    assert isinstance(_get_provider(s, False), AntigravityProvider)
    # --fake-provider は環境設定より優先される
    assert isinstance(_get_provider(s, True), FakeResearchProvider)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_reports_antigravity_checks(cli_env, capsys, monkeypatch):
    monkeypatch.setenv("AIFAQ_RESEARCH_PROVIDER", "antigravity")
    monkeypatch.setattr("aifaq.cli.shutil.which", lambda name: None)
    main(["--json", "doctor"])
    names = {c["name"] for c in json.loads(capsys.readouterr().out)}
    assert "research_provider" in names
    assert "antigravity_cli_present" in names
    # Gemini固有の項目は残っていない
    assert not any(n.startswith("gemini") for n in names)


def test_doctor_skips_cli_check_for_fake_provider(cli_env, capsys, monkeypatch):
    monkeypatch.setenv("AIFAQ_RESEARCH_PROVIDER", "fake")
    main(["--json", "doctor"])
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)}
    assert "FakeProvider" in checks["antigravity_cli_present"]["detail"]


def test_doctor_warns_about_file_transport(cli_env, capsys, monkeypatch):
    # CI は AIFAQ_RESEARCH_PROVIDER=fake を全体に設定するため、
    # このテストが見たい分岐を明示的に選ぶ。
    monkeypatch.setenv("AIFAQ_RESEARCH_PROVIDER", "antigravity")
    monkeypatch.setenv("AIFAQ_RESEARCH_TRANSPORT", "file")
    monkeypatch.setattr("aifaq.cli.shutil.which", lambda name: None)
    main(["--json", "doctor"])
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)}
    assert checks["antigravity_transport_file_note"]["status"] == "warn"


def test_doctor_reports_version_when_cli_present(cli_env, capsys, monkeypatch):
    monkeypatch.setenv("AIFAQ_RESEARCH_PROVIDER", "antigravity")
    monkeypatch.setattr("aifaq.cli.shutil.which", lambda name: "C:/fake/agy.exe")
    monkeypatch.setattr(
        "aifaq.cli.AntigravityProvider.version", lambda self: "1.1.9"
    )
    main(["--json", "doctor"])
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)}
    assert checks["antigravity_cli_version"]["detail"] == "1.1.9"


def test_doctor_warns_when_version_fails(cli_env, capsys, monkeypatch):
    from aifaq.providers.base import ProviderNotAvailableError

    monkeypatch.setenv("AIFAQ_RESEARCH_PROVIDER", "antigravity")
    monkeypatch.setattr("aifaq.cli.shutil.which", lambda name: "C:/fake/agy.exe")

    def boom(self):
        raise ProviderNotAvailableError("agy is broken")

    monkeypatch.setattr("aifaq.cli.AntigravityProvider.version", boom)
    main(["--json", "doctor"])
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)}
    assert checks["antigravity_cli_version"]["status"] == "warn"


# ---------------------------------------------------------------------------
# スキーマ移行
# ---------------------------------------------------------------------------


def test_v1_database_is_migrated_in_place(tmp_path):
    """schema v1 で作られた既存DBへ、v2の列が後から追加される。"""
    from aifaq import db as db_module

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # v1 相当の最小テーブル
    conn.executescript(
        """
        CREATE TABLE research_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL, question TEXT NOT NULL,
            provider TEXT NOT NULL, status TEXT NOT NULL, confidence REAL,
            sources_json TEXT NOT NULL DEFAULT '[]',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            error_summary TEXT, started_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE source_import_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL, finished_at TEXT,
            processed INTEGER NOT NULL DEFAULT 0,
            imported INTEGER NOT NULL DEFAULT 0,
            skipped_unchanged INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            warnings_json TEXT NOT NULL DEFAULT '[]'
        );
        """
    )
    conn.execute(
        "INSERT INTO research_runs (thread_id, question, provider, status, started_at)"
        " VALUES ('t', 'q', 'p', 'ok', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    conn = db_module.connect(Settings(db_path=db_path))
    db_module.init_db(conn)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(research_runs)")}
    assert {"answer", "review_status", "reviewed_by", "resulting_knowledge_id"} <= cols
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(source_import_runs)")}
    assert {"target_path", "actor", "added", "updated", "missing", "succeeded"} <= cols

    # 既存行は残り、新列は既定値になる
    row = conn.execute("SELECT * FROM research_runs WHERE id=1").fetchone()
    assert row["question"] == "q"
    assert row["review_status"] == "PENDING"
    conn.close()


def test_v2_database_gains_v3_pending_columns_without_data_loss(tmp_path):
    """既存DBを削除せず、pending_questions へ v3 の列を追加できる。

    instruction-006 §5「既存の data/aifaq.db を壊さない」の検証。
    """
    from aifaq import db as db_module
    from aifaq.repositories import Repositories

    db_path = tmp_path / "v2.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # v1/v2 相当の pending_questions (v3 の列は無い)
    conn.executescript(
        """
        CREATE TABLE knowledge_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_question TEXT NOT NULL,
            canonical_question_norm TEXT NOT NULL,
            answer TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'other',
            tags_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'DRAFT',
            source_type TEXT NOT NULL DEFAULT 'HUMAN',
            version INTEGER NOT NULL DEFAULT 1,
            approved_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            valid_until TEXT,
            source_urls_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE pending_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            question TEXT NOT NULL,
            original_question TEXT NOT NULL,
            classification_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'OPEN',
            created_at TEXT NOT NULL,
            answered_at TEXT,
            answered_by TEXT,
            resulting_knowledge_id INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO pending_questions "
        "(thread_id, question, original_question, created_at) "
        "VALUES ('old-thread', 'q', '旧DBの質問', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    conn = db_module.connect(Settings(db_path=db_path))
    db_module.init_db(conn)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pending_questions)")}
        assert {
            "answer_text", "answer_type", "delivery_status", "delivered_at", "updated_at"
        } <= cols
        # 既存の列を重複追加していない
        assert len([c for c in cols if c == "answered_by"]) == 1

        # 既存行は残る
        row = conn.execute("SELECT * FROM pending_questions WHERE id=1").fetchone()
        assert row["original_question"] == "旧DBの質問"
        assert row["status"] == "OPEN"
        assert row["answer_text"] is None
        assert row["delivery_status"] == "UNDELIVERED"

        # 旧DBの pending にそのまま回答でき、status も取れる
        repos = Repositories.build(conn)
        repos.pending.answer(
            1, answer_text="移行後に登録した回答", category="other",
            tags=[], variants=[], approved_by="hantani",
        )
        status = repos.thread_status(thread_id="old-thread")
        assert status.state.value == "ANSWERED"
        assert status.answer == "移行後に登録した回答"
    finally:
        conn.close()


def test_migration_is_idempotent_across_repeated_init(tmp_path):
    """init_db を繰り返しても既存データを壊さない。"""
    from aifaq import db as db_module
    from aifaq.repositories import Repositories

    settings = Settings(db_path=tmp_path / "repeat.db")
    conn = db_module.connect(settings)
    db_module.init_db(conn)
    pid = Repositories.build(conn).pending.create(
        thread_id="t", question="q", original_question="q", classification={}
    )
    conn.close()

    for _ in range(3):
        conn = db_module.connect(settings)
        db_module.init_db(conn)
        conn.close()

    conn = db_module.connect(settings)
    try:
        row = conn.execute("SELECT * FROM pending_questions WHERE id=?", (pid,)).fetchone()
        assert row is not None
        assert row["status"] == "OPEN"
    finally:
        conn.close()


def test_init_db_is_idempotent(tmp_path):
    from aifaq import db as db_module

    s = Settings(db_path=tmp_path / "a.db")
    conn = db_module.connect(s)
    db_module.init_db(conn)
    db_module.init_db(conn)  # 2回目も失敗しない
    version = conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()["value"]
    assert version == str(db_module.SCHEMA_VERSION)
    conn.close()
