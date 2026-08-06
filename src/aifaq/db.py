"""SQLite接続とスキーマ管理。

ORMは導入せず、標準ライブラリ `sqlite3` とプレースホルダーを使う。
FTS5が使えない環境では `fts5_available` を False にし、呼び出し側で
LIKE検索へフォールバックできるようにする。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from aifaq.config import Settings

SCHEMA_VERSION = 3

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
    resulting_knowledge_id INTEGER REFERENCES knowledge_articles(id),
    -- schema v3: 人間回答を元の質問者へ返すための列 (instruction-006 §5)
    -- answered_at / answered_by / resulting_knowledge_id は既存のものを使い、
    -- 同等項目を重複して追加しない。
    answer_text TEXT,
    answer_type TEXT NOT NULL DEFAULT 'HUMAN',
    delivery_status TEXT NOT NULL DEFAULT 'UNDELIVERED',
    delivered_at TEXT,
    updated_at TEXT
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
    finished_at TEXT,
    -- schema v2: AI調査結果の人間承認 (instruction-005 §5.1)
    answer TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'PENDING',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_reason TEXT NOT NULL DEFAULT '',
    was_modified INTEGER NOT NULL DEFAULT 0,
    approved_answer TEXT,
    resulting_knowledge_id INTEGER REFERENCES knowledge_articles(id)
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
    warnings_json TEXT NOT NULL DEFAULT '[]',
    -- schema v2: 取り込み単位の記録 (instruction-005 §5.2)
    target_path TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    detected INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT NOT NULL DEFAULT '',
    succeeded INTEGER NOT NULL DEFAULT 0
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


#: 既存DBへ後から足す列(スキーマ版ごと)。
#: ``CREATE TABLE IF NOT EXISTS`` は既存テーブルの列を増やさないため、
#: ``ALTER TABLE ... ADD COLUMN`` で冪等に追加する。既存DBは削除しない。
#: SQLite の ADD COLUMN は非定数 DEFAULT と REFERENCES 付き NOT NULL を
#: 受け付けないので、ここでは定数 DEFAULT または NULL 許容のみを使う。
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "research_runs": {
        "answer": "TEXT NOT NULL DEFAULT ''",
        "review_status": "TEXT NOT NULL DEFAULT 'PENDING'",
        "reviewed_by": "TEXT",
        "reviewed_at": "TEXT",
        "review_reason": "TEXT NOT NULL DEFAULT ''",
        "was_modified": "INTEGER NOT NULL DEFAULT 0",
        "approved_answer": "TEXT",
        "resulting_knowledge_id": "INTEGER",
    },
    "source_import_runs": {
        "target_path": "TEXT NOT NULL DEFAULT ''",
        "actor": "TEXT NOT NULL DEFAULT ''",
        "detected": "INTEGER NOT NULL DEFAULT 0",
        "added": "INTEGER NOT NULL DEFAULT 0",
        "updated": "INTEGER NOT NULL DEFAULT 0",
        "missing": "INTEGER NOT NULL DEFAULT 0",
        "error_summary": "TEXT NOT NULL DEFAULT ''",
        "succeeded": "INTEGER NOT NULL DEFAULT 0",
    },
    # schema v3: 人間回答を元の質問者へ返すための列 (instruction-006 §5)。
    # answered_at / answered_by / resulting_knowledge_id は v1 から存在するため
    # 重複追加しない。
    "pending_questions": {
        "answer_text": "TEXT",
        "answer_type": "TEXT NOT NULL DEFAULT 'HUMAN'",
        "delivery_status": "TEXT NOT NULL DEFAULT 'UNDELIVERED'",
        "delivered_at": "TEXT",
        "updated_at": "TEXT",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    """既存DBを最新スキーマへ引き上げる(冪等)。"""
    for table, columns in _ADDED_COLUMNS.items():
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not existing:
            continue  # テーブル自体が未作成なら _TABLES_SQL 側で作られる
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _detect_fts5(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def connect(settings: Settings | None = None) -> sqlite3.Connection:
    """業務DBへ接続する。

    2ターミナル運用(質問者と IT管理者が同じ `data/aifaq.db` を別プロセス
    から使う, instruction-006 §6)を前提に、接続ごとに次を設定する。

    - ``journal_mode=WAL``: 読み手(`watch` のポーリング)が書き手
      (`pending answer`)をブロックしない。WALはDBファイルに永続する設定
      なので毎回の指定は冪等だが、新規DBや他モードのDBでも確実に効くよう
      接続時に必ず実行する。
    - ``busy_timeout``: ロック競合時に即座に諦めず、SQLite側で待つ。
      PRAGMAは**接続ごと**の設定なので、接続を作るこの場所で必ず設定する。
    - ``synchronous=NORMAL``: WAL利用時の推奨値。耐久性を保ちつつ
      書き込みのたびのfsyncを減らし、ロック保持時間を短くする。
    """
    settings = settings or Settings.from_env()
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        # Pythonの暗黙BEGINを避け、`with conn:` の範囲だけを書き込み
        # トランザクションにする。読み取りは自動コミットのまま短く終わるため、
        # watch のポーリングが書き手を待たせない。
        isolation_level="DEFERRED",
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        # WAL非対応のファイルシステム(一部のネットワークドライブ)でも
        # 動作は継続する。既定のjournal_modeのままロック競合に備える。
        pass
    conn.execute(f"PRAGMA busy_timeout={int(settings.busy_timeout_ms)}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def is_lock_error(exc: BaseException) -> bool:
    """SQLiteのロック競合由来のエラーかどうかを判定する。"""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def init_db(conn: sqlite3.Connection) -> bool:
    """テーブルを冪等に初期化する。FTS5が使えたかどうかを返す。"""
    with conn:
        conn.executescript(_TABLES_SQL)
        _migrate(conn)
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


class DatabaseBusyError(RuntimeError):
    """ロック競合が再試行上限まで解消しなかったことを表す。

    生の「database is locked」ではなく、利用者が次に何をすればよいか
    分かる形で扱うために独自の例外にする (instruction-006 §6)。
    """


def with_lock_retry(operation, *, attempts: int = 5, base_delay: float = 0.1):
    """ロック競合時に限り、上限付きで再試行する。

    `busy_timeout` でSQLite側も待つが、WALでも書き込みが重なると
    `database is locked` が返ることがあるため、アプリ側でも短い指数
    バックオフで再試行する。**無限には再試行しない** (§6)。
    ロック以外のエラーは再試行せずそのまま送出する。
    """
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not is_lock_error(exc):
                raise
            last_exc = exc
            if attempt == attempts - 1:
                break
            time.sleep(base_delay * (2**attempt))
    raise DatabaseBusyError(
        f"データベースがロックされています(再試行{attempts}回)。"
        "他の aifaq プロセスの処理が終わってから再実行してください。"
    ) from last_exc


def fts5_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key='fts5_available'"
    ).fetchone()
    if row is None:
        return _detect_fts5(conn)
    return row["value"] == "1"
