"""SQLite接続とスキーマ管理。

ORMは導入せず、標準ライブラリ `sqlite3` とプレースホルダーを使う。
FTS5が使えない環境では `fts5_available` を False にし、呼び出し側で
LIKE検索へフォールバックできるようにする。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from aifaq.config import Settings

SCHEMA_VERSION = 1

_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_articles (
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

CREATE TABLE IF NOT EXISTS knowledge_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_id INTEGER NOT NULL REFERENCES knowledge_articles(id),
    version INTEGER NOT NULL,
    canonical_question TEXT NOT NULL,
    answer TEXT NOT NULL,
    changed_by TEXT,
    changed_at TEXT NOT NULL,
    change_reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS question_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_id INTEGER NOT NULL REFERENCES knowledge_articles(id),
    variant_question TEXT NOT NULL,
    variant_question_norm TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    question TEXT NOT NULL,
    original_question TEXT NOT NULL,
    classification_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL,
    answered_at TEXT,
    answered_by TEXT,
    resulting_knowledge_id INTEGER REFERENCES knowledge_articles(id)
);

CREATE TABLE IF NOT EXISTS query_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    question TEXT NOT NULL,
    classification_json TEXT NOT NULL DEFAULT '{}',
    route TEXT NOT NULL,
    answer_type TEXT NOT NULL,
    knowledge_ids_json TEXT NOT NULL DEFAULT '[]',
    research_run_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    question TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    error_summary TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS clarification_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    round_no INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer TEXT,
    options_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT '',
    asked_at TEXT NOT NULL,
    answered_at TEXT,
    UNIQUE(thread_id, round_no)
);

CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relative_path TEXT NOT NULL UNIQUE,
    file_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    owner TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'active',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    modified_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    last_imported_at TEXT,
    last_reviewed_at TEXT,
    valid_until TEXT,
    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS source_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id INTEGER NOT NULL REFERENCES source_files(id),
    location_type TEXT NOT NULL,
    sheet_name TEXT,
    row_start INTEGER,
    row_end INTEGER,
    heading TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS source_import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    processed INTEGER NOT NULL DEFAULT 0,
    imported INTEGER NOT NULL DEFAULT 0,
    skipped_unchanged INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_type TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source_markdown_path TEXT NOT NULL,
    heading TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    approved_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_questions(status);
CREATE INDEX IF NOT EXISTS idx_pending_thread ON pending_questions(thread_id);
CREATE INDEX IF NOT EXISTS idx_history_thread ON query_history(thread_id);
CREATE INDEX IF NOT EXISTS idx_clarif_thread ON clarification_turns(thread_id);
CREATE INDEX IF NOT EXISTS idx_variants_norm ON question_variants(variant_question_norm);
CREATE INDEX IF NOT EXISTS idx_knowledge_norm ON knowledge_articles(canonical_question_norm);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON source_chunks(source_file_id);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(memory_type);
"""

_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    knowledge_id UNINDEXED, canonical_question, answer, tags, variants,
    tokenize='trigram'
);
CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
    chunk_id UNINDEXED, source_file_id UNINDEXED, heading, content,
    tokenize='trigram'
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    memory_id UNINDEXED, key, value,
    tokenize='trigram'
);
"""
# 日本語(CJK)は単語間にスペースが無いため、既定のunicode61トークナイザーでは
# 単語単位の一致が機能しない。trigramトークナイザーを使うことで部分一致検索が
# 可能になる(3文字未満のクエリは検索対象外になるため、呼び出し側でLIKEへ
# フォールバックする)。


def _detect_fts5(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def connect(settings: Settings | None = None) -> sqlite3.Connection:
    settings = settings or Settings.from_env()
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> bool:
    """テーブルを冪等に初期化する。FTS5が使えたかどうかを返す。"""
    with conn:
        conn.executescript(_TABLES_SQL)
        fts5_available = _detect_fts5(conn)
        if fts5_available:
            conn.executescript(_FTS_SQL)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_available', ?)",
            ("1" if fts5_available else "0",),
        )
    return fts5_available


def fts5_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key='fts5_available'"
    ).fetchone()
    if row is None:
        return _detect_fts5(conn)
    return row["value"] == "1"
