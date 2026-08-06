"""決定論的なセキュリティ判定ルール。

分類をAIだけに任せず、まずここで外部送信禁止候補を検出する。
判断に迷う場合は外部へ送らない側へ倒す(誤送信より人間引き継ぎを優先する)。
"""

from __future__ import annotations

import re
import unicodedata

from aifaq.models import QuestionClassification, QuestionScope

# --- 秘密情報らしき語 ---------------------------------------------------
_SECRET_KEYWORDS = [
    "パスワード", "password", "passwd", "秘密鍵", "秘密键", "private key",
    "シークレット", "secret", "トークン", "token", "認証コード", "auth code",
    "apiキー", "api key", "apikey", "ワンタイムパスコード", "otp", "pin コード",
    "暗証番号", "credential", "資格情報",
]

_SECRET_VALUE_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"),  # base64っぽい長い文字列
]

# --- 個人情報 -------------------------------------------------------------
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b")
_PII_KEYWORDS = ["個人情報", "マイナンバー", "社員番号", "生年月日"]

# --- 社内固有情報 ----------------------------------------------------------
_INTERNAL_KEYWORDS = [
    "社内", "弊社", "当社", "申請先", "担当者", "共有フォルダ",
    "工場", "設備", "資産番号", "内部システム", "社内システム",
    "拠点", "部門", "支店", "本社",
]
_INTERNAL_IP_RE = re.compile(
    r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})\b"
)
_INTERNAL_HOST_RE = re.compile(r"\b[\w-]+\.(local|internal|corp)\b", re.IGNORECASE)

# --- 重要操作 --------------------------------------------------------------
_DANGEROUS_OP_KEYWORDS = [
    "アカウント停止", "アカウント削除", "権限付与", "権限変更",
    "退職者", "ネットワーク遮断", "強制ログアウト", "アクセス権削除",
]

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "windows": ["windows", "win10", "win11", "ウィンドウズ"],
    "network": ["wi-fi", "wifi", "ネットワーク", "lan", "vpn", "ネットワークアダプター"],
    "account": ["アカウント", "サインイン", "ログイン", "id", "パスワード"],
    "hardware": ["プリンター", "pc本体", "ハードウェア", "モニター", "周辺機器"],
    "software": ["ソフト", "アプリ", "office", "excel", "インストール"],
    "policy": ["ポリシー", "規程", "規則", "申請"],
}


def _contains_any(text: str, keywords: list[str]) -> str | None:
    lowered = text.lower()
    for kw in keywords:
        if kw.lower() in lowered:
            return kw
    return None


def contains_secret_like(text: str) -> bool:
    """本文に秘密情報らしき語・値が含まれるか判定する。"""
    if _contains_any(text, _SECRET_KEYWORDS):
        return True
    return any(p.search(text) for p in _SECRET_VALUE_PATTERNS)


def contains_pii_like(text: str) -> bool:
    if _EMAIL_RE.search(text) or _PHONE_RE.search(text):
        return True
    return _contains_any(text, _PII_KEYWORDS) is not None


def contains_internal_indicators(text: str) -> bool:
    if _contains_any(text, _INTERNAL_KEYWORDS):
        return True
    if _INTERNAL_IP_RE.search(text) or _INTERNAL_HOST_RE.search(text):
        return True
    return False


def contains_dangerous_operation(text: str) -> bool:
    return _contains_any(text, _DANGEROUS_OP_KEYWORDS) is not None


def guess_category(text: str) -> str:
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if _contains_any(text, keywords):
            return category
    return "other"


def classify_question(text: str) -> QuestionClassification:
    """質問文を決定論的ルールで分類する。

    優先順位: 秘密情報 > 危険操作 > 個人情報 > 社内固有 > 公開一般。
    """
    category = guess_category(text)

    if contains_secret_like(text):
        return QuestionClassification(
            scope=QuestionScope.SECURITY_SENSITIVE,
            category=category,
            reason="秘密情報(パスワード・トークン等)らしき語を検出したため外部送信不可",
            safe_for_external_research=False,
        )

    if contains_dangerous_operation(text):
        return QuestionClassification(
            scope=QuestionScope.SECURITY_SENSITIVE,
            category=category,
            reason="アカウント停止・権限変更等の重要操作に関する質問のため人間対応が必要",
            safe_for_external_research=False,
        )

    if contains_pii_like(text):
        return QuestionClassification(
            scope=QuestionScope.PERSONAL_DATA,
            category=category,
            reason="個人情報(メールアドレス・社員番号等)らしき情報を検出したため外部送信不可",
            safe_for_external_research=False,
        )

    if contains_internal_indicators(text):
        return QuestionClassification(
            scope=QuestionScope.INTERNAL,
            category=category,
            reason="社内固有情報(内部IP・ホスト名・社内手続き語)を検出したため外部送信不可",
            safe_for_external_research=False,
        )

    if not text.strip():
        return QuestionClassification(
            scope=QuestionScope.UNKNOWN,
            category=category,
            reason="質問が空のため分類不能",
            safe_for_external_research=False,
        )

    return QuestionClassification(
        scope=QuestionScope.PUBLIC_GENERAL,
        category=category,
        reason="社内固有・秘密・個人情報の兆候が見つからないため公開一般質問として扱う",
        safe_for_external_research=True,
    )


def redact_for_storage(text: str) -> str:
    """メモリー・確認履歴へ保存する前に秘密情報らしき値を置換する。"""
    redacted = text
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    if contains_secret_like(redacted):
        redacted = "[この回答には秘密情報らしき語が含まれていたため保存前に伏字にしました]"
    return redacted


class AnswerValidationError(ValueError):
    """人間回答の本文が受け入れ条件を満たさない。"""


#: 制御文字のうち、本文へ含めてよいもの(改行・復帰・タブ)。
_ALLOWED_CONTROL_CHARS = {"\n", "\r", "\t"}


def validate_human_answer(text: str, *, max_chars: int) -> str:
    """IT管理者の回答本文を検証して返す (instruction-006 §8)。

    標準入力経由で任意の内容が渡るため、次を拒否する。

    - 空文字
    - 上限を超える極端な長文(DBと表示を壊さないため)
    - 改行・タブ以外の制御文字(端末制御シーケンスの混入を防ぐ)

    秘密情報の扱いは既存の伏字化ポリシー(`redact_for_storage`)に従い、
    ここでは行わない。回答本文そのものは伏字化せずに保存する
    (IT管理者が意図して書いた業務回答であるため)。
    """
    if not text or not text.strip():
        raise AnswerValidationError("回答本文が空です")
    if len(text) > max_chars:
        raise AnswerValidationError(
            f"回答本文が長すぎます({len(text)}文字 > 上限{max_chars}文字)"
        )
    bad = sorted(
        {
            ch
            for ch in text
            if unicodedata.category(ch) in ("Cc", "Cf")
            and ch not in _ALLOWED_CONTROL_CHARS
        }
    )
    if bad:
        codes = ", ".join(f"U+{ord(ch):04X}" for ch in bad)
        raise AnswerValidationError(
            f"回答本文に使用できない制御文字が含まれています({codes})"
        )
    return text
