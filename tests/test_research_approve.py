"""AI調査結果の人間承認 (instruction-005 §5.1) のテスト。"""

import json

import pytest

from aifaq.cli import main


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    repo_root = tmp_path
    (repo_root / "knowledge" / "public").mkdir(parents=True)
    (repo_root / "MEMORY.md").write_text(
        "# AI-FAQ Memory Index\n\n"
        "## Source Map\n\n## Terminology\n\n## Routing Rules\n\n"
        "## Important Decisions\n\n## Known Gaps\n\n## Retired or Forbidden Sources\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("AIFAQ_DB_PATH", str(repo_root / "data" / "aifaq.db"))
    return repo_root


def _make_research_run(capsys, question="Windows 11の公式手順は？"):
    """FakeProvider で公開質問を1回流し、research run を1件作る。"""
    main(["init"])
    capsys.readouterr()
    main(["--fake-provider", "ask", question, "--thread-id", "t-research"])
    capsys.readouterr()
    main(["--json", "research", "list"])
    rows = json.loads(capsys.readouterr().out)
    assert rows, "research run が記録されていません"
    return rows[0]


def test_research_run_is_recorded_as_pending_with_answer(cli_env, capsys):
    row = _make_research_run(capsys)
    assert row["review_status"] == "PENDING"
    assert row["status"] == "ok"
    # 承認画面で元のAI回答を読めるよう、回答本文が保存されている
    assert row["answer"].strip()
    assert row["sources"]


def test_research_show(cli_env, capsys):
    row = _make_research_run(capsys)
    code = main(["research", "show", str(row["id"])])
    assert code == 0
    out = capsys.readouterr().out
    assert "AI回答:" in out
    assert "出典:" in out


def test_research_show_invalid_id(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["research", "show", "999"]) == 1
    assert "見つかりません" in capsys.readouterr().out


def test_approve_promotes_to_knowledge_and_is_reusable(cli_env, capsys):
    row = _make_research_run(capsys)
    code = main(["research", "approve", str(row["id"]), "--approved-by", "hantani"])
    assert code == 0
    out = capsys.readouterr().out
    assert "KB-" in out
    assert "修正=なし" in out

    main(["--json", "knowledge", "list"])
    articles = json.loads(capsys.readouterr().out)
    approved = [a for a in articles if a["source_type"] == "APPROVED_AI"]
    assert len(approved) == 1
    # 出典URLが引き継がれている
    assert approved[0]["source_urls"]

    # 承認後は同じ質問が承認済みナレッジで返る (外部調査を再実行しない)
    main(["--json", "--fake-provider", "ask", row["question"], "--thread-id", "t-reuse"])
    answer = json.loads(capsys.readouterr().out)
    assert answer["answer_type"] == "KNOWLEDGE"


def test_approve_records_reviewer_metadata(cli_env, capsys):
    row = _make_research_run(capsys)
    main(
        ["research", "approve", str(row["id"]), "--approved-by", "hantani",
         "--reason", "内容確認済み"]
    )
    capsys.readouterr()
    main(["--json", "research", "show", str(row["id"])])
    data = json.loads(capsys.readouterr().out)
    assert data["review_status"] == "APPROVED"
    assert data["reviewed_by"] == "hantani"
    assert data["reviewed_at"]
    assert data["review_reason"] == "内容確認済み"
    assert data["resulting_knowledge_id"]
    # 元のAI回答は書き換えず保持する
    assert data["answer"] == row["answer"]


def test_approve_with_corrected_answer_marks_modified(cli_env, capsys, tmp_path):
    row = _make_research_run(capsys)
    corrected = tmp_path / "corrected.md"
    corrected.write_text("IT管理者が修正した回答本文です。", encoding="utf-8")

    code = main(
        ["research", "approve", str(row["id"]), "--approved-by", "hantani",
         "--answer-file", str(corrected)]
    )
    assert code == 0
    assert "修正=あり" in capsys.readouterr().out

    main(["--json", "research", "show", str(row["id"])])
    data = json.loads(capsys.readouterr().out)
    assert data["was_modified"] is True
    assert data["approved_answer"] == "IT管理者が修正した回答本文です。"
    assert data["answer"] == row["answer"]  # 元のAI回答は残る

    main(["--json", "knowledge", "show", str(data["resulting_knowledge_id"])])
    article = json.loads(capsys.readouterr().out)
    assert article["answer"] == "IT管理者が修正した回答本文です。"


def test_answer_file_with_bom_is_read_cleanly(cli_env, capsys, tmp_path):
    """Windowsのエディタが付けるBOMを回答本文へ混入させない。"""
    row = _make_research_run(capsys)
    corrected = tmp_path / "corrected.md"
    corrected.write_text("修正版の回答です。", encoding="utf-8-sig")

    main(["research", "approve", str(row["id"]), "--approved-by", "hantani",
          "--answer-file", str(corrected)])
    capsys.readouterr()

    main(["--json", "research", "show", str(row["id"])])
    data = json.loads(capsys.readouterr().out)
    assert data["approved_answer"] == "修正版の回答です。"
    assert not data["approved_answer"].startswith("﻿")


def test_double_approval_is_rejected(cli_env, capsys):
    row = _make_research_run(capsys)
    assert main(["research", "approve", str(row["id"]), "--approved-by", "hantani"]) == 0
    capsys.readouterr()

    code = main(["research", "approve", str(row["id"]), "--approved-by", "other"])
    assert code == 2
    assert "既に APPROVED" in capsys.readouterr().out

    # ナレッジが二重に作られていない
    main(["--json", "knowledge", "list"])
    articles = json.loads(capsys.readouterr().out)
    assert len([a for a in articles if a["source_type"] == "APPROVED_AI"]) == 1


def test_approve_invalid_id(cli_env, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["research", "approve", "999", "--approved-by", "x"]) == 1
    assert "見つかりません" in capsys.readouterr().out


def test_reject_and_expire(cli_env, capsys):
    row = _make_research_run(capsys)
    assert main(["research", "reject", str(row["id"]), "--approved-by", "hantani",
                 "--reason", "情報が古い"]) == 0
    assert "REJECTED" in capsys.readouterr().out

    # 却下後は承認できない
    assert main(["research", "approve", str(row["id"]), "--approved-by", "hantani"]) == 2
    capsys.readouterr()

    # 却下済みはナレッジ化されていない
    main(["--json", "knowledge", "list"])
    articles = json.loads(capsys.readouterr().out)
    assert not [a for a in articles if a["source_type"] == "APPROVED_AI"]


def test_expired_status(cli_env, capsys):
    row = _make_research_run(capsys)
    assert main(["research", "reject", str(row["id"]), "--approved-by", "hantani",
                 "--expired"]) == 0
    assert "EXPIRED" in capsys.readouterr().out


def test_research_list_filters_by_status(cli_env, capsys):
    row = _make_research_run(capsys)
    main(["--json", "research", "list", "--status", "PENDING"])
    assert len(json.loads(capsys.readouterr().out)) == 1

    main(["--json", "research", "list", "--status", "APPROVED"])
    assert json.loads(capsys.readouterr().out) == []

    main(["research", "approve", str(row["id"]), "--approved-by", "hantani"])
    capsys.readouterr()

    main(["--json", "research", "list", "--status", "APPROVED"])
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_failed_research_run_is_not_approvable(cli_env, capsys):
    """調査に失敗した run はレビュー対象外(NOT_APPLICABLE)になる。"""
    main(["init"])
    capsys.readouterr()

    from aifaq import db as db_module
    from aifaq.config import Settings
    from aifaq.repositories import Repositories

    settings = Settings.from_env()
    conn = db_module.connect(settings)
    db_module.init_db(conn)
    repos = Repositories.build(conn)
    run_id = repos.research_runs.record(
        thread_id="t", question="q", provider="antigravity", status="error",
        confidence=None, sources=[], warnings=[], error_summary="timeout",
    )
    conn.close()

    main(["--json", "research", "show", str(run_id)])
    assert json.loads(capsys.readouterr().out)["review_status"] == "NOT_APPLICABLE"

    assert main(["research", "approve", str(run_id), "--approved-by", "x"]) == 2
    assert "調査に失敗している" in capsys.readouterr().out


def test_ai_answer_is_not_auto_promoted(cli_env, capsys):
    """承認するまでナレッジ化されない (自動昇格の禁止)。"""
    _make_research_run(capsys)
    main(["--json", "knowledge", "list"])
    articles = json.loads(capsys.readouterr().out)
    assert not [a for a in articles if a["source_type"] == "APPROVED_AI"]
