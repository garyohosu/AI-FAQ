"""LangGraphから独立してテストできる、純粋な経路判定ロジック。

`assess_answerability` ノードはここの関数を呼び出すだけにし、判定ロジック
そのものはLangGraphに依存させない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aifaq.config import Settings
from aifaq.models import ClarificationDecision
from aifaq.util import is_high_similarity, normalize_text

_UNKNOWN_ANSWERS = {"わからない", "分からない", "不明", "確認できない", "わかりません"}
_HUMAN_REQUEST_KEYWORDS = ["人間", "担当者にお願い", "人に代わって", "オペレーターにつないで"]

# instruction-2026-08-06-002 の「代表的な確認項目」に基づく決定論的テンプレート。
# 各エントリ: (質問文, 選択肢, 理由)
CLARIFICATION_TEMPLATES: dict[str, list[tuple[str, list[str], str]]] = {
    "windows": [
        (
            "対象端末のOSはどれですか？",
            ["Windows 10", "Windows 11", "その他"],
            "OSバージョンで対処手順が異なるため",
        ),
        (
            "エラーはサインイン前とサインイン後のどちらで表示されますか？",
            ["サインイン前", "サインイン後", "エラーは出ていない"],
            "発生タイミングで原因切り分けが変わるため",
        ),
        (
            "この症状は1台だけですか、複数端末で発生していますか？",
            ["1台だけ", "複数端末"],
            "影響範囲によって対応の緊急度が変わるため",
        ),
    ],
    "network": [
        (
            "接続方法は有線と無線のどちらですか？",
            ["有線(LAN)", "無線(Wi-Fi)", "両方で発生"],
            "接続方式によって確認手順が異なるため",
        ),
        (
            "この症状はあなただけですか、他の方も同じ状況ですか？",
            ["自分だけ", "複数人で発生", "分からない"],
            "影響範囲の切り分けのため",
        ),
        (
            "インターネットだけ使えないのか、社内の共有資源も使えないのか、どちらですか？",
            ["インターネットのみ不可", "社内資源も不可", "両方使える"],
            "障害範囲の特定のため",
        ),
    ],
    "account": [
        (
            "対象のサービス・システム名は何ですか？",
            [],
            "対象サービスによって手順が異なるため",
        ),
        (
            "サインイン自体はできますか？",
            ["できる", "できない", "分からない"],
            "サインイン可否で原因切り分けが変わるため",
        ),
    ],
    "hardware": [
        (
            "対象の機器種別は何ですか？",
            ["PC本体", "プリンター", "モニター", "その他周辺機器"],
            "機器種別で確認手順が異なるため",
        ),
        (
            "電源は入りますか？",
            ["入る", "入らない", "分からない"],
            "電源状態で切り分けが変わるため",
        ),
    ],
    "software": [
        (
            "対象の製品・アプリ名は何ですか？",
            [],
            "製品によって対処が異なるため",
        ),
        (
            "エラーメッセージは表示されていますか？表示されている場合、その内容を教えてください。",
            [],
            "エラー内容で原因を絞り込むため",
        ),
    ],
    "policy": [
        (
            "対象の手続き・申請は何ですか？",
            [],
            "手続きの種類によって窓口が異なるため",
        ),
    ],
    "other": [
        (
            "困っている操作や画面を、具体的に教えてください(例: 特定のアプリ名やエラーメッセージ)。",
            [],
            "回答に必要な具体情報が不足しているため",
        ),
    ],
}


@dataclass
class RouteDecision:
    route: str  # "knowledge" | "clarify" | "research" | "human"
    reason: str = ""
    clarification: ClarificationDecision | None = None
    escalate_reason: str | None = None


def normalize_answer(text: str) -> str:
    return normalize_text(text)


def is_unknown_answer(text: str | None) -> bool:
    if not text:
        return False
    return normalize_answer(text) in {normalize_answer(t) for t in _UNKNOWN_ANSWERS}


def requests_human(text: str | None) -> bool:
    if not text:
        return False
    return any(kw in text for kw in _HUMAN_REQUEST_KEYWORDS)


def count_consecutive_unknown(clarification_history: list[dict]) -> int:
    count = 0
    for turn in reversed(clarification_history):
        if turn.get("answer") is None:
            continue
        if is_unknown_answer(turn["answer"]):
            count += 1
        else:
            break
    return count


def check_escalation(
    *,
    classification: dict,
    clarification_history: list[dict],
    last_answer: str | None,
) -> str | None:
    """即時に人間対応へ進めるべき理由があれば返す。無ければNone。"""
    if requests_human(last_answer):
        return "質問者が人間対応を希望した"
    if count_consecutive_unknown(clarification_history) >= 2:
        return "「分からない」等の回答が2回連続したため"
    scope = classification.get("scope")
    if scope == "SECURITY_SENSITIVE":
        # 秘密情報の提示が必要に見える質問・重要操作は、確認質問すら行わず
        # 直接人間担当へ引き継ぐ(002: 機密情報が必要に見える場合はそれを
        # 質問せず人間担当へ引き継ぐ)。
        return classification.get("reason") or "セキュリティ上重要な質問のため人間対応が必要"
    return None


def pick_clarification_template(
    category: str, asked_questions: list[str]
) -> tuple[str, list[str], str] | None:
    candidates = CLARIFICATION_TEMPLATES.get(category, CLARIFICATION_TEMPLATES["other"])
    for question, options, reason in candidates:
        if any(is_high_similarity(question, asked) for asked in asked_questions):
            continue
        return question, options, reason
    return None


def is_vague_public_question(question: str, category: str) -> bool:
    norm = normalize_text(question)
    if category == "other":
        return len(norm) < 20
    return len(norm) < 12


def best_knowledge_match(matches: list[dict], threshold: float) -> dict | None:
    for m in matches:
        if m["score"] >= 1.0 - 1e-9 or m["score"] >= threshold:
            return m
    return None


def best_source_chunk(chunks: list[dict]) -> dict | None:
    return chunks[0] if chunks else None


def decide_route(state: dict, settings: Settings) -> RouteDecision:
    classification: dict = state["classification"]
    clarification_history: list[dict] = state.get("clarification_history", [])
    clarification_round: int = state.get("clarification_round", 0)
    knowledge_matches: list[dict] = state.get("knowledge_matches", [])
    source_chunks: list[dict] = state.get("source_chunks", [])
    conflict: bool = state.get("conflict", False)

    last_answer = None
    if clarification_history:
        last_answer = clarification_history[-1].get("answer")

    if conflict:
        return RouteDecision(
            route="human", reason="複数の優先資料が矛盾しているため人間判断が必要"
        )

    match = best_knowledge_match(knowledge_matches, settings.knowledge_match_threshold)
    if match is not None:
        return RouteDecision(route="knowledge", reason="承認済み知識で回答可能")

    chunk = best_source_chunk(source_chunks)
    if chunk is not None:
        return RouteDecision(route="knowledge", reason="取り込み済み資料で回答可能")

    escalate_reason = check_escalation(
        classification=classification,
        clarification_history=clarification_history,
        last_answer=last_answer,
    )
    if escalate_reason:
        return RouteDecision(route="human", reason=escalate_reason, escalate_reason=escalate_reason)

    scope = classification.get("scope")
    safe_external = bool(classification.get("safe_for_external_research"))
    category = classification.get("category", "other")

    if scope != "PUBLIC_GENERAL" or not safe_external:
        if clarification_round >= settings.max_clarification_rounds:
            return RouteDecision(
                route="human", reason="確認質問の上限に達したため人間対応が必要"
            )
        asked = [t["question"] for t in clarification_history]
        template = pick_clarification_template(category, asked)
        if template is None:
            return RouteDecision(
                route="human", reason="安全に生成できる確認質問が無いため人間対応が必要"
            )
        question, options, reason = template
        decision = ClarificationDecision(
            needs_clarification=True,
            missing_information=[reason],
            next_question=question,
            options=options,
            reason=reason,
            can_answer_now=False,
            must_escalate=False,
        )
        return RouteDecision(route="clarify", reason=reason, clarification=decision)

    # PUBLIC_GENERAL かつ 外部調査可
    if is_vague_public_question(state.get("question", ""), category):
        if clarification_round >= settings.max_clarification_rounds:
            return RouteDecision(route="research", reason="確認質問の上限に達したため調査へ進む")
        asked = [t["question"] for t in clarification_history]
        template = pick_clarification_template(category, asked)
        if template is not None:
            question, options, reason = template
            decision = ClarificationDecision(
                needs_clarification=True,
                missing_information=[reason],
                next_question=question,
                options=options,
                reason=reason,
                can_answer_now=False,
                must_escalate=False,
            )
            return RouteDecision(route="clarify", reason=reason, clarification=decision)

    return RouteDecision(route="research", reason="公開一般質問のためWeb調査を行う")
