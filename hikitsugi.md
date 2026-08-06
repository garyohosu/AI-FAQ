# 引き継ぎメモ (2026-08-06 instruction-006 完了時点)

## 現在の状態

- `instruction-2026-08-06-006.md`(人間回答ループの完成)の実装は完了し、
  `origin/main` へpush済みです。
- 判定: `SUCCESS_WITH_LIMITATIONS`(詳細は `result-2026-08-06-006.md`)
- テスト: `python -m pytest -q` → **242 passed**、全体カバレッジ **91%**
  (Python 3.12.10 / 3.13.1 の両方で確認済み)
- 2ターミナルでの人間回答ループを実機で確認済み(回答から3.2秒でwatchが検知)

## 何が動くか(2ターミナル運用)

同じフォルダで2つのターミナルを開きます。

```bash
# ターミナルA(質問者)
aifaq ask "社内のPC交換申請先を教えてください" --thread-id demo-001 --requester 山田
aifaq watch demo-001 --interval 1 --timeout 120

# ターミナルB(IT管理者)
aifaq pending list
echo "PC交換申請はIT管理担当へ提出してください。" | \
  aifaq pending answer 1 --approved-by hantani --category procedure

# ターミナルA(後から確認する場合)
aifaq status demo-001
```

対話モード:

```bash
aifaq chat --requester 山田      # /status で状態確認、/quit で終了
```

実機試験は本番知識を汚さないよう専用DBで:

```bash
AIFAQ_DB_PATH=data/test.db aifaq ask "テスト質問" --thread-id t-001
```

## 005からの主な変更

- `aifaq status` / `aifaq watch` / `aifaq chat` を追加。
- `pending answer` が**元のpendingへ回答本文・回答者・回答日時を保存**し、
  元のthread_idへ紐付けて `query_history` へ記録するようになった。
- DBスキーマ v3(`pending_questions` に `answer_text` 等5列を冪等追加)。
- SQLite: WAL・`busy_timeout`・`synchronous=NORMAL` を接続ごとに設定。
  上限付き再試行(`db.with_lock_retry`)。
- 回答本文の検証(空・長すぎ・制御文字を拒否)。

## 特に知っておくべき挙動

1. **LangGraphのチェックポイントは進めていない**。業務状態はSQLite側が正で、
   `status` / `watch` は `pending_questions` から回答を返す。理由は
   `result-2026-08-06-006.md` の §5 とREADMEに記載。ここを変えるときは
   必ずその理由を読むこと。
2. **`watch` はポーリングのたびに接続を開き直す**。DBを長時間ロックしない
   ため。進捗表示は**標準エラー**へ出す(`--json` の標準出力を壊さない)。
3. **二重回答は `UPDATE ... WHERE id=? AND status='OPEN'` の更新件数で防ぐ**。
   SELECTしてからUPDATEするだけでは、2ターミナルから同時実行されたときに
   両方通ってしまう。別プロセス2つでの検証テストあり。
4. **`AlreadyAnsweredError` は `ValueError` を継承している**。従来の
   `answer()` が `ValueError` を投げていたため、既存呼び出し側との互換性を
   保つ目的。
5. **PowerShell の `echo ... | aifaq pending answer` は先頭にBOMを付ける**。
   stdin読み込み時に除去している(除去しないと制御文字として拒否される)。

## 次にやること(優先度順)

1. **未登録の言い換えでの知識再利用**(未達事項、要判断)
   - 現状: 完全一致と `--variants` 登録済みの別表現は再利用される。
     未登録の言い換え(例「PC交換の申請先はどこですか」)はスコア0.500で
     閾値0.55に届かず、確認質問へ進む。
   - これは005で意図的に入れた安全側の設計(曖昧な一致で人間引き継ぎを
     飛ばさないため)。緩めると005のセキュリティ修正を巻き戻す恐れがある。
   - 案: 語のOR一致スコアを一致率に応じて可変にし、一致率が高いときだけ
     閾値を超えるようにする。**利用者の判断を仰いでから着手すること。**

2. **note記事の作成**(instruction-006 §12 / 005 §7)
   - §12の条件は満たしているが、上記1の未達があるため見送った。
   - 記事化に使える実機結果:
     - 2ターミナルの人間回答ループ(watchが3.2秒で検知)
     - `agy` による出典付きWeb調査の成功
     - 機密質問で外部Providerが呼ばれない(`research_runs` が0件)
     - Excel/CSV/Markdownの出典表示(シート名・見出し・行範囲)
   - コマンド例と失敗例はREADMEの「2ターミナルでの使い方」がそのまま使える。
   - スクリーンショット候補: ターミナルAのwatch待機中→回答表示の瞬間、
     `aifaq status` の出力、`aifaq chat` の対話。

3. **薄いWeb UI**
   - 業務ロジックはサービス層(`graph.run_ask` / `run_reply`、
     `repositories.thread_status`)にあり、CLIは薄い呼び出し。
     Web UIからも同じ層をそのまま使える。
   - 認証は必須(現CLIには無い。READMEの「認証なしCLI版の利用範囲」参照)。

4. **参照渡し(`AIFAQ_RESEARCH_TRANSPORT=file`)の実機検証**
   - agyのヘッドレス実行が `read_file` を自動拒否する。
     `C:\Users\garyo\.gemini\antigravity-cli\settings.json` に
     `permissions.allow` を追加すれば検証できる見込み(利用者の判断が必要)。

## 参照ファイル

- 今回の詳細: `result-2026-08-06-006.md`
- 前回(Antigravity移行・検索修正): `result-2026-08-06-005.md`
- 使い方・2ターミナル運用・セキュリティ境界: `README.md`
- 作業中の気づき: `dream.md`

## 注意事項

- `knowledge/internal/` 等の実データと `data/` はGit管理対象外です。
- コマンド実行はリポジトリ直下(`C:\project\AI-FAQ`)から行ってください。
- `tests/test_two_terminal_integration.py` は別プロセス起動を伴うため、
  実行に1分程度かかります。
