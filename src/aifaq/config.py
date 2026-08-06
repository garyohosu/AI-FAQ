"""設定。環境変数で上書き可能にする。秘密情報はここに書かない。

環境変数名は特定製品名(Gemini)に固定せず `AIFAQ_RESEARCH_*` へ統一した
(instruction-2026-08-06-005 §2.3)。旧 `AIFAQ_GEMINI_BIN` は当面読み取るが、
使用された場合は非推奨警告を出す。
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path("data/aifaq.db")

#: 既定の調査プロバイダー実行ファイル名 (Antigravity CLI)。
DEFAULT_RESEARCH_BIN = "agy"

#: 対応するプロバイダー種別。
PROVIDER_ANTIGRAVITY = "antigravity"
PROVIDER_FAKE = "fake"

#: 調査指示の受け渡し方式。
#:
#: - ``arg``  : プロンプトをコマンドライン引数として渡す (実機確認済みの既定)
#: - ``file`` : 一時ファイルへ調査指示を書き、短い参照指示だけを引数で渡す
TRANSPORT_ARG = "arg"
TRANSPORT_FILE = "file"

_DEPRECATED_ENV = {
    "AIFAQ_GEMINI_BIN": "AIFAQ_RESEARCH_BIN",
}


def _read_with_deprecation(new_name: str, old_name: str, default: str) -> str:
    """新環境変数を優先し、旧環境変数のみが設定されていれば警告して使う。"""
    value = os.environ.get(new_name)
    if value is not None:
        return value
    legacy = os.environ.get(old_name)
    if legacy is not None:
        warnings.warn(
            f"{old_name} は非推奨です。{new_name} を使用してください。",
            DeprecationWarning,
            stacklevel=3,
        )
        return legacy
    return default


@dataclass(frozen=True)
class Settings:
    db_path: Path = DEFAULT_DB_PATH

    # --- 調査プロバイダー ---------------------------------------------------
    research_provider: str = PROVIDER_ANTIGRAVITY
    research_bin: str = DEFAULT_RESEARCH_BIN
    research_transport: str = TRANSPORT_ARG
    research_timeout_seconds: float = 180.0
    #: 一時作業ディレクトリの親。None ならOS既定のtempディレクトリを使う。
    #: リポジトリ本体とは必ず分離する (instruction §2.4)。
    research_workdir: Path | None = None
    research_model: str | None = None

    max_output_chars: int = 20_000
    knowledge_match_threshold: float = 0.55
    research_confidence_threshold: float = 0.6
    knowledge_dir: Path = Path("knowledge")
    memory_dir: Path = Path("memory")
    memory_index_path: Path = Path("MEMORY.md")
    max_clarification_rounds: int = 3
    log_level: str = "INFO"

    # --- 2ターミナル運用 / SQLite共有 (instruction-006 §6) ------------------
    #: ロック競合時にSQLite側で待つ時間(ミリ秒)。接続ごとに設定する。
    busy_timeout_ms: int = 5000
    #: `aifaq watch` の既定ポーリング間隔(秒)。
    watch_interval_seconds: float = 2.0
    #: ポーリング間隔の下限。DBを叩きすぎないための保護 (§4.2)。
    watch_min_interval_seconds: float = 0.5
    #: 人間回答本文の最大文字数 (§8)。
    max_answer_chars: int = 20_000

    # 知識取り込みの上限 (行数・列数・セル文字数・ファイルサイズ)
    max_import_rows: int = 5000
    max_import_cols: int = 50
    max_cell_chars: int = 1000
    max_file_size_bytes: int = 20_000_000
    max_text_chunk_chars: int = 4000

    @classmethod
    def from_env(cls) -> "Settings":
        max_rounds = int(os.environ.get("AIFAQ_MAX_CLARIFICATION_ROUNDS", "3"))

        provider = os.environ.get("AIFAQ_RESEARCH_PROVIDER", PROVIDER_ANTIGRAVITY).strip().lower()
        if provider not in (PROVIDER_ANTIGRAVITY, PROVIDER_FAKE):
            raise ValueError(
                f"AIFAQ_RESEARCH_PROVIDER must be one of "
                f"{PROVIDER_ANTIGRAVITY!r}/{PROVIDER_FAKE!r}, got {provider!r}"
            )

        transport = os.environ.get("AIFAQ_RESEARCH_TRANSPORT", TRANSPORT_ARG).strip().lower()
        if transport not in (TRANSPORT_ARG, TRANSPORT_FILE):
            raise ValueError(
                f"AIFAQ_RESEARCH_TRANSPORT must be one of "
                f"{TRANSPORT_ARG!r}/{TRANSPORT_FILE!r}, got {transport!r}"
            )

        workdir_raw = os.environ.get("AIFAQ_RESEARCH_WORKDIR")
        model_raw = os.environ.get("AIFAQ_RESEARCH_MODEL")

        return cls(
            db_path=Path(os.environ.get("AIFAQ_DB_PATH", str(DEFAULT_DB_PATH))),
            research_provider=provider,
            research_bin=_read_with_deprecation(
                "AIFAQ_RESEARCH_BIN", "AIFAQ_GEMINI_BIN", DEFAULT_RESEARCH_BIN
            ),
            research_transport=transport,
            research_timeout_seconds=float(
                os.environ.get("AIFAQ_RESEARCH_TIMEOUT", "180")
            ),
            research_workdir=Path(workdir_raw) if workdir_raw else None,
            research_model=model_raw or None,
            max_output_chars=int(os.environ.get("AIFAQ_MAX_OUTPUT_CHARS", "20000")),
            knowledge_match_threshold=float(
                os.environ.get("AIFAQ_KNOWLEDGE_THRESHOLD", "0.55")
            ),
            research_confidence_threshold=float(
                os.environ.get("AIFAQ_RESEARCH_CONFIDENCE_THRESHOLD", "0.6")
            ),
            knowledge_dir=Path(os.environ.get("AIFAQ_KNOWLEDGE_DIR", "knowledge")),
            memory_dir=Path(os.environ.get("AIFAQ_MEMORY_DIR", "memory")),
            memory_index_path=Path(os.environ.get("AIFAQ_MEMORY_INDEX", "MEMORY.md")),
            max_clarification_rounds=min(3, max(0, max_rounds)),
            log_level=os.environ.get("AIFAQ_LOG_LEVEL", "INFO"),
            busy_timeout_ms=int(os.environ.get("AIFAQ_BUSY_TIMEOUT_MS", "5000")),
            watch_interval_seconds=float(os.environ.get("AIFAQ_WATCH_INTERVAL", "2.0")),
            max_answer_chars=int(os.environ.get("AIFAQ_MAX_ANSWER_CHARS", "20000")),
        )
