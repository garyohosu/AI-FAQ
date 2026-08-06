"""Antigravity CLI (`agy`) を利用した公開情報調査プロバイダー。

呼び出し方式は推測ではなく、次の2つの実地確認に基づく
(instruction-2026-08-06-005 §2.1)。

1. 過去に稼働した実装
   - ``C:/project/werewolf-game/config/agents.json``
     → ``{"command": "agy", "args": ["--print"], "prompt_mode": "arg"}``
   - ``C:/project/OracleCouncil/src/oracle_council/adapters/agy.py``
     → ``agy --print "<prompt>"`` をプロンプトのargv渡しで使用。
2. 本作業時(2026-08-06)の実機確認 (agy 1.1.9)
   - ``agy --version`` → ``1.1.9``
   - ``agy --print "<prompt>" --output-format json`` が終了コード0で
     次のエンベロープを返すことを確認::

         {"conversation_id": "...", "status": "SUCCESS", "response": "<本文>",
          "duration_seconds": 2.5, "num_turns": 1, "usage": {...}}

     OracleCouncil実装時(1.1.5/1.1.6)にはこのエンベロープが無く、stdoutは
     生テキストだった。1.1.9で ``--output-format json`` が追加されたため、
     本実装ではエンベロープを使い、``response`` からアプリJSONを取り出す。
   - Web検索は権限フラグ無しで動作し、出典URL付きの回答が得られることを
     公開質問で確認済み。
   - ``--json-schema <file>`` は受理され、エンベロープにも反映される。
   - ``read_file`` などのツールは、権限フラグ無しのヘッドレス実行では
     自動拒否される::

         a tool required the "read_file" permission that headless mode
         cannot prompt for, so it was auto-denied.

     このため参照渡し(``transport="file"``)は、agy側の
     ``permissions.allow`` 設定または ``--dangerously-skip-permissions``
     が無いと成立しない。既定は実機確認済みの ``transport="arg"`` とし、
     ``file`` は設定で選べるようにしてある(READMEの制約を参照)。

安全上の約束:

- ``shell=False`` と引数配列のみを使う。
- 呼び出し側でセキュリティ判定済みの公開質問しか渡さない
  (``graph.research_with_cli`` は ``route == "research"`` でのみ到達する)。
- stdout/stderrはサイズ制限し、終了コード・タイムアウト・JSON妥当性・
  URL形式をすべて検証する。不明な形式は成功扱いにしない。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from aifaq.config import TRANSPORT_FILE, Settings
from aifaq.models import ResearchResult, ResearchSource
from aifaq.providers.base import (
    ProviderAuthError,
    ProviderNotAvailableError,
    ProviderOutputError,
    ProviderTimeoutError,
)

#: 調査指示の本文。公開情報のみを扱うことを明示する。
_RESEARCH_INSTRUCTIONS = """あなたは社内IT管理部門のFAQ調査アシスタントです。
次の制約を厳守してください。

- 公開情報のみで回答してください。社内固有の事実を推測しないでください。
- 可能な限り一次情報・公式情報を優先してください。
- 重要な主張ごとに出典URLを付けてください。
- 情報の日付を確認してください。
- 不明な場合は「不明」と回答し、confidence を低くしてください。
- 出力は次のJSONオブジェクトのみを1つ返してください。
  前後に説明文を付けないでください。

