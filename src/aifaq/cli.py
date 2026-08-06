"""`aifaq` CLI。

コマンド名は instruction-2026-08-06-004.md (最新版) を基準にし、
001/002由来の別名をエイリアスとして残す。対応関係はREADMEに明記する。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from aifaq import db, ingestion, memory
from aifaq.config import PROVIDER_FAKE, Settings
from aifaq.graph import ReplyError, run_ask, run_reply
from aifaq.models import (
    AnswerType,
    KnowledgeSourceType,
    KnowledgeStatus,
    PendingStatus,
    SourcePriority,
    SourceScope,
    SourceStatus,
    ThreadState,
    ThreadStatus,
)
from aifaq.providers.antigravity import AntigravityProvider
from aifaq.providers.base import ResearchProviderError
from aifaq.providers.fake import FakeResearchProvider
from aifaq.repositories import AlreadyAnsweredError, Repositories
from aifaq.security import AnswerValidationError, validate_human_answer


def _repo_root() -> Path:
    return Path.cwd()


def _open(settings: Settings) -> tuple[sqlite3.Connection, Repositories]:
    conn = db.connect(settings)
    db.init_db(conn)
    return conn, Repositories.build(conn)


def _print(obj, as_json: bool) -> None:
    if as_json:
        if hasattr(obj, "model_dump"):
            print(json.dumps(obj.model_dump(mode="json"), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    else:
        print(obj)


def _format_answer(ans, max_rounds: int) -> str:
    if ans.answer_type == AnswerType.KNOWLEDGE:
        lines = ["[社内承認済みナレッジ]", ans.answer]
        if ans.notice:
            lines += ["", ans.notice]
        return "\n".join(lines)
    if ans.answer_type == AnswerType.INTERNET_RESEARCH:
        lines = ["[インターネット調査による暫定回答]", ans.answer, ""]
        if ans.sources:
            lines.append("出典:")
            lines += [f"- {s.url}" for s in ans.sources]
        if ans.notice:
            lines.append(f"注意: {ans.notice}")
        return "\n".join(lines)
    if ans.answer_type == AnswerType.PENDING_HUMAN:
        lines = [
            "IT管理者へ引き継ぎました。",
            ans.answer,
            f"受付番号: {ans.pending_id}",
            f"thread_id: {ans.thread_id}",
            "状態: 回答待ち",
        ]
        if ans.notice:
            lines.append(f"理由: {ans.notice}")
        lines += [
            "",
            "後から次のコマンドで確認できます:",
            f"  aifaq status {ans.thread_id}",
            "",
            "回答を待ち続ける場合:",
            f"  aifaq watch {ans.thread_id}",
        ]
        return "\n".join(lines)
    if ans.answer_type == AnswerType.NEEDS_CLARIFICATION:
        lines = [
            f"[確認質問 {ans.clarification_round}/{max_rounds} "
            f"(残り{ans.remaining_rounds}回)]",
            ans.question,
        ]
        if ans.options:
            lines.append("選択肢: " + " / ".join(ans.options))
        lines.append(f"thread_id: {ans.thread_id}")
        lines.append(f'次は次のコマンドで回答してください: aifaq reply {ans.thread_id} "回答"')
        return "\n".join(lines)
    return ans.answer


def _format_citation(chunk) -> str:
    """取り込み資料の出典表記を組み立てる。

    instruction-005 §4.6 が求めるとおり、ファイル・Excelシート名・行範囲を
    すべて示す。シート名を持たないMarkdown/TXTでは見出しがあれば見出しを使う。
    """
    parts = [chunk["relative_path"]]
    sheet = chunk["sheet_name"] if "sheet_name" in chunk.keys() else None
    if sheet:
        parts.append(f"シート「{sheet}」")
    row_start, row_end = chunk["row_start"], chunk["row_end"]
    if row_start is not None and row_end is not None:
        parts.append(f"行{row_start}-{row_end}" if row_start != row_end else f"行{row_start}")
    heading = chunk["heading"] if "heading" in chunk.keys() else None
    if not sheet and heading:
        parts.append(f"見出し「{heading}」")
    return " ".join(parts)


def _get_provider(settings: Settings, use_fake: bool):
    """調査プロバイダーを決める。

    `--fake-provider` が最優先、次に `AIFAQ_RESEARCH_PROVIDER=fake`。
    どちらも指定が無ければ実環境用の Antigravity CLI を使う。
    """
    if use_fake or settings.research_provider == PROVIDER_FAKE:
        return FakeResearchProvider()
    return AntigravityProvider(settings)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def _doctor_checks(settings: Settings, repo_root: Path) -> list[dict]:
    checks: list[dict] = []

    checks.append({"name": "python_version", "status": "ok", "detail": sys.version.split()[0]})
    checks.append(
        {"name": "sqlite_version", "status": "ok", "detail": sqlite3.sqlite_version}
    )

    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        checks.append({"name": "fts5_available", "status": "ok", "detail": "利用可能"})
    except sqlite3.OperationalError:
        checks.append(
            {"name": "fts5_available", "status": "warn", "detail": "利用不可(LIKE検索へフォールバック)"}
        )

    try:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        test_conn = db.connect(settings)
        db.init_db(test_conn)
        test_conn.execute("SELECT 1")
        test_conn.close()
        checks.append({"name": "db_writable", "status": "ok", "detail": str(settings.db_path)})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "db_writable", "status": "error", "detail": str(exc)})

    checks.append(
        {
            "name": "research_provider",
            "status": "ok",
            "detail": f"{settings.research_provider} (transport={settings.research_transport})",
        }
    )

    if settings.research_provider == PROVIDER_FAKE:
        checks.append(
            {
                "name": "antigravity_cli_present",
                "status": "ok",
                "detail": "FakeProvider を使用中のため未確認",
            }
        )
    else:
        agy_path = shutil.which(settings.research_bin)
        if agy_path is None:
            checks.append(
                {
                    "name": "antigravity_cli_present",
                    "status": "warn",
                    "detail": f"{settings.research_bin!r} が見つかりません",
                }
            )
        else:
            checks.append(
                {"name": "antigravity_cli_present", "status": "ok", "detail": agy_path}
            )
            try:
                version = AntigravityProvider(settings).version()
                checks.append(
                    {"name": "antigravity_cli_version", "status": "ok", "detail": version[:200]}
                )
            except ResearchProviderError as exc:
                checks.append(
                    {"name": "antigravity_cli_version", "status": "warn", "detail": str(exc)[:200]}
                )

        if settings.research_transport == "file":
            # 参照渡しは agy 側の権限設定が要る。ヘッドレスでは read_file が
            # 自動拒否されるため、既定の arg 方式より確認事項が多い。
            checks.append(
                {
                    "name": "antigravity_transport_file_note",
                    "status": "warn",
                    "detail": (
                        "transport=file は agy の permissions.allow へ read_file を "
                        "許可する設定が必要です(未設定だとヘッドレスで自動拒否されます)"
                    ),
                }
            )

    try:
        result = subprocess.run(
            ["git", "ls-files", "knowledge/internal", "knowledge/inbox"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=10,
            shell=False,
            check=False,
        )
        tracked = [line for line in result.stdout.splitlines() if line.strip()]
        if tracked:
            checks.append(
                {
                    "name": "internal_files_not_tracked",
                    "status": "warn",
                    "detail": f"Gitで追跡されている内部資料があります: {tracked}",
                }
            )
        else:
            checks.append(
                {"name": "internal_files_not_tracked", "status": "ok", "detail": "OK"}
            )
    except (OSError, subprocess.SubprocessError):
        checks.append(
            {"name": "internal_files_not_tracked", "status": "warn", "detail": "gitが見つからないため未確認"}
        )

    return checks


# ---------------------------------------------------------------------------
# コマンド実装
# ---------------------------------------------------------------------------


def cmd_init(args, settings: Settings) -> int:
    conn, _ = _open(settings)
    fts = db.fts5_available(conn)
    conn.close()
    print(f"初期化完了: {settings.db_path} (FTS5: {'利用可能' if fts else '利用不可'})")
    return 0


def cmd_doctor(args, settings: Settings) -> int:
    checks = _doctor_checks(settings, _repo_root())
    if args.json:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            mark = {"ok": "OK", "warn": "WARN", "error": "ERROR"}[c["status"]]
            print(f"[{mark}] {c['name']}: {c['detail']}")
    return 1 if any(c["status"] == "error" for c in checks) else 0


def cmd_ask(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        thread_id = args.thread_id or str(uuid.uuid4())
        provider = _get_provider(settings, args.fake_provider)
        ans = run_ask(
            repos, settings, provider,
            thread_id=thread_id, question=args.question, requester=args.requester,
        )
        _print(ans, args.json) if args.json else print(_format_answer(ans, settings.max_clarification_rounds))
        return 0
    finally:
        conn.close()


def cmd_reply(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        provider = _get_provider(settings, args.fake_provider)
        try:
            ans = run_reply(
                repos, settings, provider,
                thread_id=args.thread_id, answer_text=args.answer,
            )
        except ReplyError as exc:
            if args.json:
                print(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                print(f"エラー: {exc}")
            return 2
        if args.json:
            _print(ans, True)
        else:
            print(_format_answer(ans, settings.max_clarification_rounds))
        return 0
    finally:
        conn.close()


def cmd_pending_list(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        status = PendingStatus(args.status) if args.status else None
        rows = repos.pending.list(status)
        data = [dict(r) for r in rows]
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        else:
            for r in data:
                print(f"#{r['id']} [{r['status']}] thread={r['thread_id']} : {r['original_question']}")
        return 0
    finally:
        conn.close()


def cmd_pending_show(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        row = repos.pending.get(args.id)
        if row is None:
            print(f"pending #{args.id} が見つかりません")
            return 1
        data = dict(row)
        clarifications = [
            t.model_dump(mode="json") for t in repos.clarifications.list_for_thread(data["thread_id"])
        ]
        data["clarification_history"] = clarifications
        _print(data if args.json else data, args.json)
        return 0
    finally:
        conn.close()


def cmd_pending_answer(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        if args.answer_file:
            # utf-8-sig: Windows のエディタが付けるBOMを回答本文へ混入させない
            answer_text = Path(args.answer_file).read_text(encoding="utf-8-sig").strip()
        else:
            # PowerShell の `echo ... | aifaq ...` は先頭にBOM(U+FEFF)を
            # 付けることがある。ファイル読み込み側と同じ扱いで取り除く。
            answer_text = sys.stdin.read().lstrip("﻿").strip()
        if not answer_text:
            print("回答本文が空です(--answer-fileまたは標準入力で指定してください)")
            return 2
        try:
            answer_text = validate_human_answer(
                answer_text, max_chars=settings.max_answer_chars
            )
        except AnswerValidationError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 2

        tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        variants = [v.strip() for v in (args.variants or "").split(",") if v.strip()]
        try:
            knowledge_id = repos.pending.answer(
                args.id,
                answer_text=answer_text,
                category=args.category,
                tags=tags,
                variants=variants,
                approved_by=args.approved_by,
                valid_until=args.valid_until,
                change_reason=args.reason or "IT管理者による人間回答",
            )
        except AlreadyAnsweredError as exc:
            # 既存回答は上書きしない (§4.3)。
            print(f"エラー: {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 1

        pending = repos.pending.get(args.id)
        thread_id = pending["thread_id"] if pending else ""
        if args.json:
            _print(
                {
                    "pending_id": args.id,
                    "thread_id": thread_id,
                    "knowledge_id": knowledge_id,
                    "status": "ANSWERED",
                },
                True,
            )
        else:
            print(
                f"承認済み知識として保存しました: KB-{knowledge_id} "
                f"(pending #{args.id} ANSWERED)"
            )
            print(f"質問者のthread_id: {thread_id}")
            print(f"質問者は次で受け取れます: aifaq status {thread_id}")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# status / watch (instruction-006 §4.1 / §4.2)
# ---------------------------------------------------------------------------

#: `watch` がタイムアウトしたときの終了コード。READMEに記載する。
WATCH_TIMEOUT_EXIT_CODE = 3
#: `watch` / `status` の対象が見つからないときの終了コード。
NOT_FOUND_EXIT_CODE = 1
#: Ctrl+C で中断したときの終了コード。
INTERRUPTED_EXIT_CODE = 130


def _format_status(status: ThreadStatus) -> str:
    """`status` / `watch` の人間向け表示。"""
    if status.state == ThreadState.NOT_FOUND:
        return f"該当する質問が見つかりません: {status.thread_id or '(指定なし)'}"

    lines: list[str] = []
    if status.state == ThreadState.ANSWERED:
        lines.append("[IT管理者からの回答]")
        lines.append(status.answer or "(回答本文が記録されていません)")
        lines.append("")
    elif status.state == ThreadState.PENDING_HUMAN:
        lines.append("[回答待ち]")
    elif status.state == ThreadState.NEEDS_CLARIFICATION:
        lines.append("[確認質問に回答してください]")
        if status.next_question:
            lines.append(status.next_question)
        if status.options:
            lines.append("選択肢: " + " / ".join(status.options))
        lines.append("")
    elif status.state == ThreadState.CANCELLED:
        lines.append("[取り下げ済み]")
    elif status.state == ThreadState.COMPLETED:
        lines.append("[対応済み(人間引き継ぎなし)]")

    if status.pending_id is not None:
        lines.append(f"受付番号: {status.pending_id}")
    lines.append(f"thread_id: {status.thread_id}")
    if status.original_question:
        lines.append(f"質問: {status.original_question}")
    if status.state != ThreadState.ANSWERED:
        lines.append(f"状態: {status.state.value}")
    if status.created_at:
        lines.append(f"受付日時: {status.created_at.isoformat()}")
    if status.answered_by:
        lines.append(f"回答者: {status.answered_by}")
    if status.answered_at:
        lines.append(f"回答日時: {status.answered_at.isoformat()}")
    if status.answer_type:
        lines.append(f"回答種別: {status.answer_type}")
    if status.knowledge_id:
        lines.append(f"承認済み知識: KB-{status.knowledge_id}")
    if status.sources:
        lines.append("出典:")
        lines += [f"- {s.url}" for s in status.sources]
    if status.clarifications:
        lines.append("確認質問履歴:")
        for c in status.clarifications:
            lines.append(f"  round{c.round_no}: Q={c.question} A={c.answer or '(未回答)'}")
    return "\n".join(lines)


def _lookup_status(repos, args) -> ThreadStatus:
    pending_id = getattr(args, "pending_id", None)
    thread_id = getattr(args, "thread_id", None)
    return repos.thread_status(thread_id=thread_id, pending_id=pending_id)


def cmd_status(args, settings: Settings) -> int:
    if not args.thread_id and args.pending_id is None:
        print("thread_id または --pending-id を指定してください", file=sys.stderr)
        return 2
    conn, repos = _open(settings)
    try:
        status = _lookup_status(repos, args)
        if status.state == ThreadState.ANSWERED and status.pending_id is not None:
            # 受領は記録するが状態は壊さない (§7)。
            repos.pending.mark_delivered(status.pending_id)
            status = _lookup_status(repos, args)
        if args.json:
            _print(status, True)
        else:
            print(_format_status(status))
        return NOT_FOUND_EXIT_CODE if status.state == ThreadState.NOT_FOUND else 0
    finally:
        conn.close()


def cmd_watch(args, settings: Settings) -> int:
    """回答が入るまでSQLiteをポーリングする。

    ポーリングのたびに接続を開き直し、短い読み取りで閉じる。`watch` が
    DBを長時間ロックしないようにするため (instruction-006 §6)。
    """
    if not args.thread_id and args.pending_id is None:
        print("thread_id または --pending-id を指定してください", file=sys.stderr)
        return 2

    interval = args.interval if args.interval is not None else settings.watch_interval_seconds
    if interval < settings.watch_min_interval_seconds:
        print(
            f"エラー: --interval は {settings.watch_min_interval_seconds} 秒以上に"
            f"してください(指定値: {interval})",
            file=sys.stderr,
        )
        return 2

    deadline = None if args.timeout is None else time.monotonic() + args.timeout
    last_state: ThreadState | None = None
    #: 進捗表示は stderr へ出す。`--json` の標準出力を壊さないため (§4.2)。
    progress = sys.stderr

    try:
        while True:
            conn, repos = _open(settings)
            try:
                status = _lookup_status(repos, args)
                if status.state == ThreadState.NOT_FOUND:
                    if args.json:
                        _print(status, True)
                    else:
                        print(_format_status(status))
                    return NOT_FOUND_EXIT_CODE
                if status.is_final:
                    if status.state == ThreadState.ANSWERED and status.pending_id is not None:
                        repos.pending.mark_delivered(status.pending_id)
                        status = _lookup_status(repos, args)
                    if args.json:
                        _print(status, True)
                    else:
                        print(_format_status(status))
                    return 0
            finally:
                conn.close()

            # 状態が変わったときだけ表示し、待機中に大量出力しない (§4.2)。
            if status.state != last_state:
                last_state = status.state
                if not args.json:
                    print(
                        f"待機中... (状態: {status.state.value}, "
                        f"{interval}秒ごとに確認。Ctrl+Cで終了)",
                        file=progress,
                    )

            if deadline is not None and time.monotonic() + interval > deadline:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining > 0:
                    time.sleep(remaining)
                conn, repos = _open(settings)
                try:
                    status = _lookup_status(repos, args)
                finally:
                    conn.close()
                if status.is_final:
                    if args.json:
                        _print(status, True)
                    else:
                        print(_format_status(status))
                    return 0
                if args.json:
                    _print(status, True)
                else:
                    print(f"タイムアウトしました({args.timeout}秒)。")
                    print(_format_status(status))
                return WATCH_TIMEOUT_EXIT_CODE

            time.sleep(interval)
    except KeyboardInterrupt:
        # Ctrl+C は異常終了ではなく「待つのをやめた」だけ。状態は壊さない。
        print("\n待機を中止しました(回答はDBに残ります)。", file=progress)
        return INTERRUPTED_EXIT_CODE


def cmd_knowledge_import(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    # 取り込み単位を source_import_runs へ記録する (instruction-005 §5.2)。
    # 失敗時も finish() を必ず呼び、途中終了した run を残さない。
    run_id = repos.import_runs.start(
        target_path=args.path or str(settings.knowledge_dir),
        actor=getattr(args, "actor", None) or "cli",
    )
    try:
        repo_root = _repo_root()
        result = validate_and_get_index(repo_root, settings)
        index_lookup = memory.build_index_lookup(result.entries)

        only_path = Path(args.path).resolve() if args.path else None
        outcomes, missing = ingestion.import_all(repos, settings, repo_root, only_path=only_path)

        for outcome in outcomes:
            entry = index_lookup.get(outcome.relative_path)
            if entry is not None and outcome.status == "imported":
                row = repos.source_files.get_by_path(outcome.relative_path)
                if row is not None:
                    from aifaq.models import SourceFileRecord
                    from datetime import datetime, UTC as _UTC

                    rec = SourceFileRecord(
                        relative_path=row["relative_path"],
                        file_type=row["file_type"],
                        scope=entry.scope,
                        category=entry.category,
                        owner=entry.owner,
                        priority=entry.priority,
                        status=entry.status,
                        size_bytes=row["size_bytes"],
                        modified_at=datetime.fromisoformat(row["modified_at"]),
                        sha256=row["sha256"],
                        last_imported_at=datetime.now(_UTC),
                        last_reviewed_at=None,
                        valid_until=None,
                        error_summary=row["error_summary"],
                    )
                    repos.source_files.upsert(rec)

        imported = [o for o in outcomes if o.status == "imported"]
        failures = [o for o in outcomes if o.status == "failed"]
        counts = {
            "detected": len(outcomes),
            "added": sum(1 for o in imported if not o.was_existing),
            "updated": sum(1 for o in imported if o.was_existing),
            "skipped_unchanged": sum(1 for o in outcomes if o.status == "skipped_unchanged"),
            "missing": len(missing),
            "failed": len(failures),
        }
        repos.import_runs.finish(
            run_id,
            **counts,
            warnings=[w.message for o in outcomes for w in o.warnings],
            error_summary="; ".join(f"{o.relative_path}: failed" for o in failures)[:500],
            succeeded=not failures,
        )

        if args.json:
            print(
                json.dumps(
                    {
                        "import_run_id": run_id,
                        "counts": counts,
                        "outcomes": [
                            {
                                "path": o.relative_path,
                                "status": o.status,
                                "warnings": [w.message for w in o.warnings],
                            }
                            for o in outcomes
                        ],
                        "missing": missing,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            for o in outcomes:
                print(f"{o.status}: {o.relative_path}")
                for w in o.warnings:
                    print(f"  警告: {w.message}")
            for m in missing:
                print(f"欠落として記録: {m}")
            print(
                f"取り込み実行 #{run_id}: 検出{counts['detected']} "
                f"追加{counts['added']} 更新{counts['updated']} "
                f"不変{counts['skipped_unchanged']} 欠落{counts['missing']} "
                f"失敗{counts['failed']}"
            )
        return 0
    except Exception as exc:  # noqa: BLE001 - 失敗も実行記録として残す
        repos.import_runs.finish(
            run_id, error_summary=str(exc)[:500], succeeded=False, failed=1
        )
        raise
    finally:
        conn.close()


def cmd_knowledge_scan(args, settings: Settings) -> int:
    repo_root = _repo_root()
    files = ingestion.scan_supported_files(settings, repo_root)
    result = subprocess.run(
        ["git", "ls-files", "knowledge/internal", "knowledge/inbox"],
        capture_output=True, text=True, cwd=str(repo_root), timeout=10, shell=False, check=False,
    )
    tracked = set(line.strip() for line in result.stdout.splitlines() if line.strip())
    for f in files:
        rel = f.relative_to(repo_root).as_posix()
        warn = " [WARNING: Git追跡対象]" if rel in tracked else ""
        print(f"{rel}{warn}")
    return 0


def cmd_knowledge_search(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        matches = repos.knowledge.search(args.query, limit=args.limit)
        chunks = repos.source_chunks.search(args.query, limit=args.limit)
        if args.json:
            print(
                json.dumps(
                    {
                        "knowledge": [m.model_dump(mode="json") for m in matches],
                        "sources": [dict(c) for c in chunks],
                    },
                    ensure_ascii=False, indent=2, default=str,
                )
            )
        else:
            print("== 承認済み知識 ==")
            for m in matches:
                print(f"KB-{m.knowledge_id} (score={m.score:.2f}): {m.canonical_question}")
            print("== 取り込み資料 ==")
            for c in chunks:
                print(f"{_format_citation(c)}: {c['content'][:80]}")
        return 0
    finally:
        conn.close()


def cmd_knowledge_list(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        status = KnowledgeStatus(args.status) if args.status else None
        arts = repos.knowledge.list(status)
        if args.json:
            print(json.dumps([a.model_dump(mode="json") for a in arts], ensure_ascii=False, indent=2))
        else:
            for a in arts:
                print(f"KB-{a.id} v{a.version} [{a.status.value}] {a.canonical_question}")
        return 0
    finally:
        conn.close()


def cmd_knowledge_show(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        art = repos.knowledge.get(args.id)
        if art is None:
            print(f"KB-{args.id} が見つかりません")
            return 1
        _print(art, args.json)
        return 0
    finally:
        conn.close()


def cmd_knowledge_retire(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        repos.knowledge.retire(args.id, args.by)
        print(f"KB-{args.id} を retired にしました")
        return 0
    finally:
        conn.close()


def cmd_knowledge_status(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        rows = repos.source_files.list()
        if args.json:
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2, default=str))
        else:
            for r in rows:
                print(f"{r['relative_path']} [{r['status']}] scope={r['scope']} priority={r['priority']}")
        return 0
    finally:
        conn.close()


def cmd_knowledge_rebuild_index(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        if not db.fts5_available(conn):
            print("FTS5が利用できないため索引再構築は不要です(LIKE検索を使用中)")
            return 0
        with conn:
            conn.execute("DELETE FROM knowledge_fts")
            conn.execute("DELETE FROM source_fts")
            conn.execute("DELETE FROM memory_fts")
            for art in repos.knowledge.list():
                repos.knowledge._sync_fts(art.id)  # noqa: SLF001
            for chunk_row in conn.execute("SELECT * FROM source_chunks").fetchall():
                conn.execute(
                    "INSERT INTO source_fts (chunk_id, source_file_id, heading, content) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        chunk_row["id"],
                        chunk_row["source_file_id"],
                        chunk_row["heading"] or "",
                        chunk_row["content"],
                    ),
                )
            for mem_row in conn.execute("SELECT * FROM memory_entries").fetchall():
                conn.execute(
                    "INSERT INTO memory_fts (memory_id, key, value) VALUES (?, ?, ?)",
                    (mem_row["id"], mem_row["key"], mem_row["value"]),
                )
        print("索引を再構築しました")
        return 0
    finally:
        conn.close()


def validate_and_get_index(repo_root: Path, settings: Settings):
    return memory.validate_memory_index(repo_root, settings)


def cmd_memory_validate(args, settings: Settings) -> int:
    repo_root = _repo_root()
    result = validate_and_get_index(repo_root, settings)
    if args.json:
        print(
            json.dumps(
                {"errors": result.errors, "warnings": result.warnings, "ok": result.ok},
                ensure_ascii=False, indent=2,
            )
        )
    else:
        for e in result.errors:
            print(f"[ERROR] {e}")
        for w in result.warnings:
            print(f"[WARN] {w}")
        if result.ok:
            print("OK: 必須章と形式に問題ありません")
    return 0 if result.ok else 1


def cmd_memory_show(args, settings: Settings) -> int:
    repo_root = _repo_root()
    index_path = repo_root / settings.memory_index_path
    if not index_path.exists():
        print(f"{settings.memory_index_path} が見つかりません")
        return 1
    print(index_path.read_text(encoding="utf-8"))
    return 0


def cmd_memory_sync(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        repo_root = _repo_root()
        entries = memory.load_all_memory_entries(repo_root, settings)
        repos.memory_entries.replace_all(entries)
        print(f"{len(entries)} 件のメモリーエントリをDBへ同期しました(Markdownが正)")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# chat: 対話モード (instruction-006 §4.4)
# ---------------------------------------------------------------------------

_CHAT_BANNER = """AI-FAQ CLI
終了: /quit
状態確認: /status
"""


def cmd_chat(args, settings: Settings) -> int:
    """確認質問を繰り返す対話モード。

    FAQロジックは重複実装せず、既存の `run_ask` / `run_reply` をそのまま
    呼ぶ。確認質問の最大3回制限も既存のグラフ側の制御に従う。
    """
    thread_id = args.thread_id or str(uuid.uuid4())
    print(_CHAT_BANNER)
    print(f"thread_id: {thread_id}\n")

    conn, repos = _open(settings)
    try:
        provider = _get_provider(settings, args.fake_provider)
        awaiting_clarification = False

        while True:
            try:
                line = input("あなた> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n終了します。")
                return 0

            if not line:
                continue
            if line in ("/quit", "/exit"):
                print("終了します。")
                return 0
            if line == "/status":
                status = repos.thread_status(thread_id=thread_id)
                print(_format_status(status))
                print()
                continue
            if line.startswith("/"):
                print("不明なコマンドです。使えるのは /status と /quit です。\n")
                continue

            try:
                if awaiting_clarification:
                    ans = run_reply(
                        repos, settings, provider,
                        thread_id=thread_id, answer_text=line,
                    )
                else:
                    ans = run_ask(
                        repos, settings, provider,
                        thread_id=thread_id, question=line, requester=args.requester,
                    )
            except ReplyError as exc:
                print(f"AI-FAQ> エラー: {exc}\n")
                awaiting_clarification = False
                continue

            awaiting_clarification = ans.answer_type == AnswerType.NEEDS_CLARIFICATION

            if awaiting_clarification:
                print(f"AI-FAQ> {ans.question}")
                for i, option in enumerate(ans.options, start=1):
                    print(f"  {i}. {option}")
                print()
                continue

            if ans.answer_type == AnswerType.PENDING_HUMAN:
                print(f"AI-FAQ> IT管理者へ引き継ぎました。受付番号は{ans.pending_id}です。")
                if not _chat_offer_wait(args, settings, thread_id):
                    return 0
                print()
                continue

            print(f"AI-FAQ> {ans.answer}")
            if ans.sources:
                for s in ans.sources:
                    print(f"  出典: {s.url}")
            if ans.notice:
                print(f"  注意: {ans.notice}")
            print()
    finally:
        conn.close()


def _chat_offer_wait(args, settings: Settings, thread_id: str) -> bool:
    """人間回答待ちで、待機するか終了するかを選ばせる。

    待機して回答を受け取った場合も、続けて質問できるよう True を返す。
    利用者が終了を選んだ場合のみ False。
    """
    try:
        choice = input("AI-FAQ> このまま回答を待ちますか？ [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if choice in ("n", "no"):
        print(f"AI-FAQ> 後から次で確認できます: aifaq status {thread_id}")
        return False

    watch_args = argparse.Namespace(
        thread_id=thread_id, pending_id=None, interval=None, timeout=None, json=False
    )
    cmd_watch(watch_args, settings)
    return True


# ---------------------------------------------------------------------------
# research: AI調査結果の人間承認 (instruction-005 §5.1)
# ---------------------------------------------------------------------------

#: research run のレビュー状態。
REVIEW_PENDING = "PENDING"
REVIEW_APPROVED = "APPROVED"
REVIEW_REJECTED = "REJECTED"
REVIEW_EXPIRED = "EXPIRED"
REVIEW_NOT_APPLICABLE = "NOT_APPLICABLE"


def _research_row_to_dict(row) -> dict:
    data = dict(row)
    data["sources"] = json.loads(data.pop("sources_json", "[]") or "[]")
    data["warnings"] = json.loads(data.pop("warnings_json", "[]") or "[]")
    data["was_modified"] = bool(data.get("was_modified"))
    return data


def cmd_research_list(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        rows = repos.research_runs.list(args.status)
        data = [_research_row_to_dict(r) for r in rows]
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        else:
            if not data:
                print("該当する調査結果はありません")
            for r in data:
                print(
                    f"#{r['id']} [{r['review_status']}] status={r['status']} "
                    f"conf={r['confidence']} 出典{len(r['sources'])}件 : {r['question']}"
                )
        return 0
    finally:
        conn.close()


def cmd_research_show(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        row = repos.research_runs.get(args.id)
        if row is None:
            print(f"research run #{args.id} が見つかりません")
            return 1
        data = _research_row_to_dict(row)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"#{data['id']} [{data['review_status']}]")
            print(f"質問: {data['question']}")
            print(f"プロバイダー: {data['provider']} / 信頼度: {data['confidence']}")
            print(f"\nAI回答:\n{data['answer']}")
            if data["sources"]:
                print("\n出典:")
                for s in data["sources"]:
                    print(f"- {s.get('url', '')} {s.get('title', '')}".rstrip())
            if data["warnings"]:
                print("\n警告: " + " / ".join(data["warnings"]))
            if data["review_status"] != REVIEW_PENDING:
                print(
                    f"\nレビュー: {data['review_status']} by {data['reviewed_by']} "
                    f"at {data['reviewed_at']} (修正={'あり' if data['was_modified'] else 'なし'})"
                )
                if data.get("resulting_knowledge_id"):
                    print(f"昇格先ナレッジ: KB-{data['resulting_knowledge_id']}")
        return 0
    finally:
        conn.close()


def cmd_research_approve(args, settings: Settings) -> int:
    """AI調査結果を人間が確認し、承認済み知識へ昇格する。

    元のAI回答と出典は research_runs に残したまま、承認された本文を
    knowledge_articles / knowledge_revisions へ保存する。
    """
    conn, repos = _open(settings)
    try:
        row = repos.research_runs.get(args.id)
        if row is None:
            print(f"research run #{args.id} が見つかりません")
            return 1
        if row["status"] != "ok":
            print(f"research run #{args.id} は調査に失敗しているため承認できません")
            return 2
        if row["review_status"] != REVIEW_PENDING:
            print(
                f"research run #{args.id} は既に {row['review_status']} です"
                "(二重承認はできません)"
            )
            return 2

        original_answer = row["answer"] or ""
        if args.answer_file:
            # utf-8-sig: Windows のエディタが付けるBOMを回答本文へ混入させない
            answer_text = Path(args.answer_file).read_text(encoding="utf-8-sig").strip()
        else:
            answer_text = original_answer.strip()
        if not answer_text:
            print("承認する回答本文が空です(--answer-file で指定してください)")
            return 2

        was_modified = answer_text != original_answer.strip()
        sources = json.loads(row["sources_json"] or "[]")
        source_urls = [s.get("url", "") for s in sources if isinstance(s, dict) and s.get("url")]
        tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        variants = [v.strip() for v in (args.variants or "").split(",") if v.strip()]

        reason = args.reason or (
            "AI調査結果を人間が修正のうえ承認" if was_modified else "AI調査結果を人間が承認"
        )
        knowledge_id = repos.knowledge.create(
            canonical_question=row["question"],
            answer=answer_text,
            category=args.category,
            tags=tags,
            source_type=KnowledgeSourceType.APPROVED_AI,
            approved_by=args.approved_by,
            variants=variants,
            valid_until=args.valid_until,
            source_urls=source_urls,
            change_reason=reason,
        )
        try:
            repos.research_runs.mark_reviewed(
                args.id,
                review_status=REVIEW_APPROVED,
                reviewed_by=args.approved_by,
                reason=reason,
                was_modified=was_modified,
                approved_answer=answer_text,
                resulting_knowledge_id=knowledge_id,
            )
        except ValueError as exc:
            # 承認直前に他プロセスが承認した場合。作成済みナレッジを取り消す。
            repos.knowledge.retire(knowledge_id, args.approved_by)
            print(f"エラー: {exc}")
            return 2

        print(
            f"承認済み知識として保存しました: KB-{knowledge_id} "
            f"(research #{args.id} APPROVED, 修正={'あり' if was_modified else 'なし'})"
        )
        return 0
    finally:
        conn.close()


def cmd_research_reject(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        row = repos.research_runs.get(args.id)
        if row is None:
            print(f"research run #{args.id} が見つかりません")
            return 1
        status = REVIEW_EXPIRED if args.expired else REVIEW_REJECTED
        try:
            repos.research_runs.mark_reviewed(
                args.id,
                review_status=status,
                reviewed_by=args.approved_by,
                reason=args.reason or "",
            )
        except ValueError as exc:
            print(f"エラー: {exc}")
            return 2
        print(f"research #{args.id} を {status} にしました")
        return 0
    finally:
        conn.close()


def cmd_history(args, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        rows = repos.history.list_for_thread(args.thread_id)
        clarifications = repos.clarifications.list_for_thread(args.thread_id)
        if args.json:
            print(
                json.dumps(
                    {
                        "history": [dict(r) for r in rows],
                        "clarifications": [c.model_dump(mode="json") for c in clarifications],
                    },
                    ensure_ascii=False, indent=2, default=str,
                )
            )
        else:
            for r in rows:
                print(f"{r['created_at']} route={r['route']} answer_type={r['answer_type']}")
            for c in clarifications:
                print(f"  round{c.round_no}: Q={c.question} A={c.answer}")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aifaq", description="学習型AI FAQ (社内IT管理部門向け)")
    parser.add_argument("--json", action="store_true", help="JSON出力モード")
    parser.add_argument(
        "--fake-provider",
        action="store_true",
        help="テスト用: 実際のAI CLI(Antigravity)の代わりにFakeProviderを使う",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", aliases=["init-db"])
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("--thread-id")
    p.add_argument("--requester")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("reply")
    p.add_argument("thread_id")
    p.add_argument("answer")
    p.set_defaults(func=cmd_reply)

    p = sub.add_parser("history")
    p.add_argument("thread_id")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("chat", help="確認質問を繰り返す対話モード")
    p.add_argument("--requester")
    p.add_argument("--thread-id")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("status", help="質問スレッドの状態と回答を一度だけ確認する")
    p.add_argument("thread_id", nargs="?")
    p.add_argument("--pending-id", type=int, help="受付番号で検索する")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("watch", help="回答が入るまで待って表示する")
    p.add_argument("thread_id", nargs="?")
    p.add_argument("--pending-id", type=int, help="受付番号で検索する")
    p.add_argument(
        "--interval",
        type=float,
        help=f"ポーリング間隔(秒)。既定2.0、最短0.5",
    )
    p.add_argument("--timeout", type=float, help="待機の上限(秒)。省略時は無制限")
    p.set_defaults(func=cmd_watch)

    research = sub.add_parser("research", help="AI調査結果の確認と承認")
    research_sub = research.add_subparsers(dest="research_command", required=True)

    p = research_sub.add_parser("list")
    p.add_argument(
        "--status",
        choices=[
            REVIEW_PENDING, REVIEW_APPROVED, REVIEW_REJECTED,
            REVIEW_EXPIRED, REVIEW_NOT_APPLICABLE,
        ],
        help="レビュー状態で絞り込む",
    )
    p.set_defaults(func=cmd_research_list)

    p = research_sub.add_parser("show")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_research_show)

    p = research_sub.add_parser("approve", help="AI調査結果を承認済み知識へ昇格する")
    p.add_argument("id", type=int)
    p.add_argument("--approved-by", required=True)
    p.add_argument("--answer-file", help="修正版の回答本文ファイル(省略時はAI回答をそのまま承認)")
    p.add_argument("--category", default="other")
    p.add_argument("--tags", help="カンマ区切り")
    p.add_argument("--variants", help="カンマ区切りの別表現")
    p.add_argument("--valid-until", help="ISO8601の有効期限")
    p.add_argument("--reason", help="承認・修正の理由")
    p.set_defaults(func=cmd_research_approve)

    p = research_sub.add_parser("reject", help="AI調査結果を却下または期限切れにする")
    p.add_argument("id", type=int)
    p.add_argument("--approved-by", required=True, help="判断した担当者")
    p.add_argument("--reason", help="却下理由")
    p.add_argument("--expired", action="store_true", help="却下ではなく期限切れとして記録する")
    p.set_defaults(func=cmd_research_reject)

    pending = sub.add_parser("pending")
    pending_sub = pending.add_subparsers(dest="pending_command", required=True)

    p = pending_sub.add_parser("list")
    p.add_argument("--status", choices=[s.value for s in PendingStatus])
    p.set_defaults(func=cmd_pending_list)

    p = pending_sub.add_parser("show")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_pending_show)

    p = pending_sub.add_parser("answer")
    p.add_argument("id", type=int)
    p.add_argument("--answer-file")
    p.add_argument("--approved-by", "--by", dest="approved_by", required=True)
    p.add_argument("--category", default="other")
    p.add_argument("--tags", default="")
    p.add_argument("--variants", default="")
    p.add_argument("--valid-until")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_pending_answer)

    knowledge = sub.add_parser("knowledge")
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command", required=True)

    p = knowledge_sub.add_parser("scan")
    p.set_defaults(func=cmd_knowledge_scan)

    p = knowledge_sub.add_parser("import")
    p.add_argument("path", nargs="?")
    p.add_argument("--actor", help="取り込み実行者(source_import_runs へ記録)")
    p.set_defaults(func=cmd_knowledge_import)

    p = knowledge_sub.add_parser("sync")
    p.add_argument("path", nargs="?", default=None)
    p.add_argument("--actor", help="取り込み実行者(source_import_runs へ記録)")
    p.set_defaults(func=cmd_knowledge_import)

    p = knowledge_sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_knowledge_search)

    p = knowledge_sub.add_parser("list")
    p.add_argument("--status", choices=[s.value for s in KnowledgeStatus])
    p.set_defaults(func=cmd_knowledge_list)

    p = knowledge_sub.add_parser("show")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_knowledge_show)

    p = knowledge_sub.add_parser("retire")
    p.add_argument("id", type=int)
    p.add_argument("--by", required=True)
    p.set_defaults(func=cmd_knowledge_retire)

    p = knowledge_sub.add_parser("status")
    p.set_defaults(func=cmd_knowledge_status)

    p = knowledge_sub.add_parser("rebuild-index")
    p.set_defaults(func=cmd_knowledge_rebuild_index)

    mem = sub.add_parser("memory")
    mem_sub = mem.add_subparsers(dest="memory_command", required=True)

    p = mem_sub.add_parser("validate")
    p.set_defaults(func=cmd_memory_validate)

    p = mem_sub.add_parser("show")
    p.set_defaults(func=cmd_memory_show)

    p = mem_sub.add_parser("sync")
    p.set_defaults(func=cmd_memory_sync)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        # 不正な環境変数(AIFAQ_RESEARCH_PROVIDER など)は起動時に弾く。
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    try:
        return args.func(args, settings)
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            # 「database is locked」だけでは原因が伝わらないので、
            # 何が起きていて何をすればよいかを明示する。
            print(
                f"エラー: データベース({settings.db_path})がロックされています。"
                "他の aifaq プロセスが実行中でないか確認し、終了後に再実行してください。",
                file=sys.stderr,
            )
            return 3
        raise


if __name__ == "__main__":
    sys.exit(main())
