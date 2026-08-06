"""設定。環境変数で上書き可能にする。秘密情報はここに書かない。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path("data/aifaq.db")


@dataclass(frozen=True)
class Settings:
    db_path: Path = DEFAULT_DB_PATH
    gemini_executable: str = "gemini"
    research_timeout_seconds: float = 60.0
    max_output_chars: int = 20_000
    knowledge_match_threshold: float = 0.55
    research_confidence_threshold: float = 0.6
    knowledge_dir: Path = Path("knowledge")
    memory_dir: Path = Path("memory")
    memory_index_path: Path = Path("MEMORY.md")
    max_clarification_rounds: int = 3
    log_level: str = "INFO"

    # 知識取り込みの上限 (行数・列数・セル文字数・ファイルサイズ)
    max_import_rows: int = 5000
    max_import_cols: int = 50
    max_cell_chars: int = 1000
    max_file_size_bytes: int = 20_000_000
    max_text_chunk_chars: int = 4000

    @classmethod
    def from_env(cls) -> "Settings":
        max_rounds = int(os.environ.get("AIFAQ_MAX_CLARIFICATION_ROUNDS", "3"))
        return cls(
            db_path=Path(os.environ.get("AIFAQ_DB_PATH", str(DEFAULT_DB_PATH))),
            gemini_executable=os.environ.get("AIFAQ_GEMINI_BIN", "gemini"),
            research_timeout_seconds=float(
                os.environ.get("AIFAQ_RESEARCH_TIMEOUT", "60")
            ),
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
        )
