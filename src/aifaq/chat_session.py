"""対話モードのセッション管理。

1つのLangGraph thread_idは1件の質問と、その確認質問だけに使用する。
回答完了後の次の入力には新しいthread_idを割り当て、完了済みチェックポイントを
再利用して確認状態が食い違うことを防ぐ。
"""

from __future__ import annotations

import uuid

from aifaq.cli import (
    _CHAT_BANNER,
    _chat_offer_wait,
    _format_status,
    _get_provider,
    _open,
)
from aifaq.graph import ReplyError, run_ask, run_reply
from aifaq.models import AnswerType


def _new_thread_id() -> str:
    return str(uuid.uuid4())


def _print_new_thread(thread_id: str) -> None:
    print(f"AI-FAQ> 新しい質問を開始します。thread_id: {thread_id}\n")


def cmd_chat(args, settings) -> int:
    """複数の質問を安全に扱える対話モード。

    - 確認質問への回答中だけ同じthread_idを使う。
    - 最終回答の後は、次の入力時に新しいthread_idへ切り替える。
    - DBと画面の確認状態が食い違った場合は、入力を捨てず新しい質問として
      自動復旧する。
    """

    thread_id = args.thread_id or _new_thread_id()
    last_thread_id = thread_id
    needs_new_thread = False

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
                status_target = thread_id if awaiting_clarification else last_thread_id
                status = repos.thread_status(thread_id=status_target)
                print(_format_status(status))
                print()
                continue
            if line.startswith("/"):
                print("不明なコマンドです。使えるのは /status と /quit です。\n")
                continue

            if not awaiting_clarification and needs_new_thread:
                thread_id = _new_thread_id()
                last_thread_id = thread_id
                needs_new_thread = False
                _print_new_thread(thread_id)

            if awaiting_clarification:
                try:
                    ans = run_reply(
                        repos,
                        settings,
                        provider,
                        thread_id=thread_id,
                        answer_text=line,
                    )
                except ReplyError as exc:
                    # 完了済みthread_idの再利用や外部プロセスによる状態変更で
                    # 確認状態が失われても、利用者の入力を捨てずに継続する。
                    old_thread_id = thread_id
                    thread_id = _new_thread_id()
                    last_thread_id = thread_id
                    awaiting_clarification = False
                    print(
                        "AI-FAQ> 確認状態が一致しなかったため、入力内容を"
                        "新しい質問として続けます。"
                    )
                    print(f"  旧thread_id: {old_thread_id}")
                    print(f"  新thread_id: {thread_id}")
                    print(f"  詳細: {exc}\n")
                    ans = run_ask(
                        repos,
                        settings,
                        provider,
                        thread_id=thread_id,
                        question=line,
                        requester=args.requester,
                    )
            else:
                ans = run_ask(
                    repos,
                    settings,
                    provider,
                    thread_id=thread_id,
                    question=line,
                    requester=args.requester,
                )

            last_thread_id = thread_id
            awaiting_clarification = ans.answer_type == AnswerType.NEEDS_CLARIFICATION

            if awaiting_clarification:
                print(f"AI-FAQ> {ans.question}")
                for i, option in enumerate(ans.options, start=1):
                    print(f"  {i}. {option}")
                print()
                continue

            if ans.answer_type == AnswerType.PENDING_HUMAN:
                print(
                    f"AI-FAQ> IT管理者へ引き継ぎました。"
                    f"受付番号は{ans.pending_id}です。"
                )
                if not _chat_offer_wait(args, settings, thread_id):
                    return 0
                needs_new_thread = True
                print()
                continue

            print(f"AI-FAQ> {ans.answer}")
            if ans.sources:
                for source in ans.sources:
                    print(f"  出典: {source.url}")
            if ans.notice:
                print(f"  注意: {ans.notice}")
            print()

            # 最終回答を得たthread_idは完了済み。次の利用者入力は別質問として
            # 新しいthread_idを割り当てる。
            needs_new_thread = True
    finally:
        conn.close()
