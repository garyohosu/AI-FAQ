"""`aifaq`コマンドの統合エントリーポイント。"""

from __future__ import annotations

import sqlite3
import sys

from aifaq import cli
from aifaq.chat_session import cmd_chat
from aifaq.config import Settings


def main(argv: list[str] | None = None) -> int:
    parser = cli.build_parser()
    args = parser.parse_args(argv)

    # 既存CLIのコマンド定義をそのまま使い、chatだけセッション管理を改善した
    # 実装へ差し替える。
    if getattr(args, "command", None) == "chat":
        args.func = cmd_chat

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2

    try:
        return args.func(args, settings)
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            print(
                f"エラー: データベース({settings.db_path})がロックされています。"
                "他の aifaq プロセスが実行中でないか確認し、終了後に再実行してください。",
                file=sys.stderr,
            )
            return 3
        raise


if __name__ == "__main__":
    sys.exit(main())
