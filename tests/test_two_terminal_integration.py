"""2ターミナル運用の統合テスト (instruction-006 §9.2)。

質問者CLIとIT管理者CLIを**別プロセス**として起動し、
`watch` が別プロセスの回答を検知して終了することを確認する。

同一プロセス内のテスト(`test_human_answer_loop.py`)では、
SQLiteを別プロセスから共有したときのロック挙動を確認できないため、
実際に `subprocess` を使う。
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def workspace(tmp_path):
    """別プロセスのCLIが動く作業ディレクトリを用意する。"""
    (tmp_path / "knowledge" / "public").mkdir(parents=True)
    (tmp_path / "MEMORY.md").write_text(
        "# AI-FAQ Memory Index\n\n"
        "## Source Map\n\n## Terminology\n\n## Routing Rules\n\n"
        "## Important Decisions\n\n## Known Gaps\n\n## Retired or Forbidden Sources\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["AIFAQ_DB_PATH"] = str(tmp_path / "data" / "aifaq.db")
    env["PYTHONPATH"] = REPO_SRC
    env["PYTHONIOENCODING"] = "utf-8"
    # 実AI CLIを絶対に呼ばない
    env["AIFAQ_RESEARCH_PROVIDER"] = "fake"
    return tmp_path, env


def run_cli(workspace, args, **kw):
    cwd, env = workspace
    return subprocess.run(
        [sys.executable, "-m", "aifaq", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, **kw
    )


def make_handoff(workspace, thread_id):
    """人間引き継ぎを作り、pending_id を返す。"""
    run_cli(workspace, ["init"])
    run_cli(workspace, ["ask", "社内VPNのパスワードを教えてください",
                        "--thread-id", thread_id])
    result = run_cli(workspace, ["--json", "status", thread_id])
    return json.loads(result.stdout)["pending_id"]


def test_watch_detects_answer_from_another_process(workspace):
    """watch起動中に別プロセスから回答すると、watchが検知して終了する。"""
    cwd, env = workspace
    thread_id = "integration-001"
    pending_id = make_handoff(workspace, thread_id)
    assert pending_id is not None

    watch = subprocess.Popen(
        [sys.executable, "-m", "aifaq", "watch", thread_id,
         "--interval", "0.5", "--timeout", "60"],
        cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        # watch がポーリングを始めるのを待つ
        time.sleep(2)
        assert watch.poll() is None, "watch が早期終了した"

        answer = subprocess.run(
            [sys.executable, "-m", "aifaq", "pending", "answer", str(pending_id),
             "--approved-by", "hantani", "--category", "network"],
            cwd=str(cwd), env=env, input="別プロセスから登録した回答です",
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        assert answer.returncode == 0, answer.stderr

        stdout, _stderr = watch.communicate(timeout=60)
        assert watch.returncode == 0
        assert "別プロセスから登録した回答です" in stdout
        assert "hantani" in stdout
    finally:
        if watch.poll() is None:
            watch.kill()
            watch.communicate()


def test_watch_json_from_another_process_is_parseable(workspace):
    """別プロセスの watch --json でも標準出力がJSONとして読める。"""
    cwd, env = workspace
    thread_id = "integration-002"
    pending_id = make_handoff(workspace, thread_id)

    watch = subprocess.Popen(
        [sys.executable, "-m", "aifaq", "--json", "watch", thread_id,
         "--interval", "0.5", "--timeout", "60"],
        cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        time.sleep(2)
        subprocess.run(
            [sys.executable, "-m", "aifaq", "pending", "answer", str(pending_id),
             "--approved-by", "hantani"],
            cwd=str(cwd), env=env, input="JSON経路の回答",
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        stdout, _ = watch.communicate(timeout=60)
        data = json.loads(stdout)
        assert data["state"] == "ANSWERED"
        assert data["answer"] == "JSON経路の回答"
    finally:
        if watch.poll() is None:
            watch.kill()
            watch.communicate()


def test_concurrent_answers_from_two_processes_only_one_wins(workspace):
    """2プロセスが同時に同じpendingへ回答しても、片方だけが成功する。"""
    cwd, env = workspace
    thread_id = "integration-003"
    pending_id = make_handoff(workspace, thread_id)

    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "aifaq", "pending", "answer", str(pending_id),
             "--approved-by", f"admin{i}"],
            cwd=str(cwd), env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        for i in range(2)
    ]
    results = [p.communicate(input=f"admin{i}の回答", timeout=60) for i, p in enumerate(procs)]
    codes = [p.returncode for p in procs]

    assert sorted(codes) == [0, 2], f"codes={codes}, out={results}"

    status = run_cli(workspace, ["--json", "status", thread_id])
    data = json.loads(status.stdout)
    assert data["state"] == "ANSWERED"
    # 勝った側の回答だけが残り、上書きされていない
    assert data["answer"] in ("admin0の回答", "admin1の回答")


def test_status_works_while_another_process_writes(workspace):
    """別プロセスが書き込み中でも status が読める(WAL)。"""
    cwd, env = workspace
    thread_id = "integration-004"
    pending_id = make_handoff(workspace, thread_id)

    answer = subprocess.Popen(
        [sys.executable, "-m", "aifaq", "pending", "answer", str(pending_id),
         "--approved-by", "hantani"],
        cwd=str(cwd), env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        # 書き込みプロセスと並行して読み取りを繰り返す
        for _ in range(5):
            result = run_cli(workspace, ["--json", "status", thread_id])
            assert result.returncode == 0, result.stderr
            json.loads(result.stdout)  # 壊れたJSONを返さない
        answer.communicate(input="並行書き込みの回答", timeout=60)
    finally:
        if answer.poll() is None:
            answer.kill()
            answer.communicate()

    final = run_cli(workspace, ["--json", "status", thread_id])
    assert json.loads(final.stdout)["state"] == "ANSWERED"


def test_full_two_terminal_flow_end_to_end(workspace):
    """§10の手動手順と同じ流れを別プロセスで通す。"""
    thread_id = "integration-005"
    pending_id = make_handoff(workspace, thread_id)

    listed = run_cli(workspace, ["pending", "list"])
    assert str(pending_id) in listed.stdout

    shown = run_cli(workspace, ["pending", "show", str(pending_id)])
    assert shown.returncode == 0

    answered = subprocess.run(
        [sys.executable, "-m", "aifaq", "pending", "answer", str(pending_id),
         "--approved-by", "hantani", "--category", "network",
         "--variants", "VPNパスワード再発行"],
        cwd=str(workspace[0]), env=workspace[1],
        input="情報システム部の申請フォームから再発行を依頼してください。",
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert answered.returncode == 0

    status = run_cli(workspace, ["status", thread_id])
    assert "情報システム部の申請フォーム" in status.stdout
    assert "hantani" in status.stdout

    history = run_cli(workspace, ["history", thread_id])
    assert history.returncode == 0
    assert "HUMAN_ANSWER" in history.stdout

    # 別表現の類似質問が承認済み知識から回答される
    reuse = run_cli(workspace, ["--json", "ask", "VPNパスワード再発行",
                                "--thread-id", "integration-005-reuse"])
    assert json.loads(reuse.stdout)["answer_type"] == "KNOWLEDGE"
