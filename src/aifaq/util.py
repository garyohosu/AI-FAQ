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


# ---------------------------------------------------------------------------
# 日本語の自然文からの検索語抽出
# ---------------------------------------------------------------------------
#
# 形態素解析器は導入しない(依存を増やさないため)。代わりに、日本語では
# 内容語が漢字・カタカナ・英数字に、文法要素(助詞・助動詞・活用語尾・
# 丁寧表現)がひらがなに強く偏る性質を使う。漢字/カタカナ/英数字の連続を
# 検索語として取り出し、ひらがなは捨てる。
#
#   「スプーラーの状態を確認する方法」→ ["スプーラー", "状態", "確認", "方法"]
#   「Wi-Fiが繋がらないのですが…」    → ["Wi-Fi", "繋"]
#
# 助詞を区切り文字にする方法も試したが、「繋がらない」の「が」で語が壊れる
# など、ひらがなを含む語を誤って分割するため採用しない。
#
# 長音記号「ー」と繰り返し記号「々」は語の内部に現れるので、
# それぞれカタカナ・漢字のクラスに含める。

_TERM_RE = re.compile(
    r"[一-龯々〆ヵヶ]+"                    # 漢字
    r"|[ァ-ヶー]+"                          # カタカナ(長音符を含む)
    r"|[A-Za-z][A-Za-z0-9\-]*"             # 英字始まりの語 (Wi-Fi, VPN, Windows11)
    r"|[0-9]+"                              # 数字
)

#: 質問側に頻出するが単独では絞り込みに寄与しない語。
#: これらは2文字なので `is_relevant_match` の条件を単独では満たさないが、
#: 一致語数を水増しして順位を歪めるため、あらかじめ除いておく。
_QUERY_STOPWORDS = frozenset({"方法", "手順", "場合", "対応", "内容", "以下", "何"})

#: FTS5のtrigramトークナイザーは3文字未満を索引できない。
_FTS_MIN_TERM_LEN = 3
#: LIKE検索でも1文字語はノイズが多すぎるため2文字以上に限る。
_LIKE_MIN_TERM_LEN = 2

def extract_search_terms(query: str, *, min_len: int = _LIKE_MIN_TERM_LEN) -> list[str]:
    """日本語の自然文から検索に有効な語を抽出する。

    出現順を保ったまま重複を除いて返す。抽出できない場合は空リストを返す
    (呼び出し側はクエリ全体でのフレーズ検索へフォールバックする)。
    """
    normalized = unicodedata.normalize("NFKC", query).strip()
    if not normalized:
        return []

    terms: list[str] = []
    for piece in _TERM_RE.findall(normalized):
        if len(piece) < min_len:
            continue
        if piece in _QUERY_STOPWORDS:
            continue
        if piece not in terms:
            terms.append(piece)
    return terms


def fts_terms(query: str) -> list[str]:
    """FTS5(trigram)で実際に索引しうる長さの検索語だけを返す。"""
    return [t for t in extract_search_terms(query) if len(t) >= _FTS_MIN_TERM_LEN]


def matched_terms(text: str, terms: list[str]) -> list[str]:
    """`text` に実際に含まれている検索語を返す(大文字小文字を無視)。"""
    haystack = unicodedata.normalize("NFKC", text).lower()
    return [t for t in terms if t.lower() in haystack]


#: 一致した語が質問全体をどれだけ説明できていれば採用するか(文字数比)。
#: 語数の比ではなく文字数の比を使うのは、長い語ほど情報量が大きいため。
#:
#:   「Windows 11でネットワークアダプターを再起動する公式手順は？」
#:     語 = Windows(7) 11(2) ネットワークアダプター(11) 再起動(3) 公式手順(4) = 27文字
#:     ネットワークアダプター + 再起動 が一致 = 14文字 → 0.52 → 採用
#:
#:   「Windows 11のディスク空き容量を確認する公式手順は？」
#:     語 = Windows(7) 11(2) ディスク(4) 容量(2) 確認(2) 公式手順(4) = 21文字
#:     見出しの「Windows」だけが一致 = 7文字 → 0.33 → 不採用
#:
#: 「Windows」のような一般的な語がひとつ当たっただけの資料を、
#: 質問への回答として採用してしまわないための閾値。
_MIN_RELEVANCE_RATIO = 0.4


def relevance_ratio(text: str, terms: list[str]) -> float:
    """一致した検索語が、質問の検索語全体に占める文字数の割合。"""
    total = sum(len(t) for t in terms)
    if total == 0:
        return 0.0
    return sum(len(t) for t in matched_terms(text, terms)) / total


def is_relevant_match(text: str, terms: list[str]) -> bool:
    """OR検索の結果を採用してよいかを判定する。

    OR検索は語を1つでも含めば当たってしまうため、無関係な資料を拾わないよう
    次の2つを同時に満たすことを求める。

    1. 「特徴的な語(3文字以上)を1つ以上含む」または「2語以上を含む」
    2. 一致した語が質問の検索語全体の `_MIN_RELEVANCE_RATIO` 以上を説明する
    """
    hits = matched_terms(text, terms)
    if not hits:
        return False
    if len(hits) < 2 and not any(len(t) >= _FTS_MIN_TERM_LEN for t in hits):
        return False
    return relevance_ratio(text, terms) >= _MIN_RELEVANCE_RATIO


def relevance_key(text: str, terms: list[str]) -> tuple[int, int]:
    """一致した語数と最長一致語長。大きいほど関連が強い。"""
    hits = matched_terms(text, terms)
    return (len(hits), max((len(t) for t in hits), default=0))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def is_high_similarity(a: str, b: str, threshold: float = 0.9) -> bool:
    return similarity(a, b) >= threshold
