"""LangGraphによる回答フロー。

ノードは判定ロジックを持たず、`routing.py` の純粋関数を呼び出すだけにする。
人間回答待ち・確認質問は `interrupt()` で一時停止し、`thread_id` を使って
別プロセス(別CLI呼び出し)から再開できる。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from aifaq import routing, security
from aifaq.config import Settings
from aifaq.models import AnswerType, FAQAnswer, KnowledgeCitation, ResearchSource
from aifaq.providers.base import ResearchProviderError
from aifaq.repositories import Repositories
from aifaq.util import now_iso, similarity

logger = logging.getLogger("aifaq.graph")


class FAQState(TypedDict, total=False):
    thread_id: str
    requester: str | None
    original_question: str
    question: str
    classification: dict
    clarification_round: int
    clarification_history: list[dict]
    knowledge_matches: list[dict]
    source_chunks: list[dict]
    conflict: bool
    route: str
    route_reason: str
    pending_clarification: dict
    last_clarification_answer: str
    research_result: dict
    research_sufficient: bool
    answer_type: str
    answer_text: str
    answer_confidence: float
    answer_sources: list[dict]
    answer_knowledge_ids: list[int]
    answer_notice: str | None
    citation: dict | None
    pending_id: int


class ReplyError(ValueError):
    """`reply` を受理できない場合のエラー。"""


def _detect_conflict(source_chunks: list[dict]) -> bool:
    authoritative = [c for c in source_chunks if c.get("priority") == "authoritative"]
    file_ids = {c["source_file_id"] for c in authoritative}
    if len(file_ids) < 2:
        return False
    texts = [c["content"] for c in authoritative[:2]]
    return similarity(texts[0], texts[1]) < 0.5


def build_graph(
    repos: Repositories, settings: Settings, provider: Any
) -> StateGraph:
    g: StateGraph = StateGraph(FAQState)

    def validate_question(state: FAQState) -> dict:
        return {"question": state["original_question"].strip()}

    def security_precheck(state: FAQState) -> dict:
        combined = state["original_question"]
        for turn in state.get("clarification_history", []):
            combined += "\n" + turn.get("question", "")
            if turn.get("answer"):
                combined += "\n" + turn["answer"]
        classification = security.classify_question(combined)
        return {"classification": classification.model_dump(mode="json")}

    def load_memory_context(state: FAQState) -> dict:
        # instruction-003: 用語・所在・禁止資料を検索前に確認する。
        # MVPでは件数把握のみに留め、詳細な文脈注入はCLIの `memory show` に委ねる。
        return {}

    def search_approved_knowledge(state: FAQState) -> dict:
        matches = repos.knowledge.search(state["question"], limit=5)
        return {"knowledge_matches": [m.model_dump(mode="json") for m in matches]}

    def search_imported_sources(state: FAQState) -> dict:
        rows = repos.source_chunks.search(state["question"], limit=5)
        return {"source_chunks": [dict(r) for r in rows]}

    def check_source_conflicts(state: FAQState) -> dict:
        return {"conflict": _detect_conflict(state.get("source_chunks", []))}

    def assess_answerability(state: FAQState) -> dict:
        decision = routing.decide_route(state, settings)
        update: dict = {"route": decision.route, "route_reason": decision.reason}
        if decision.clarification is not None:
            update["pending_clarification"] = decision.clarification.model_dump(mode="json")
        return update

    def compose_knowledge_answer(state: FAQState) -> dict:
        match = routing.best_knowledge_match(
            state.get("knowledge_matches", []), settings.knowledge_match_threshold
        )
        if match is not None:
            notice = (
                "この回答は有効期限が切れている可能性があります。"
                "IT管理者へ確認してください。"
                if match.get("is_stale")
                else None
            )
            return {
                "answer_type": "KNOWLEDGE",
                "answer_text": match["answer"],
                "answer_confidence": 1.0,
                "answer_knowledge_ids": [match["knowledge_id"]],
                "answer_notice": notice,
                "citation": {"knowledge_id": match["knowledge_id"]},
            }
        chunk = routing.best_source_chunk(state.get("source_chunks", []))
        notice = (
            "この資料は古い可能性があります(status=stale)。IT管理者へ確認してください。"
            if chunk.get("file_status") == "stale"
            else None
        )
        return {
            "answer_type": "KNOWLEDGE",
            "answer_text": chunk["content"],
            "answer_confidence": 0.8,
            "answer_knowledge_ids": [],
            "answer_notice": notice,
            "citation": {
                "source_file_id": chunk["source_file_id"],
                "relative_path": chunk["relative_path"],
                "sheet_name": chunk.get("sheet_name"),
                "row_start": chunk.get("row_start"),
                "row_end": chunk.get("row_end"),
                "heading": chunk.get("heading"),
            },
        }

    def create_clarification_question(state: FAQState) -> dict:
        pc = dict(state["pending_clarification"])
        round_no = state.get("clarification_round", 0) + 1
        repos.clarifications.create_turn(
            thread_id=state["thread_id"],
            round_no=round_no,
            question=pc["next_question"],
            options=pc["options"],
            reason=pc["reason"],
        )
        pc["round_no"] = round_no
        return {"pending_clarification": pc}

    def ask_requester(state: FAQState) -> dict:
        pc = state["pending_clarification"]
        answer = interrupt(
            {
                "type": "clarification",
                "round_no": pc["round_no"],
                "question": pc["next_question"],
                "options": pc["options"],
            }
        )
        return {"last_clarification_answer": answer}

    def merge_clarification(state: FAQState) -> dict:
        pc = state["pending_clarification"]
        raw_answer = state["last_clarification_answer"]
        safe_answer = (
            security.redact_for_storage(raw_answer)
            if security.contains_secret_like(raw_answer)
            else raw_answer
        )
        repos.clarifications.answer_open_turn(state["thread_id"], safe_answer)
        history = list(state.get("clarification_history", []))
        history.append(
            {
                "round_no": pc["round_no"],
                "question": pc["next_question"],
                "answer": safe_answer,
                "options": pc["options"],
                "reason": pc["reason"],
                "asked_at": now_iso(),
                "answered_at": now_iso(),
            }
        )
        return {
            "clarification_history": history,
            "clarification_round": pc["round_no"],
            "question": (state["original_question"] + " " + safe_answer).strip(),
        }

    def research_with_cli(state: FAQState) -> dict:
        try:
            result = provider.research(state["question"])
        except ResearchProviderError as exc:
            repos.research_runs.record(
                thread_id=state["thread_id"],
                question=state["question"],
                provider=getattr(provider, "name", "unknown"),
                status="error",
                confidence=None,
                sources=[],
                warnings=[],
                error_summary=str(exc)[:500],
            )
            return {"research_result": None, "research_sufficient": False}

        repos.research_runs.record(
            thread_id=state["thread_id"],
            question=state["question"],
            provider=result.provider,
            status="ok",
            confidence=result.confidence,
            sources=[s.model_dump(mode="json") for s in result.sources],
            warnings=result.warnings,
            # 人間承認(`aifaq research approve`)で元のAI回答を参照できるよう
            # 回答本文も保存する。承認するまでナレッジには昇格しない。
            answer=result.answer,
        )
        sufficient = (
            result.confidence >= settings.research_confidence_threshold
            and len(result.sources) > 0
        )
        return {
            "research_result": result.model_dump(mode="json"),
            "research_sufficient": sufficient,
        }

    def compose_research_answer(state: FAQState) -> dict:
        rr = state["research_result"]
        notice = "社内固有ルールはIT管理者へ確認してください。"
        if rr.get("warnings"):
            notice += " " + " / ".join(rr["warnings"])
        return {
            "answer_type": "INTERNET_RESEARCH",
            "answer_text": rr["answer"],
            "answer_confidence": rr["confidence"],
            "answer_sources": rr["sources"],
            "answer_knowledge_ids": [],
            "answer_notice": notice,
            "citation": None,
        }

    def record_history(state: FAQState) -> dict:
        repos.history.record(
            thread_id=state["thread_id"],
            question=state["original_question"],
            classification=state["classification"],
            route=state["route"],
            answer_type=state["answer_type"],
            knowledge_ids=state.get("answer_knowledge_ids", []),
        )
        return {}

    def request_human(state: FAQState) -> dict:
        existing = repos.pending.get_open_by_thread(state["thread_id"])
        if existing is None:
            pending_id = repos.pending.create(
                thread_id=state["thread_id"],
                question=state["question"],
                original_question=state["original_question"],
                classification=state["classification"],
            )
        else:
            pending_id = int(existing["id"])
        repos.history.record(
            thread_id=state["thread_id"],
            question=state["original_question"],
            classification=state["classification"],
            route="human",
            answer_type="PENDING_HUMAN",
            knowledge_ids=[],
        )
        interrupt(
            {
                "type": "pending_human",
                "pending_id": pending_id,
                "reason": state.get("route_reason", ""),
            }
        )
        return {"answer_type": "PENDING_HUMAN", "pending_id": pending_id}

    g.add_node("validate_question", validate_question)
    g.add_node("security_precheck", security_precheck)
    g.add_node("load_memory_context", load_memory_context)
    g.add_node("search_approved_knowledge", search_approved_knowledge)
    g.add_node("search_imported_sources", search_imported_sources)
    g.add_node("check_source_conflicts", check_source_conflicts)
    g.add_node("assess_answerability", assess_answerability)
    g.add_node("compose_knowledge_answer", compose_knowledge_answer)
    g.add_node("create_clarification_question", create_clarification_question)
    g.add_node("ask_requester", ask_requester)
    g.add_node("merge_clarification", merge_clarification)
    g.add_node("research_with_cli", research_with_cli)
    g.add_node("compose_research_answer", compose_research_answer)
    g.add_node("record_history", record_history)
    g.add_node("request_human", request_human)

    g.add_edge(START, "validate_question")
    g.add_edge("validate_question", "security_precheck")
    g.add_edge("security_precheck", "load_memory_context")
    g.add_edge("load_memory_context", "search_approved_knowledge")
    g.add_edge("search_approved_knowledge", "search_imported_sources")
    g.add_edge("search_imported_sources", "check_source_conflicts")
    g.add_edge("check_source_conflicts", "assess_answerability")

    g.add_conditional_edges(
        "assess_answerability",
        lambda s: s["route"],
        {
            "knowledge": "compose_knowledge_answer",
            "clarify": "create_clarification_question",
            "research": "research_with_cli",
            "human": "request_human",
        },
    )
    g.add_edge("compose_knowledge_answer", "record_history")
    g.add_edge("record_history", END)

    g.add_edge("create_clarification_question", "ask_requester")
    g.add_edge("ask_requester", "merge_clarification")
    g.add_edge("merge_clarification", "security_precheck")

    g.add_conditional_edges(
        "research_with_cli",
        lambda s: "ok" if s.get("research_sufficient") else "insufficient",
        {"ok": "compose_research_answer", "insufficient": "request_human"},
    )
    g.add_edge("compose_research_answer", "record_history")
    g.add_edge("request_human", END)

    return g


def _citation_notice(state_citation: dict | None, extra_notice: str | None) -> str | None:
    parts = []
    if extra_notice:
        parts.append(extra_notice)
    if state_citation:
        parts.append(KnowledgeCitation(**state_citation).display())
    return " / ".join(parts) if parts else None


def _answer_from_interrupt(interrupt_value: dict, thread_id: str, max_rounds: int) -> FAQAnswer:
    if interrupt_value["type"] == "clarification":
        round_no = interrupt_value["round_no"]
        return FAQAnswer(
            answer="",
            answer_type=AnswerType.NEEDS_CLARIFICATION,
            thread_id=thread_id,
            clarification_round=round_no,
            question=interrupt_value["question"],
            options=interrupt_value["options"],
            remaining_rounds=max(max_rounds - round_no, 0),
        )
    if interrupt_value["type"] == "pending_human":
        return FAQAnswer(
            answer=(
                "この質問は社内固有情報を含むか、十分な根拠を確認できませんでした。"
                "IT管理者の回答をお待ちください。"
            ),
            answer_type=AnswerType.PENDING_HUMAN,
            thread_id=thread_id,
            pending_id=interrupt_value["pending_id"],
            notice=interrupt_value.get("reason") or None,
        )
    raise AssertionError(f"unknown interrupt payload type: {interrupt_value}")


def _answer_from_final_state(state: dict, thread_id: str) -> FAQAnswer:
    answer_type = state["answer_type"]
    citation_notice = _citation_notice(state.get("citation"), state.get("answer_notice"))
    if answer_type == "KNOWLEDGE":
        return FAQAnswer(
            answer=state["answer_text"],
            answer_type=AnswerType.KNOWLEDGE,
            thread_id=thread_id,
            confidence=state.get("answer_confidence", 1.0),
            knowledge_ids=state.get("answer_knowledge_ids", []),
            notice=citation_notice,
        )
    if answer_type == "INTERNET_RESEARCH":
        sources = [ResearchSource(**s) for s in state.get("answer_sources", [])]
        return FAQAnswer(
            answer=state["answer_text"],
            answer_type=AnswerType.INTERNET_RESEARCH,
            thread_id=thread_id,
            confidence=state.get("answer_confidence", 0.0),
            sources=sources,
            notice=citation_notice,
        )
    raise AssertionError(f"unexpected terminal answer_type: {answer_type}")


@contextlib.contextmanager
def open_checkpointer(settings: Settings):
    with SqliteSaver.from_conn_string(str(settings.db_path)) as saver:
        yield saver


def run_ask(
    repos: Repositories,
    settings: Settings,
    provider: Any,
    *,
    thread_id: str,
    question: str,
    requester: str | None,
) -> FAQAnswer:
    graph = build_graph(repos, settings, provider)
    with open_checkpointer(settings) as saver:
        compiled = graph.compile(checkpointer=saver)
        initial_state: FAQState = {
            "thread_id": thread_id,
            "requester": requester,
            "original_question": question,
            "question": question,
            "classification": {},
            "clarification_round": 0,
            "clarification_history": [],
            "knowledge_matches": [],
            "source_chunks": [],
            "conflict": False,
        }
        cfg = {"configurable": {"thread_id": thread_id}}
        result = compiled.invoke(initial_state, config=cfg)
        interrupts = result.get("__interrupt__")
        if interrupts:
            return _answer_from_interrupt(
                interrupts[0].value, thread_id, settings.max_clarification_rounds
            )
        return _answer_from_final_state(result, thread_id)


def run_reply(
    repos: Repositories,
    settings: Settings,
    provider: Any,
    *,
    thread_id: str,
    answer_text: str,
) -> FAQAnswer:
    open_turn = repos.clarifications.get_open_turn(thread_id)
    if open_turn is None:
        raise ReplyError(
            f"thread_id={thread_id!r} に未回答の確認質問が見つかりません"
            "(存在しない・複数存在・既に回答済みのいずれかです)"
        )

    graph = build_graph(repos, settings, provider)
    with open_checkpointer(settings) as saver:
        compiled = graph.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": thread_id}}
        result = compiled.invoke(Command(resume=answer_text), config=cfg)
        interrupts = result.get("__interrupt__")
        if interrupts:
            return _answer_from_interrupt(
                interrupts[0].value, thread_id, settings.max_clarification_rounds
            )
        return _answer_from_final_state(result, thread_id)