{"answer": "回答本文(日本語)", "sources": [{"url": "https://...", "title": "..."}],
 "confidence": 0.0から1.0の数値, "warnings": ["注意点があれば"]}"""

#: 参照渡し時に一時ファイルへ書き出すJSON Schema。
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "number"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
                "required": ["title", "url"],
            },
        },
    },
    "required": ["answer", "confidence", "warnings", "sources"],
}

_REQUEST_FILENAME = "request.md"
_SCHEMA_FILENAME = "schema.json"

#: 参照渡し時にargvへ載せる短い指示。質問本文は載せない。
_FILE_REFERENCE_PROMPT = (
    f"Read the file ./{_REQUEST_FILENAME} in the current working directory "
    "using your file reading tool, carry out the research request written in "
    "it, and return only the resulting JSON object."
)

#: 認証・利用ティア起因の失敗を、単なる出力エラーと区別するための目印。
_AUTH_ERROR_MARKERS = (
    "IneligibleTierError",
    "not authenticated",
    "please login",
    "please log in",
    "unauthorized",
    "session limit",
    "quota exceeded",
)

#: ヘッドレスで権限が下りずツールが自動拒否されたときのagyのメッセージ。
_PERMISSION_DENIED_MARKER = "permission that headless mode cannot prompt for"

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

#: Windows の CreateProcess はコマンドラインを約32,767 UTF-16コード単位に
#: 制限する。OracleCouncil が実測で確認した値を踏まえ、余裕を持った上限にする。
_WINDOWS_SAFE_COMMAND_LINE_LIMIT = 30_000

_ALLOWED_URL_SCHEMES = ("http", "https")


def _command_line_utf16_units(cmd: list[str]) -> int:
    """CreateProcess が受け取る実際のコマンドライン長を UTF-16 コード単位で測る。

    ``len(str)`` ではなく UTF-16 で測るのは、CreateProcess の上限が UTF-16
    コード単位で定義されており、BMP外の文字(一部の絵文字やCJK拡張)が
    Python の str では1文字でも実際には2単位を占めるため。
    """
    return len(subprocess.list2cmdline(cmd).encode("utf-16-le")) // 2


def _is_valid_source_url(url: str) -> bool:
    """出典URLとして受け入れられる形式かを検証する。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in _ALLOWED_URL_SCHEMES and bool(parsed.netloc)


