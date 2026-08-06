"""Pydantic v2 モデル定義。

外部境界(CLI引数・JSON出力・DB復元・AI CLIの出力)を通過するデータは、
すべてここに定義したモデルで検証する。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class QuestionScope(StrEnum):
    PUBLIC_GENERAL = "PUBLIC_GENERAL"
    INTERNAL = "INTERNAL"
    SECURITY_SENSITIVE = "SECURITY_SENSITIVE"
    PERSONAL_DATA = "PERSONAL_DATA"
    UNKNOWN = "UNKNOWN"


class KnowledgeStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


class KnowledgeSourceType(StrEnum):
    HUMAN = "HUMAN"
    APPROVED_AI = "APPROVED_AI"
    IMPORT = "IMPORT"


class AnswerType(StrEnum):
    KNOWLEDGE = "KNOWLEDGE"
    INTERNET_RESEARCH = "INTERNET_RESEARCH"
    PENDING_HUMAN = "PENDING_HUMAN"
    REFUSED = "REFUSED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


class PendingStatus(StrEnum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    CANCELLED = "CANCELLED"


class SourceScope(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class SourcePriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    AUTHORITATIVE = "authoritative"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    DRAFT = "draft"
    STALE = "stale"
    RETIRED = "retired"
    FORBIDDEN = "forbidden"
    MISSING = "missing"


class MemoryType(StrEnum):
    SOURCE_MAP = "SOURCE_MAP"
    TERMINOLOGY = "TERMINOLOGY"
    ROUTING_RULE = "ROUTING_RULE"
    DECISION = "DECISION"
    GAP = "GAP"
    FORBIDDEN_SOURCE = "FORBIDDEN_SOURCE"


class LocationType(StrEnum):
    SHEET_ROWS = "SHEET_ROWS"
    MARKDOWN_HEADING = "MARKDOWN_HEADING"
    TEXT_BLOCK = "TEXT_BLOCK"
    CSV_ROWS = "CSV_ROWS"


# ---------------------------------------------------------------------------
# Clarification (instruction 002)
# ---------------------------------------------------------------------------


class ClarificationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int = Field(ge=1, le=3)
    question: str = Field(min_length=1)
    answer: str | None = None
    options: list[str] = Field(default_factory=list)
    reason: str = ""
    asked_at: datetime
    answered_at: datetime | None = None

    @field_validator("options")
    @classmethod
    def _validate_options(cls, v: list[str]) -> list[str]:
        if len(v) == 1 or len(v) > 4:
            raise ValueError("options must contain 0 or 2-4 items")
        return v


class ClarificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    needs_clarification: bool
    missing_information: list[str] = Field(default_factory=list)
    next_question: str | None = None
    options: list[str] = Field(default_factory=list)
    reason: str = ""
    can_answer_now: bool = False
    must_escalate: bool = False

    @field_validator("options")
    @classmethod
    def _validate_options(cls, v: list[str]) -> list[str]:
        if len(v) == 1 or len(v) > 4:
            raise ValueError("options must contain 0 or 2-4 items")
        return v

    @model_validator(mode="after")
    def _validate_next_question(self) -> "ClarificationDecision":
        if self.needs_clarification:
            if not self.next_question or not self.next_question.strip():
                raise ValueError(
                    "next_question is required when needs_clarification=True"
                )
        return self


# ---------------------------------------------------------------------------
# Question / classification
# ---------------------------------------------------------------------------


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    original_question: str = Field(min_length=1)
    requester: str | None = None
    thread_id: str = Field(min_length=1)
    created_at: datetime
    clarification_round: int = Field(default=0, ge=0, le=3)
    clarification_history: list[ClarificationTurn] = Field(default_factory=list)


class QuestionClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: QuestionScope
    category: str = "other"
    reason: str = ""
    safe_for_external_research: bool = False

    @model_validator(mode="after")
    def _consistency(self) -> "QuestionClassification":
        if self.scope != QuestionScope.PUBLIC_GENERAL and self.safe_for_external_research:
            raise ValueError(
                "safe_for_external_research can only be true for PUBLIC_GENERAL scope"
            )
        return self


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------


class KnowledgeArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    canonical_question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    category: str = "other"
    tags: list[str] = Field(default_factory=list)
    status: KnowledgeStatus = KnowledgeStatus.DRAFT
    source_type: KnowledgeSourceType = KnowledgeSourceType.HUMAN
    version: int = Field(default=1, ge=1)
    approved_by: str | None = None
    created_at: datetime
    updated_at: datetime
    valid_until: datetime | None = None
    source_urls: list[str] = Field(default_factory=list)


class KnowledgeMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_id: int
    score: float = Field(ge=0.0)
    canonical_question: str
    answer: str
    version: int
    is_stale: bool = False


# ---------------------------------------------------------------------------
# Research (Gemini CLI)
# ---------------------------------------------------------------------------


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    title: str = ""
    retrieved_at: datetime | None = None


class ResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    sources: list[ResearchSource] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    researched_at: datetime
    provider: str
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Answer
# ---------------------------------------------------------------------------


class FAQAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    answer_type: AnswerType
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    knowledge_ids: list[int] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    thread_id: str
    notice: str | None = None

    # NEEDS_CLARIFICATION 用の追加フィールド
    clarification_round: int | None = None
    question: str | None = None
    options: list[str] = Field(default_factory=list)
    remaining_rounds: int | None = None

    # PENDING_HUMAN 用
    pending_id: int | None = None

    @model_validator(mode="after")
    def _clarification_fields_required(self) -> "FAQAnswer":
        if self.answer_type == AnswerType.NEEDS_CLARIFICATION:
            if self.clarification_round is None or not self.question:
                raise ValueError(
                    "clarification_round and question are required for "
                    "NEEDS_CLARIFICATION answers"
                )
        return self


# ---------------------------------------------------------------------------
# Thread status (instruction 006 §4.1 / §5)
# ---------------------------------------------------------------------------


class ThreadState(StrEnum):
    """`aifaq status` / `aifaq watch` が返すスレッドの状態。

    `PendingStatus` は pending_questions 行の状態だけを表すのに対し、
    こちらは「質問者から見たスレッド全体の状態」を表す。
    """

    #: 確認質問に回答待ち(質問者のアクション待ち)
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    #: IT管理者の回答待ち
    PENDING_HUMAN = "PENDING_HUMAN"
    #: IT管理者が回答済み
    ANSWERED = "ANSWERED"
    #: 取り下げ
    CANCELLED = "CANCELLED"
    #: 人間引き継ぎを経ずにAI・知識で完了した
    COMPLETED = "COMPLETED"
    #: 該当するスレッド・pendingが無い
    NOT_FOUND = "NOT_FOUND"


class ClarificationSummary(BaseModel):
    """`status` 出力用の確認質問履歴の要約。"""

    model_config = ConfigDict(extra="forbid")

    round_no: int
    question: str
    answer: str | None = None


class ThreadStatus(BaseModel):
    """CLIの状態表示用レスポンス。

    CLIが複数テーブルを場当たり的に読むのではなく、リポジトリ層が
    このモデルを組み立てて返す (instruction-006 §5)。
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    state: ThreadState
    pending_id: int | None = None
    original_question: str | None = None
    #: 現在の状態に対応する回答本文(人間回答またはAI/知識の回答)
    answer: str | None = None
    #: 回答の種別 (HUMAN / KNOWLEDGE / INTERNET_RESEARCH など)
    answer_type: str | None = None
    answered_by: str | None = None
    created_at: datetime | None = None
    answered_at: datetime | None = None
    delivery_status: str | None = None
    delivered_at: datetime | None = None
    #: 人間回答が承認済み知識になった場合のID
    knowledge_id: int | None = None
    sources: list[ResearchSource] = Field(default_factory=list)
    clarifications: list[ClarificationSummary] = Field(default_factory=list)
    #: 確認質問待ちのときに、質問者へ示す次の質問
    next_question: str | None = None
    options: list[str] = Field(default_factory=list)

    @property
    def is_final(self) -> bool:
        """`watch` がこれ以上待つ必要がない状態か。"""
        return self.state in (
            ThreadState.ANSWERED,
            ThreadState.CANCELLED,
            ThreadState.COMPLETED,
        )


