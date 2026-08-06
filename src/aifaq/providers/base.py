"""ResearchProvider の抽象インターフェース。

Gemini CLIへ密結合させず、将来 Claude Code CLI / Codex CLI / ローカルLLM へ
差し替えられるようにする。
"""

from __future__ import annotations

from typing import Protocol

from aifaq.models import ResearchResult


class ResearchProviderError(Exception):
    """調査プロバイダー由来のエラーの基底クラス。"""


class ProviderNotAvailableError(ResearchProviderError):
    """CLI実行ファイルが見つからない、または未導入。"""


class ProviderAuthError(ResearchProviderError):
    """未ログイン、利用制限、認証ティア不適合など。"""


class ProviderTimeoutError(ResearchProviderError):
    """タイムアウト。"""


class ProviderOutputError(ResearchProviderError):
    """JSON破損・スキーマ不一致など出力解析エラー。"""


class ResearchProvider(Protocol):
    """公開情報のWeb調査を行うプロバイダーの共通インターフェース。"""

    name: str

    def research(self, question: str) -> ResearchResult:
        """公開情報のみを用いて質問を調査する。

        内部・機密・個人情報を含む質問をこのメソッドへ渡してはならない
        (呼び出し側でセキュリティ判定を済ませておくこと)。
        """
        ...
