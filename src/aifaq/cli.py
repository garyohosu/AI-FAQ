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
import uuid
from pathlib import Path

from aifaq import db, ingestion, memory
from aifaq.config import Settings
from aifaq.graph import ReplyError, run_ask, run_reply
from aifaq.models import (
    AnswerType,
    KnowledgeStatus,
    PendingStatus,
    SourcePriority,
    SourceScope,
    SourceStatus,
)
from aifaq.providers.fake import FakeResearchProvider
from aifaq.providers.gemini_cli import GeminiCLIProvider
from aifaq.repositories import Repositories


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
            "[IT管理者の回答待ち]",
            ans.answer,
            f"受付番号: {ans.pending_id}",
            f"thread_id: {ans.thread_id}",
        ]
        if ans.notice:
            lines.append(f"理由: {ans.notice}")
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


def _get_provider(settings: Settings, use_fake: bool):
    if use_fake:
        return FakeResearchProvider()
    return GeminiCLIProvider(settings)


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

    gemini_path = shutil.which(settings.gemini_executable)
    if gemini_path is None:
        checks.append(
            {"name": "gemini_cli_present", "status": "warn", "detail": "見つかりません"}
        )
    else:
        checks.append({"name": "gemini_cli_present", "status": "ok", "detail": gemini_path})
        try:
            proc = subprocess.run(
                [gemini_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
                check=False,
            )
            checks.append(
                {
                    "name": "gemini_cli_version",
                    "status": "ok" if proc.returncode == 0 else "warn",
                    "detail": (proc.stdout or proc.stderr).strip()[:200],
                }
            )
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": "gemini_cli_version", "status": "warn", "detail": str(exc)})

    gemini_settings_path = repo_root / ".gemini" / "settings.json"
    if gemini_settings_path.exists():
        checks.append(
            {"name": "gemini_settings_present", "status": "ok", "detail": str(gemini_settings_path)}
        )
        try:
            data = json.loads(gemini_settings_path.read_text(encoding="utf-8"))
            excluded = set(data.get("tools", {}).get("exclude", []))
            dangerous = {"run_shell_command", "write_file", "edit", "replace", "save_memory"}
            missing = dangerous - excluded
            if missing:
                checks.append(
                    {
                        "name": "gemini_dangerous_tools_disabled",
                        "status": "warn",
                        "detail": f"除外設定に無いツール: {sorted(missing)}",
                    }
                )
            else:
                checks.append(
                    {"name": "gemini_dangerous_tools_disabled", "status": "ok", "detail": "OK"}
                )
        except (json.JSONDecodeError, OSError) as exc:
            checks.append(
                {"name": "gemini_dangerous_tools_disabled", "status": "warn", "detail": str(exc)}
            )
    else:
        checks.append(
            {"name": "gemini_settings_present", "status": "warn", "detail": "見つかりません"}
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
            answer_text = Path(args.answer_file).read_text(encoding="utf-8").strip()
        else:
            answer_text = sys.stdin.read().strip()
        if not answer_text:
            print("回答本文が空です(--answer-fileまたは標準入力で指定してください)")
            return 2
        tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        variants = [v.strip() for v in (args.variants or "").split(",") if v.strip()]
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
        print(f"承認済み知識として保存しました: KB-{knowledge_id} (pending #{args.id} ANSWERED)")
        return 0
    finally:
        conn.close()


def cmd_knowledge_import(args, settings: Settings) -> int:
    conn, repos = _open(settings)
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

        if args.json:
            print(
                json.dumps(
                    {
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
        return 0
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
                print(f"{c['relative_path']} 行{c['row_start']}-{c['row_end']}: {c['content'][:80]}")
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
    parser.add_argument("--fake-provider", action="store_true", help="テスト用: Gemini CLIの代わりにFakeProviderを使う")
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
    p.set_defaults(func=cmd_knowledge_import)

    p = knowledge_sub.add_parser("sync")
    p.add_argument("path", nargs="?", default=None)
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
    settings = Settings.from_env()
    return args.func(args, settings)


if __name__ == "__main__":
    sys.exit(main())