# ---------------------------------------------------------------------------
# Source ingestion (instruction 003)
# ---------------------------------------------------------------------------


def _validate_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("~"):
        raise ValueError("absolute paths are not allowed")
    if ":" in normalized:
        raise ValueError("drive-letter / UNC paths are not allowed")
    if normalized.startswith("//") or normalized.startswith("\\\\"):
        raise ValueError("UNC paths are not allowed")
    parts = normalized.split("/")
    if any(p == ".." for p in parts):
        raise ValueError("path traversal ('..') is not allowed")
    return normalized


class SourceFileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    relative_path: str
    file_type: str
    scope: SourceScope
    category: str = "other"
    owner: str | None = None
    priority: SourcePriority = SourcePriority.NORMAL
    status: SourceStatus = SourceStatus.ACTIVE
    size_bytes: int = Field(ge=0)
    modified_at: datetime
    sha256: str = Field(min_length=64, max_length=64)
    last_imported_at: datetime | None = None
    last_reviewed_at: datetime | None = None
    valid_until: datetime | None = None
    error_summary: str | None = None

    @field_validator("relative_path")
    @classmethod
    def _check_path(cls, v: str) -> str:
        return _validate_relative_path(v)


class SourceChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    source_file_id: int
    location_type: LocationType
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    heading: str | None = None
    content: str = Field(min_length=1)
    content_hash: str
    is_active: bool = True


