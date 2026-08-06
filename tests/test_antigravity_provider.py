"""AntigravityProvider のテスト。

実際の `agy` は呼ばず、`subprocess.run` を差し替えて、実機で確認した
出力形式(`--output-format json` のエンベロープ)と、その異常系を再現する。
"""

import json
import subprocess

import pytest

from aifaq.config import Settings
from aifaq.providers.antigravity import (
    AntigravityProvider,
    _is_valid_source_url,
)
from aifaq.providers.base import (
    ProviderAuthError,
    ProviderNotAvailableError,
    ProviderOutputError,
    ProviderTimeoutError,
)


def _envelope(response: str, status: str = "SUCCESS") -> str:
    """実機 agy 1.1.9 の `--output-format json` 出力を模す。"""
    return json.dumps(
        {
            "conversation_id": "test-conversation",
            "status": status,
            "response": response,
            "duration_seconds": 1.0,
            "num_turns": 1,
            "usage": {"total_tokens": 100},
        }
    )


APP_JSON = json.dumps(
    {
        "answer": "設定 > ネットワークとインターネット から無効化・有効化します。",
        "confidence": 0.9,
        "warnings": [],
        "sources": [
            {"title": "Microsoft サポート", "url": "https://support.microsoft.com/ja-jp/windows"}
        ],
    },
    ensure_ascii=False,
)


@pytest.fixture
def provider(tmp_path):
    return AntigravityProvider(Settings(db_path=tmp_path / "aifaq.db"))


@pytest.fixture(autouse=True)
def _fake_which(monkeypatch):
    monkeypatch.setattr(
        "aifaq.providers.antigravity.shutil.which", lambda name: f"C:/fake/{name}.exe"
    )


def _patch_run(monkeypatch, *, stdout="", stderr="", returncode=0, capture=None):
    def fake_run(cmd, **kwargs):
        if capture is not None:
            capture.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr("aifaq.providers.antigravity.subprocess.run", fake_run)


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------


def test_research_parses_real_envelope_shape(provider, monkeypatch):
    _patch_run(monkeypatch, stdout=_envelope(APP_JSON))
    result = provider.research("Windows 11でネットワークアダプターを再起動する方法は？")
    assert result.provider == "antigravity"
    assert "ネットワーク" in result.answer
    assert result.confidence == pytest.approx(0.9)
    assert len(result.sources) == 1
    assert result.sources[0].url.startswith("https://support.microsoft.com")


def test_arg_transport_puts_prompt_on_argv(provider, monkeypatch):
    calls: list = []
    _patch_run(monkeypatch, stdout=_envelope(APP_JSON), capture=calls)
    provider.research("公開質問です")
    cmd, kwargs = calls[0]
    assert cmd[1] == "--print"
    assert "公開質問です" in cmd[2]
    assert "--output-format" in cmd and "json" in cmd
    # shell=False と stdin 無効化を必ず守る
    assert kwargs["shell"] is False
    assert kwargs["stdin"] is subprocess.DEVNULL
    # arg 方式では作業ディレクトリを移動しない
    assert kwargs["cwd"] is None


def test_code_fenced_json_is_normalized(provider, monkeypatch):
    _patch_run(monkeypatch, stdout=_envelope(f"```json\n{APP_JSON}\n```"))
    result = provider.research("公開質問です")
    assert result.confidence == pytest.approx(0.9)


def test_model_option_is_forwarded(tmp_path, monkeypatch):
    calls: list = []
    p = AntigravityProvider(
        Settings(db_path=tmp_path / "aifaq.db", research_model="Gemini 3.5 Flash")
    )
    _patch_run(monkeypatch, stdout=_envelope(APP_JSON), capture=calls)
    p.research("公開質問です")
    cmd, _ = calls[0]
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "Gemini 3.5 Flash"


# ---------------------------------------------------------------------------
# 参照渡し (transport=file)
# ---------------------------------------------------------------------------


