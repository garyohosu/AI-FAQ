"""GeminiCLIProviderのユニットテスト。

実際のGemini CLIやネットワークは一切呼ばない。`subprocess.run` をモックし、
JSON抽出・エラー分類ロジックだけを検証する。
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from aifaq.config import Settings
from aifaq.providers.base import (
    ProviderAuthError,
    ProviderNotAvailableError,
    ProviderOutputError,
    ProviderTimeoutError,
)
from aifaq.providers.gemini_cli import GeminiCLIProvider


@pytest.fixture
def provider(tmp_path):
    settings = Settings(db_path=tmp_path / "aifaq.db", gemini_executable="gemini")
    return GeminiCLIProvider(settings)


def test_extract_app_json_from_plain_wrapped_response():
    stdout = '{"response": "{\\"answer\\": \\"ok\\", \\"sources\\": [], \\"confidence\\": 0.8, \\"warnings\\": []}"}'
    payload = GeminiCLIProvider._extract_app_json(stdout)
    assert payload["answer"] == "ok"


def test_extract_app_json_strips_code_fence():
    stdout = '{"response": "```json\\n{\\"answer\\": \\"x\\", \\"confidence\\": 0.5}\\n```"}'
    payload = GeminiCLIProvider._extract_app_json(stdout)
    assert payload["answer"] == "x"


def test_extract_app_json_raw_json_without_wrapper():
    stdout = '{"answer": "raw", "confidence": 0.9}'
    payload = GeminiCLIProvider._extract_app_json(stdout)
    assert payload["answer"] == "raw"


def test_extract_app_json_invalid_raises_output_error():
    with pytest.raises(ProviderOutputError):
        GeminiCLIProvider._extract_app_json("not json at all")


def test_resolve_executable_missing_raises_not_available(tmp_path):
    settings = Settings(db_path=tmp_path / "aifaq.db", gemini_executable="definitely-not-a-real-binary-xyz")
    p = GeminiCLIProvider(settings)
    with pytest.raises(ProviderNotAvailableError):
        p._resolve_executable()


def test_research_timeout_raises_provider_timeout(provider):
    with patch("aifaq.providers.gemini_cli.shutil.which", return_value="C:/fake/gemini.exe"):
        with patch(
            "aifaq.providers.gemini_cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["gemini"], timeout=1),
        ):
            with pytest.raises(ProviderTimeoutError):
                provider.research("Windowsの再起動方法")


def test_research_auth_error_detected_from_stderr(provider):
    fake_result = MagicMock(
        returncode=1, stdout="", stderr="Error: IneligibleTierError: not supported"
    )
    with patch("aifaq.providers.gemini_cli.shutil.which", return_value="C:/fake/gemini.exe"):
        with patch("aifaq.providers.gemini_cli.subprocess.run", return_value=fake_result):
            with pytest.raises(ProviderAuthError):
                provider.research("Windowsの再起動方法")


def test_research_success_parses_json_and_builds_result(provider):
    payload = (
        '{"answer": "再起動してください", "sources": [{"url": "https://example.com", "title": "t"}], '
        '"confidence": 0.85, "warnings": []}'
    )
    fake_result = MagicMock(returncode=0, stdout=payload, stderr="")
    with patch("aifaq.providers.gemini_cli.shutil.which", return_value="C:/fake/gemini.exe"):
        with patch("aifaq.providers.gemini_cli.subprocess.run", return_value=fake_result):
            result = provider.research("Windowsの再起動方法")
    assert result.answer == "再起動してください"
    assert result.confidence == 0.85
    assert result.provider == "gemini-cli"


def test_research_retries_once_on_parse_failure_then_succeeds(provider):
    bad = MagicMock(returncode=0, stdout="not json", stderr="")
    good = MagicMock(
        returncode=0,
        stdout='{"answer": "ok", "sources": [], "confidence": 0.7, "warnings": []}',
        stderr="",
    )
    with patch("aifaq.providers.gemini_cli.shutil.which", return_value="C:/fake/gemini.exe"):
        with patch("aifaq.providers.gemini_cli.subprocess.run", side_effect=[bad, good]):
            result = provider.research("Windowsの再起動方法")
    assert result.answer == "ok"


def test_research_fails_after_two_bad_parses(provider):
    bad = MagicMock(returncode=0, stdout="not json", stderr="")
    with patch("aifaq.providers.gemini_cli.shutil.which", return_value="C:/fake/gemini.exe"):
        with patch("aifaq.providers.gemini_cli.subprocess.run", side_effect=[bad, bad]):
            with pytest.raises(ProviderOutputError):
                provider.research("Windowsの再起動方法")
