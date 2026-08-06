"""Gemini CLI (ヘッドレスモード) を利用した公開情報調査プロバイダー。

- クラウドAPI SDKを直接使わず `gemini` CLIをサブプロセスとして呼び出す。
- `shell=False` / 引数配列 / 標準入力でのプロンプト受け渡しを守る。
- CLI未導入・未ログイン(利用制限)・タイムアウト・JSON破損を別エラーとして扱う。

実装時点(2026-08-06)でのローカル `gemini --help` に基づき、以下を確認済み:

- 非対話モードは `-p/--prompt` で有効化し、値は追加のstdin入力へ連結される
  (`--prompt`: "Appended to input on stdin (if any)")。
- `-o/--output-format json` でJSON出力が選べる。
- 信頼されていないディレクトリでは `--skip-trust` (または
  `GEMINI_CLI_TRUST_WORKSPACE=true`) が無いと非対話実行が失敗する
  (https://geminicli.com/docs/cli/trusted-folders/#headless-and-automated-environments)。
- `--approval-mode plan` は読み取り専用モードで、Web検索など非破壊的な
  ツールのみを想定し、ファイル書換・シェル実行を伴うツールは実行できない。

このプロジェクトでは `--yolo` を使用しない。実際の認証済み環境では、この
モジュールの成功パスの出力形式を実データで再確認すること
(本環境ではアカウントの利用ティア制限により実呼び出しの成功を確認できていない。
詳細は result-2026-08-06-004.md を参照)。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime

from aifaq.config import Settings
from aifaq.models import ResearchResult, ResearchSource
from aifaq.providers.base import (
    ProviderAuthError,
    ProviderNotAvailableError,
    ProviderOutputError,
    ProviderTimeoutError,
)

_SYSTEM_PROMPT = """あなたは社内IT管理部門のFAQ調査アシスタントです。
次の制約を厳守してください。

- 公開情報のみで回答してください。社内固有の事実を推測しないでください。
- 可能な限り一次情報・公式情報を優先してください。
- 重要な主張ごとに出典URLを付けてください。
- 情報の日付を確認してください。
- 不明な場合は「不明」と回答してください。
- 出力は次のJSONオブジェクトのみを、コードフェンス無しで1つ返してください。
  他の説明文を前後に含めないでください。

{"answer": "回答本文(日本語)", "sources": [{"url": "https://...", "title": "..."}],
 "confidence": 0.0から1.0の数値, "warnings": ["注意点があれば"]}

調査対象の質問は標準入力から渡します。"""

_RETRY_PROMPT_SUFFIX = """

前回の出力はJSONとして解析できませんでした。
説明文を含めず、指定されたJSONオブジェクトのみを出力し直してください。"""

_AUTH_ERROR_MARKERS = (
    "IneligibleTierError",
    "not authenticated",
    "please login",
    "please migrate",
    "unauthorized",
    "401",
)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class GeminiCLIProvider:
    name = "gemini-cli"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings.from_env()

    def _resolve_executable(self) -> str:
        path = shutil.which(self._settings.gemini_executable)
        if path is None:
            raise ProviderNotAvailableError(
                f"Gemini CLI executable not found: {self._settings.gemini_executable!r}"
            )
        return path

    def _run(self, prompt_prefix: str, question: str) -> str:
        executable = self._resolve_executable()
        args = [
            executable,
            "-p",
            prompt_prefix,
            "-o",
            "json",
            "--approval-mode",
            "plan",
            "--skip-trust",
        ]
        try:
            proc = subprocess.run(
                args,
                input=question,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=self._settings.research_timeout_seconds,
                shell=False,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProviderNotAvailableError(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeoutError(
                f"gemini CLI timed out after {self._settings.research_timeout_seconds}s"
            ) from exc

        stdout = (proc.stdout or "")[: self._settings.max_output_chars]
        stderr = (proc.stderr or "")[: self._settings.max_output_chars]

        if any(marker.lower() in stderr.lower() for marker in _AUTH_ERROR_MARKERS):
            raise ProviderAuthError(stderr[:500])

        if proc.returncode != 0:
            raise ProviderOutputError(
                f"gemini CLI exited with {proc.returncode}: {stderr[:500]}"
            )

        return stdout

    @staticmethod
    def _extract_app_json(stdout: str) -> dict:
        """CLI全体のJSON包みから、アプリ用JSON本文を取り出す。"""
        stdout = stdout.strip()
        outer_text = stdout
        try:
            outer = json.loads(stdout)
        except json.JSONDecodeError:
            outer = None

        if isinstance(outer, dict):
            for key in ("response", "text", "output", "message", "result"):
                value = outer.get(key)
                if isinstance(value, str) and value.strip():
                    outer_text = value
                    break
                if isinstance(value, dict):
                    return value

        candidate = _CODE_FENCE_RE.sub("", outer_text).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ProviderOutputError(f"failed to parse app JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ProviderOutputError("app JSON body is not an object")
        return parsed

    def research(self, question: str) -> ResearchResult:
        stdout = self._run(_SYSTEM_PROMPT, question)
        try:
            payload = self._extract_app_json(stdout)
        except ProviderOutputError:
            stdout = self._run(_SYSTEM_PROMPT + _RETRY_PROMPT_SUFFIX, question)
            payload = self._extract_app_json(stdout)

        sources = [
            ResearchSource(url=s.get("url", ""), title=s.get("title", ""))
            for s in payload.get("sources", [])
            if isinstance(s, dict) and s.get("url")
        ]

        try:
            return ResearchResult(
                answer=str(payload.get("answer", "")).strip() or "不明",
                sources=sources,
                confidence=float(payload.get("confidence", 0.0)),
                researched_at=datetime.now(UTC),
                provider=self.name,
                warnings=[str(w) for w in payload.get("warnings", [])],
            )
        except (TypeError, ValueError) as exc:
            raise ProviderOutputError(f"app JSON failed schema validation: {exc}") from exc