def test_file_transport_keeps_question_off_argv(tmp_path, monkeypatch):
    """質問本文は argv ではなく一時ファイルへ渡され、実行後に削除される。"""
    calls: list = []
    workdir_parent = tmp_path / "work"
    p = AntigravityProvider(
        Settings(
            db_path=tmp_path / "aifaq.db",
            research_transport="file",
            research_workdir=workdir_parent,
        )
    )

    secret_marker = "この質問文はargvに載ってはいけない"
    seen_request_files: list = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        # 実行時点では request.md が存在し、質問が書かれている
        workdir = __import__("pathlib").Path(kwargs["cwd"])
        request = workdir / "request.md"
        seen_request_files.append(workdir)
        assert request.exists()
        assert secret_marker in request.read_text(encoding="utf-8")
        assert (workdir / "schema.json").exists()
        return subprocess.CompletedProcess(cmd, 0, _envelope(APP_JSON), "")

    monkeypatch.setattr("aifaq.providers.antigravity.subprocess.run", fake_run)
    result = p.research(secret_marker)

    cmd, _ = calls[0]
    assert secret_marker not in " ".join(cmd)
    assert "--json-schema" in cmd
    assert result.confidence == pytest.approx(0.9)
    # 一時ディレクトリは後始末される
    assert not seen_request_files[0].exists()


def test_file_transport_cleans_up_on_failure(tmp_path, monkeypatch):
    workdirs: list = []
    p = AntigravityProvider(
        Settings(
            db_path=tmp_path / "aifaq.db",
            research_transport="file",
            research_workdir=tmp_path / "work",
        )
    )

    def fake_run(cmd, **kwargs):
        workdirs.append(__import__("pathlib").Path(kwargs["cwd"]))
        return subprocess.CompletedProcess(cmd, 1, "", "boom")

    monkeypatch.setattr("aifaq.providers.antigravity.subprocess.run", fake_run)
    with pytest.raises(ProviderOutputError):
        p.research("公開質問です")
    assert not workdirs[0].exists()


# ---------------------------------------------------------------------------
# 異常系
# ---------------------------------------------------------------------------


def test_empty_stdout_is_error(provider, monkeypatch):
    _patch_run(monkeypatch, stdout="")
    with pytest.raises(ProviderOutputError, match="empty stdout"):
        provider.research("公開質問です")


def test_empty_response_field_is_error(provider, monkeypatch):
    _patch_run(monkeypatch, stdout=_envelope(""))
    with pytest.raises(ProviderOutputError, match="no non-empty 'response'"):
        provider.research("公開質問です")


def test_broken_envelope_is_error(provider, monkeypatch):
    _patch_run(monkeypatch, stdout="not json at all")
    with pytest.raises(ProviderOutputError, match="envelope is not valid JSON"):
        provider.research("公開質問です")


def test_partial_tool_failure_keeps_answer_but_warns(provider, monkeypatch):
    """実機挙動: 1件の取得失敗で status=ERROR になっても回答は完全なことがある。

    回答が取り出せる限り捨てず、失敗内容を警告として人間へ伝える。
    """
    envelope = json.dumps(
        {
            "conversation_id": "c",
            "status": "ERROR",
            "response": APP_JSON,
            "error": "Failed to fetch document content at https://support.microsoft.com/x",
        }
    )
    _patch_run(monkeypatch, stdout=envelope)
    result = provider.research("公開質問です")
    assert result.confidence == pytest.approx(0.9)
    assert len(result.sources) == 1
    assert any("一部の取得に失敗" in w for w in result.warnings)
    assert any("Failed to fetch" in w for w in result.warnings)


def test_error_status_without_response_is_error(provider, monkeypatch):
    envelope = json.dumps(
        {"conversation_id": "c", "status": "ERROR", "response": "", "error": "boom"}
    )
    _patch_run(monkeypatch, stdout=envelope)
    with pytest.raises(ProviderOutputError, match="no non-empty 'response'"):
        provider.research("公開質問です")


def test_successful_run_has_no_partial_warning(provider, monkeypatch):
    _patch_run(monkeypatch, stdout=_envelope(APP_JSON))
    result = provider.research("公開質問です")
    assert result.warnings == []


def test_malformed_app_json_is_not_guessed(provider, monkeypatch):
    """実機で観測した崩れたJSON(`{ok: true}`)を成功扱いにしない。"""
    _patch_run(monkeypatch, stdout=_envelope("{answer: 未クオート}"))
    with pytest.raises(ProviderOutputError, match="failed to parse app JSON"):
        provider.research("公開質問です")


def test_empty_answer_is_error(provider, monkeypatch):
    payload = json.dumps({"answer": "  ", "confidence": 0.9, "sources": [], "warnings": []})
    _patch_run(monkeypatch, stdout=_envelope(payload))
    with pytest.raises(ProviderOutputError, match="empty 'answer'"):
        provider.research("公開質問です")


