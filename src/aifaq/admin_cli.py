"""IT管理者向けの簡易回答CLI。

通常の `aifaq pending answer` が持つ詳細オプションは残したまま、
日常運用では `aifaq-admin` だけで回答できるようにする。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from aifaq import db
from aifaq.config import Settings
from aifaq.models import PendingStatus
from aifaq.repositories import AlreadyAnsweredError, Repositories
from aifaq.security import AnswerValidationError, validate_human_answer


def _open(settings: Settings) -> tuple[sqlite3.Connection, Repositories]:
    conn = db.connect(settings)
    db.init_db(conn)
    return conn, Repositories.build(conn)


def _csv_values(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _resolve_approved_by(args: argparse.Namespace) -> str:
    candidates = (
        args.approved_by,
        os.environ.get("AIFAQ_ADMIN_NAME"),
        os.environ.get("USERNAME"),
        os.environ.get("USER"),
    )
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()

    try:
        value = input("回答者名> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n中止しました。")
        return ""
    return value


def _print_open_rows(rows) -> None:
    if not rows:
        print("未回答の質問はありません。")
        return

    print("未回答の質問:")
    for row in rows:
        print(f"  #{row['id']}  {row['original_question']}")


def _select_pending(repos: Repositories, pending_id: int | None):
    if pending_id is not None:
        row = repos.pending.get(pending_id)
        if row is None:
            print(f"受付番号 #{pending_id} は見つかりません。", file=sys.stderr)
            return None
        if row["status"] != PendingStatus.OPEN.value:
            print(
                f"受付番号 #{pending_id} は {row['status']} のため回答できません。",
                file=sys.stderr,
            )
            return None
        return row

    rows = repos.pending.list(PendingStatus.OPEN)
    if not rows:
        _print_open_rows(rows)
        return None

    _print_open_rows(rows)
    if len(rows) == 1:
        row = rows[0]
        print(f"\n受付番号 #{row['id']} を選択しました。")
        return row

    valid_ids = {int(row["id"]): row for row in rows}
    while True:
        try:
            raw = input("\n回答する受付番号> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n中止しました。")
            return None
        if raw.lower() in {"q", "quit", "/quit"}:
            print("中止しました。")
            return None
        try:
            selected = int(raw)
        except ValueError:
            print("受付番号を数字で入力してください。")
            continue
        if selected not in valid_ids:
            print("一覧にある未回答の受付番号を入力してください。")
            continue
        return valid_ids[selected]


def _show_pending(repos: Repositories, row) -> None:
    print("\n------------------------------")
    print(f"受付番号: {row['id']}")
    print(f"質問: {row['original_question']}")
    print(f"thread_id: {row['thread_id']}")

    clarifications = repos.clarifications.list_for_thread(row["thread_id"])
    if clarifications:
        print("確認履歴:")
        for item in clarifications:
            print(f"  {item.round_no}. {item.question}")
            if item.answer:
                print(f"     回答: {item.answer}")
    print("------------------------------")


def _read_answer(args: argparse.Namespace) -> str:
    if args.answer and args.answer_file:
        raise ValueError("回答本文と --answer-file は同時に指定できません。")

    if args.answer:
        return args.answer.strip()
    if args.answer_file:
        return Path(args.answer_file).read_text(encoding="utf-8-sig").strip()

    try:
        return input("回答> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n中止しました。")
        return ""


def _confirm(answer_text: str) -> bool:
    print("\n登録する回答:")
    print(answer_text)
    try:
        choice = input("\nこの内容で登録しますか？ [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n中止しました。")
        return False
    return choice not in {"n", "no"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aifaq-admin",
        description="IT管理者向けの簡易回答画面",
    )
    parser.add_argument("id", nargs="?", type=int, help="受付番号。省略時は一覧から選択")
    parser.add_argument("answer", nargs="?", help="回答本文。指定すると確認なしで登録")
    parser.add_argument("--by", "--approved-by", dest="approved_by", help="回答者名")
    parser.add_argument("--answer-file", help="UTF-8の回答ファイル")
    parser.add_argument("--category", default="other", help="分類。通常は省略可")
    parser.add_argument("--tags", default="", help="カンマ区切りのタグ")
    parser.add_argument("--variants", default="", help="カンマ区切りの別表現")
    parser.add_argument("--valid-until", help="ISO8601の有効期限")
    parser.add_argument("--reason", default="aifaq-adminから回答")
    parser.add_argument("--yes", action="store_true", help="確認を省略")
    parser.add_argument("--list", action="store_true", help="未回答一覧だけ表示")
    parser.add_argument("--json", action="store_true", help="結果をJSONで表示")
    return parser


def run(args: argparse.Namespace, settings: Settings) -> int:
    conn, repos = _open(settings)
    try:
        if args.list:
            rows = repos.pending.list(PendingStatus.OPEN)
            if args.json:
                print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2, default=str))
            else:
                _print_open_rows(rows)
            return 0

        row = _select_pending(repos, args.id)
        if row is None:
            return 0 if args.id is None else 1
        _show_pending(repos, row)

        approved_by = _resolve_approved_by(args)
        if not approved_by:
            print("回答者名が空のため中止しました。", file=sys.stderr)
            return 2

        try:
            answer_text = _read_answer(args)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 2
        if not answer_text:
            print("回答が空のため中止しました。", file=sys.stderr)
            return 2

        try:
            answer_text = validate_human_answer(
                answer_text,
                max_chars=settings.max_answer_chars,
            )
        except AnswerValidationError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 2

        # コマンドラインに回答本文を明示した場合は、1コマンド運用を優先して
        # 確認を省略する。対話入力と回答ファイルは既定で確認する。
        if not args.answer and not args.yes and not _confirm(answer_text):
            print("登録しませんでした。")
            return 0

        try:
            knowledge_id = repos.pending.answer(
                int(row["id"]),
                answer_text=answer_text,
                category=args.category,
                tags=_csv_values(args.tags),
                variants=_csv_values(args.variants),
                approved_by=approved_by,
                valid_until=args.valid_until,
                change_reason=args.reason,
            )
        except AlreadyAnsweredError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 1

        remaining = len(repos.pending.list(PendingStatus.OPEN))
        result = {
            "pending_id": int(row["id"]),
            "thread_id": row["thread_id"],
            "knowledge_id": knowledge_id,
            "status": "ANSWERED",
            "approved_by": approved_by,
            "remaining_open": remaining,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"\n回答を登録しました。承認済み知識: KB-{knowledge_id}")
            print("質問者へ回答済みです。")
            print(f"未回答の残り: {remaining}件")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2

    try:
        return run(args, settings)
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            print(
                f"エラー: データベース({settings.db_path})が使用中です。"
                "ほかの処理が終わってから再実行してください。",
                file=sys.stderr,
            )
            return 3
        raise


if __name__ == "__main__":
    sys.exit(main())
