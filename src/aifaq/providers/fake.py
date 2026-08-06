"""テスト専用のフェイク調査プロバイダー。

すべての呼び出しを `calls` に記録するため、テストで
「内部質問はこのプロバイダーへ絶対に渡らない」ことをアサートできる。
"""

from __future__ import annotations

from datetime import UTC, datetime

from aifaq.models import ResearchResult, ResearchSource


class FakeResearchProvider:
    name = "fake"

    def __init__(
        self,
        *,
        answer: str = "これはテスト用の暫定回答です。",
        confidence: float = 0.9,
        sources: list[ResearchSource] | None = None,
        warnings: list[str] | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self._answer = answer
        self._confidence = confidence
        self._sources = sources or [
            ResearchSource(url="https://example.com/doc", title="Example Docs")
        ]
        self._warnings = warnings or []
        self._raise_error = raise_error
        self.calls: list[str] = []

    def research(self, question: str) -> ResearchResult:
        self.calls.append(question)
        if self._raise_error is not None:
            raise self._raise_error
        return ResearchResult(
            answer=self._answer,
            sources=self._sources,
            confidence=self._confidence,
            researched_at=datetime.now(UTC),
            provider=self.name,
            warnings=self._warnings,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)