class AntigravityProvider:
    """Antigravity CLI (`agy`) 経由で公開情報を調査するプロバイダー。"""

    name = "antigravity"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings.from_env()

    # -- 実行ファイル ------------------------------------------------------

    def _resolve_executable(self) -> str:
        path = shutil.which(self._settings.research_bin)
        if path is None:
            raise ProviderNotAvailableError(
                f"Antigravity CLI executable not found: {self._settings.research_bin!r}"
            )
        return path

    def version(self) -> str:
        """`agy --version` を返す。doctor から利用する。"""
        executable = self._resolve_executable()
        try:
            proc = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                shell=False,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ProviderNotAvailableError(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeoutError("agy --version timed out") from exc
        if proc.returncode != 0:
            raise ProviderNotAvailableError(
                f"agy --version exited with {proc.returncode}: {(proc.stderr or '')[:200]}"
            )
        return (proc.stdout or proc.stderr or "").strip()

    # -- コマンド組み立て --------------------------------------------------

    def _build_command(self, executable: str, prompt: str, workdir: Path | None) -> list[str]:
        cmd = [executable, "--print", prompt, "--output-format", "json"]
        if self._settings.research_model:
            cmd += ["--model", self._settings.research_model]
        if workdir is not None:
            # 参照渡しではスキーマファイルで構造化出力を強制する。
            cmd += ["--json-schema", str(workdir / _SCHEMA_FILENAME)]
        return cmd

    def _run(self, prompt: str, workdir: Path | None) -> str:
        executable = self._resolve_executable()
        cmd = self._build_command(executable, prompt, workdir)

        # 事前チェック: agy は stdin / --prompt-file の受け口を持たないため、
        # プロンプトは必ず argv に載る。プロセス起動前に上限超過を検出する。
        if os.name == "nt":
            units = _command_line_utf16_units(cmd)
            if units > _WINDOWS_SAFE_COMMAND_LINE_LIMIT:
                raise ProviderOutputError(
                    f"agy command line would be {units} UTF-16 code units "
                    f"(safe limit {_WINDOWS_SAFE_COMMAND_LINE_LIMIT}); "
                    "質問または調査指示が長すぎます。"
                )

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._settings.research_timeout_seconds,
                shell=False,
                check=False,
                stdin=subprocess.DEVNULL,
                cwd=str(workdir) if workdir is not None else None,
            )
        except FileNotFoundError as exc:
            # Windows は コマンドライン長超過でも FileNotFoundError
            # (WinError 206) を投げる。実行ファイル不在(WinError 2)と
            # 取り違えないよう分岐する。
            if getattr(exc, "winerror", None) == 206:
                raise ProviderOutputError(
                    "agy command line too long for CreateProcess"
                ) from exc
            raise ProviderNotAvailableError(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeoutError(
                f"agy timed out after {self._settings.research_timeout_seconds}s"
            ) from exc
        except OSError as exc:
            raise ProviderNotAvailableError(str(exc)) from exc

        limit = self._settings.max_output_chars
        stdout = (proc.stdout or "")[:limit]
        stderr = (proc.stderr or "")[:limit]
        combined = f"{stderr}\n{stdout}".lower()

        if any(marker.lower() in combined for marker in _AUTH_ERROR_MARKERS):
            raise ProviderAuthError((stderr or stdout)[:500])

        if _PERMISSION_DENIED_MARKER in combined:
            # agy は終了コード0のまま空応答を返すので、明示的に失敗させる。
            raise ProviderOutputError(
                "agy headless mode auto-denied a required tool permission. "
                "AIFAQ_RESEARCH_TRANSPORT=arg を使うか、agy の settings.json へ "
                "permissions.allow を設定してください。"
            )

        if proc.returncode != 0:
            raise ProviderOutputError(f"agy exited with {proc.returncode}: {stderr[:500]}")

        return stdout

    # -- 出力の取り出しと検証 ----------------------------------------------

    @staticmethod
    def _extract_response_text(stdout: str) -> tuple[str, str]:
        """`--output-format json` のエンベロープから応答本文を取り出す。

        戻り値は ``(応答本文, 部分エラーの説明)``。

        実機(agy 1.1.10, 2026-08-06)で確認した挙動として、調査中に1件でも
        ツール呼び出しが失敗すると、最終回答が完全に得られていても
        ``status`` が ``ERROR`` になり、失敗した内容が ``error`` に入る::

            {"status": "ERROR", "response": "<完全なJSON回答>",
             "error": "Failed to fetch document content at https://..."}

        そのため ``status`` だけで機械的に失敗にすると、実際には使える
        調査結果まで捨ててしまう。ここでは本文が取り出せるかどうかで判断し、
        ``error`` の内容は警告として呼び出し側へ引き渡して人間に見せる。
        本文が空・壊れている場合は従来どおり失敗させる。
        """
        text = stdout.strip()
        if not text:
            raise ProviderOutputError("agy returned empty stdout")

        try:
            envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderOutputError(f"agy envelope is not valid JSON: {exc}") from exc

        if not isinstance(envelope, dict):
            raise ProviderOutputError("agy envelope is not a JSON object")

        status = str(envelope.get("status", "")).upper()
        envelope_error = str(envelope.get("error", "") or "")

        response = envelope.get("response")
        if not isinstance(response, str) or not response.strip():
            detail = f" (status={status!r}, error={envelope_error[:200]!r})" if status else ""
            raise ProviderOutputError(f"agy envelope has no non-empty 'response'{detail}")

        partial_error = ""
        if status and status != "SUCCESS":
            # 本文はあるので捨てないが、部分的な失敗があったことは必ず伝える。
            partial_error = (
                f"Antigravityの調査中に一部の取得に失敗しました"
                f"(status={status}): {envelope_error[:300]}"
                if envelope_error
                else f"Antigravityが status={status} を報告しました"
            )
        return response, partial_error

    @staticmethod
    def _parse_app_json(response_text: str) -> dict:
        """応答本文からアプリ用JSONを取り出す。

        実機で確認できた揺れ(前後の空白、Markdownコードフェンス)のみを
        正規化する。それ以外の未知の形式は推測で救わない。
        """
        candidate = _CODE_FENCE_RE.sub("", response_text.strip()).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ProviderOutputError(f"failed to parse app JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ProviderOutputError("app JSON body is not an object")
        return parsed

    @staticmethod
    def _build_result(
        payload: dict, provider_name: str, extra_warnings: list[str] | None = None
    ) -> ResearchResult:
        raw_sources = payload.get("sources")
        if raw_sources is None:
            raw_sources = []
        if not isinstance(raw_sources, list):
            raise ProviderOutputError("app JSON 'sources' is not a list")

        sources: list[ResearchSource] = []
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not _is_valid_source_url(url):
                # 形式不正のURLは黙って通さず捨てる。出典が0件になれば
                # 呼び出し側が「不十分」と判断し人間回答待ちへ送る。
                continue
            sources.append(ResearchSource(url=url, title=str(item.get("title", ""))))

        raw_warnings = payload.get("warnings") or []
        warnings = (
            [str(w) for w in raw_warnings] if isinstance(raw_warnings, list) else []
        )
        warnings = list(extra_warnings or []) + warnings

        answer = str(payload.get("answer", "")).strip()
        if not answer:
            raise ProviderOutputError("app JSON has an empty 'answer'")

        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise ProviderOutputError(f"app JSON 'confidence' is not a number: {exc}") from exc
        # スキーマ外の値は丸めずに弾く(Pydantic の ge/le に委ねる)。
        try:
            return ResearchResult(
                answer=answer,
                sources=sources,
                confidence=confidence,
                researched_at=datetime.now(UTC),
                provider=provider_name,
                warnings=warnings,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderOutputError(f"app JSON failed schema validation: {exc}") from exc

    # -- 参照渡し ----------------------------------------------------------

    def _write_request_files(self, workdir: Path, question: str) -> None:
        """公開判定済みの調査指示だけを一時ファイルへ書く。

        社内資料本文・DB・認証情報・内部URLなどは一切書き込まない。
        """
        (workdir / _SCHEMA_FILENAME).write_text(
            json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (workdir / _REQUEST_FILENAME).write_text(
            "\n".join(
                [
                    "# 調査依頼",
                    "",
                    "## 質問",
                    "",
                    question,
                    "",
                    "## 指示",
                    "",
                    _RESEARCH_INSTRUCTIONS,
                    "",
                    "## 出力スキーマ",
                    "",
                    f"`{_SCHEMA_FILENAME}` のJSON Schemaに適合するJSONのみを返してください。",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    # -- 公開API -----------------------------------------------------------

    def research(self, question: str) -> ResearchResult:
        if self._settings.research_transport == TRANSPORT_FILE:
            parent = self._settings.research_workdir
            if parent is not None:
                parent.mkdir(parents=True, exist_ok=True)
            # リポジトリ本体から分離した一時ディレクトリを使い、
            # 成否にかかわらず必ず削除する。
            with tempfile.TemporaryDirectory(
                prefix="aifaq-research-",
                dir=str(parent) if parent is not None else None,
                ignore_cleanup_errors=True,
            ) as tmp:
                workdir = Path(tmp)
                self._write_request_files(workdir, question)
                stdout = self._run(_FILE_REFERENCE_PROMPT, workdir)
                response_text, partial_error = self._extract_response_text(stdout)
        else:
            prompt = f"{_RESEARCH_INSTRUCTIONS}\n\n## 調査対象の質問\n\n{question}"
            stdout = self._run(prompt, None)
            response_text, partial_error = self._extract_response_text(stdout)

        payload = self._parse_app_json(response_text)
        return self._build_result(
            payload, self.name, [partial_error] if partial_error else None
        )