class SourceIndexEntry(BaseModel):
    """MEMORY.md の Source Map 1エントリに対応する。"""

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1)
    path: str
    contains: str
    sheets: list[str] = Field(default_factory=list)
    scope: SourceScope
    category: str = "other"
    owner: str | None = None
    priority: SourcePriority = SourcePriority.NORMAL
    status: SourceStatus = SourceStatus.ACTIVE
    last_reviewed: str | None = None
    valid_until: str | None = None
    notes: str = ""

    @field_validator("path")
    @classmethod
    def _check_path(cls, v: str) -> str:
        return _validate_relative_path(v)


class ImportWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    message: str
    severity: str = "warning"


class ImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processed: int = 0
    imported: int = 0
    skipped_unchanged: int = 0
    failed: int = 0
    warnings: list[ImportWarning] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    memory_type: MemoryType
    key: str
    value: str
    source_markdown_path: str
    heading: str | None = None
    status: str = "active"
    approved_by: str | None = None
    updated_at: datetime


class KnowledgeCitation(BaseModel):
    """回答に付与する出典情報。"""

    model_config = ConfigDict(extra="forbid")

    knowledge_id: int | None = None
    source_file_id: int | None = None
    relative_path: str | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    heading: str | None = None
    url: str | None = None

    def display(self) -> str:
        if self.relative_path:
            parts = [self.relative_path]
            if self.sheet_name:
                parts.append(f'シート「{self.sheet_name}」')
            elif self.heading:
                # Markdown/TXTでは見出しがあると本文中の位置を追いやすい。
                parts.append(f"見出し「{self.heading}」")
            if self.row_start is not None and self.row_end is not None:
                parts.append(f"行{self.row_start}-{self.row_end}")
            return "参照: " + " / ".join(parts)
        if self.knowledge_id is not None:
            return f"参照: KB-{self.knowledge_id}"
        if self.url:
            return f"出典: {self.url}"
        return "出典不明"
