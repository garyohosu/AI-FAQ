"""正規化・時刻まわりの小さなユーティリティ。"""

from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import UTC, datetime

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[、。・.,!?！？「」『』()（）\[\]【】:：;；\-—_/\\]")


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def normalize_text(text: str) -> str:
    """検索・重複判定用の正規化。全角/半角統一、記号除去、小文字化。"""
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    normalized = _PUNCT_RE.sub("", normalized)
    normalized = _WHITESPACE_RE.sub("", normalized)
    return normalized


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def is_high_similarity(a: str, b: str, threshold: float = 0.9) -> bool:
    return similarity(a, b) >= threshold
