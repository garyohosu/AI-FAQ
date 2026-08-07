"""IT管理者向けの常駐監視画面。

未回答が無いときも終了せず、新しい質問が登録されるまで定期確認する。
質問が届いたら、その場で回答し、回答後も監視を続ける。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

from aifaq import db
from aifaq.config import Settings
from aifaq.models import PendingStatus
from aifaq.repositories import AlreadyAnsweredError, Repositories
from aifaq.security import AnswerValidationError, validate_human_answer


class MonitorExit(Exception):
    """管理者が常駐監視の終了を選んだ。"""


def _open(settings: Settings) -> tuple[sqlite3.Connection, Repositories]:
    conn = db.connect(settings)
    db.init_db(conn)
    return conn, Repositories.build(conn)


def _resolve_admin_name(explicit_name: str | None) -> str:
    for value in (
        explicit_name,
        os.environ.get("AIFAQ_ADMIN_NAME"),
        os.environ.get("USERNAME"),
        os.environ.get("USER"),
    ):
        if value and value.strip():
            return value.strip()

    value = input("回答者名> ").strip()
    if not value:
        raise MonitorExit
    return value


def _fetch_open_rows(settings: Settings):
    conn, repos = _open(settings)
    try:
        return repos.pending.list(PendingStatus.OPEN)
    finally:
        conn.close()


def _choose_row(rows):
    print("\n未回答の質問:")
    for row in rows:
        print(f"  #{row['id']}  {row['original_question']}")

    if len(rows) == 1:
        row = rows[0]
        print(f"\n受付番号 #{row['id']} を選択しました。")
        return row

    choices = {int(row["id"]): row for row in rows}
    while True:
        raw = input("\n回答する受付番号（終了は /quit）> ").strip()
        if raw.lower() in {"/quit", "quit", "q"}:
            raise MonitorExit
        try:
            pending_id = int(raw)
        except ValueError:
            print("受付番号を数字で入力してください。")
            continue
        if pending_id not in choices:
            print("一覧にある受付番号を入力してください。")
            continue
        return choices[pending_id]


def _show_question(settings: Settings, row) -> None:
    conn, repos = _open(settings)
    try:
        print("\n------------------------------")
        print(f"受付番号: {row['id']}")
        print(f"質問: {row['original_question']}")
        clarifications = repos.clarifications.list_for_thread(row["thread_id"])
        if clarifications:
            print("確認履歴:")
            for item in clarifications:
                print(f"  {item.round_no}. {item.question}")
                if item.answer:
                    print(f"     回答: {item.answer}")
        print("------------------------------")
    finally:
        conn.close()


def _read_answer() -> str:
    answer = input("回答（終了は /quit）> ").strip()
    if answer.lower() in {"/quit", "quit"}:
        raise MonitorExit
    return answer


def _confirm(answer: str) -> bool:
    print("\n登録する回答:")
    print(answer)
    choice = input("\nこの内容で登録しますか？ [Y/n] ").strip().lower()
    if choice in {"/quit", "quit"}:
        raise MonitorExit
    return choice not in {"n", "no"}


def _answer_pending(
    settings: Settings,
    *,
    pending_id: int,
    answer: str,
    approved_by: str,
) -> tuple[int, int]:
    answer = validate_human_answer(answer, max_chars=settings.max_answer_chars)
    conn, repos = _open(settings)
    try:
        knowledge_id = repos.pending.answer(
            pending_id,
            answer_text=answer,
            category="other",
            tags=[],
            variants=[],
            approved_by=approved_by,
            valid_until=None,
            change_reason="IT管理者モニターから回答",
        )
        remaining = len(repos.pending.list(PendingStatus.OPEN))
        return knowledge_id, remaining
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aifaq-admin-monitor",
        description="IT管理者向けの常駐質問監視画面",
    )
    parser.add_argument("--by", dest="approved_by", help="回答者名")
    parser.add_argument(
        "--interval",
        type=float,
        help="未回答を確認する間隔（秒）。省略時はAIFAQ_WATCH_INTERVALまたは2秒",
    )
    return parser


def monitor(args: argparse.Namespace, settings: Settings) -> int:
    interval = args.interval or settings.watch_interval_seconds
    if interval < settings.watch_min_interval_seconds:
        print(
            f"エラー: --interval は {settings.watch_min_interval_seconds}秒以上にしてください。",
            file=sys.stderr,
        )
        return 2

    try:
        approved_by = _resolve_admin_name(args.approved_by)
    except (EOFError, KeyboardInterrupt, MonitorExit):
        print("\n終了します。")
        return 0

    print("AI-FAQ IT管理者モニター")
    print(f"回答者: {approved_by}")
    print(f"新しい質問を{interval:g}秒ごとに確認します。Ctrl+Cまたは/quitで終了。")

    waiting_message_shown = False
    try:
        while True:
            rows = _fetch_open_rows(settings)
            if not rows:
                if not waiting_message_shown:
                    print("未回答の質問はありません。新しい質問を待っています...")
                    waiting_message_shown = True
                time.sleep(interval)
                continue

            waiting_message_shown = False
            print("\a\n新しい質問が届きました。")
            row = _choose_row(rows)
            _show_question(settings, row)

            answer = _read_answer()
            if not answer:
                print("回答が空のため登録しませんでした。監視を続けます。")
                continue
            if not _confirm(answer):
                print("登録しませんでした。監視を続けます。")
                continue

            try:
                knowledge_id, remaining = _answer_pending(
                    settings,
                    pending_id=int(row["id"]),
                    answer=answer,
                    approved_by=approved_by,
                )
            except AnswerValidationError as exc:
                print(f"エラー: {exc}", file=sys.stderr)
                continue
            except AlreadyAnsweredError as exc:
                print(f"エラー: {exc}", file=sys.stderr)
                continue
            except ValueError as exc:
                print(f"エラー: {exc}", file=sys.stderr)
                continue

            print(f"\n回答を登録しました。承認済み知識: KB-{knowledge_id}")
            print("質問者へ回答済みです。")
            print(f"未回答の残り: {remaining}件")
            print("監視を続けます。")
    except (KeyboardInterrupt, MonitorExit):
        print("\nIT管理者モニターを終了します。")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2

    try:
        return monitor(args, settings)
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            print(
                f"エラー: データベース({settings.db_path})が使用中です。"
                "少し待ってから再実行してください。",
                file=sys.stderr,
            )
            return 3
        raise


if __name__ == "__main__":
    sys.exit(main())