def test_out_of_range_confidence_is_rejected(provider, monkeypatch):
    payload = json.dumps({"answer": "x", "confidence": 5.0, "sources": [], "warnings": []})
    _patch_run(monkeypatch, stdout=_envelope(payload))
    with pytest.raises(ProviderOutputError, match="schema validation"):
        provider.research("公開質問です")


def test_invalid_source_urls_are_dropped(provider, monkeypatch):
    payload = json.dumps(
        {
            "answer": "回答",
            "confidence": 0.8,
            "warnings": [],
            "sources": [
                {"title": "bad scheme", "url": "javascript:alert(1)"},
                {"title": "no netloc", "url": "https://"},
                {"title": "not a url", "url": "ただの文字列"},
                {"title": "good", "url": "https://example.com/doc"},
            ],
        },
        ensure_ascii=False,
    )
    _patch_run(monkeypatch, stdout=_envelope(payload))
    result = provider.research("公開質問です")
    assert [s.url for s in result.sources] == ["https://example.com/doc"]


def test_nonzero_exit_code_is_error(provider, monkeypatch):
    _patch_run(monkeypatch, stdout="", stderr="something failed", returncode=3)
    with pytest.raises(ProviderOutputError, match="exited with 3"):
        provider.research("公開質問です")


def test_timeout_is_reported_as_timeout(provider, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 180)

    monkeypatch.setattr("aifaq.providers.antigravity.subprocess.run", fake_run)
    with pytest.raises(ProviderTimeoutError):
        provider.research("公開質問です")


def test_auth_error_is_distinguished(provider, monkeypatch):
    _patch_run(monkeypatch, stdout="", stderr="IneligibleTierError: not supported", returncode=1)
    with pytest.raises(ProviderAuthError):
        provider.research("公開質問です")


def test_headless_permission_denial_is_error(provider, monkeypatch):
    """agy は権限拒否時に終了コード0で空応答を返すので、明示的に失敗させる。"""
    stderr = (
        'jetski: no output produced - a tool required the "read_file" permission '
        "that headless mode cannot prompt for, so it was auto-denied."
    )
    _patch_run(monkeypatch, stdout=_envelope("dummy"), stderr=stderr, returncode=0)
    with pytest.raises(ProviderOutputError, match="auto-denied"):
        provider.research("公開質問です")


def test_missing_executable_is_not_available(tmp_path, monkeypatch):
    monkeypatch.setattr("aifaq.providers.antigravity.shutil.which", lambda name: None)
    p = AntigravityProvider(Settings(db_path=tmp_path / "aifaq.db"))
    with pytest.raises(ProviderNotAvailableError):
        p.research("公開質問です")


def test_oversized_prompt_is_detected_before_launch(provider, monkeypatch):
    """Windows のコマンドライン上限超過をプロセス起動前に検出する。"""
    monkeypatch.setattr("aifaq.providers.antigravity.os.name", "nt")

    def fake_run(cmd, **kwargs):  # pragma: no cover - 到達しないことを検証する
        raise AssertionError("subprocess must not be launched for an oversized prompt")

    monkeypatch.setattr("aifaq.providers.antigravity.subprocess.run", fake_run)
    with pytest.raises(ProviderOutputError, match="UTF-16 code units"):
        provider.research("あ" * 40_000)


def test_stdout_is_size_limited(tmp_path, monkeypatch):
    p = AntigravityProvider(Settings(db_path=tmp_path / "aifaq.db", max_output_chars=10))
    _patch_run(monkeypatch, stdout=_envelope(APP_JSON))
    # 10文字に切り詰められた結果、エンベロープとして解析できず失敗する
    with pytest.raises(ProviderOutputError, match="envelope is not valid JSON"):
        p.research("公開質問です")


# ---------------------------------------------------------------------------
# version() と補助関数
# ---------------------------------------------------------------------------


def test_version_returns_cli_version(provider, monkeypatch):
    _patch_run(monkeypatch, stdout="1.1.9\n")
    assert provider.version() == "1.1.9"


def test_version_failure_raises(provider, monkeypatch):
    _patch_run(monkeypatch, stdout="", stderr="broken", returncode=1)
    with pytest.raises(ProviderNotAvailableError):
        provider.version()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/a", True),
        ("http://example.com", True),
        ("ftp://example.com", False),
        ("javascript:alert(1)", False),
        ("https://", False),
        ("", False),
    ],
)
def test_source_url_validation(url, expected):
    assert _is_valid_source_url(url) is expected
