# 引き継ぎメモ (2026-08-06 17:00時点)

## 現在の状態

- `instruction-2026-08-06-004.md`(001〜004統合)の実装は完了し、
  `origin/main` へpush済みです。
- 最終コミット: `5fc4e19b3a3cce68e5ddca453ae70220152d83c0`
- 判定: `SUCCESS_WITH_LIMITATIONS`(詳細は `result-2026-08-06-004.md`)
- `git status --short` はクリーン(未コミットの変更なし)。
- テスト: `python -m pytest -q` → 84 passed、カバレッジ全体84%。

## 何が動くか

```bash
python -m venv .venv && .venv\Scripts\activate  # 未作成の場合
pip install -e ".[dev]"
python -m aifaq doctor
python -m aifaq init
python -m aifaq --fake-provider ask "Windows 11でネットワークアダプターを再起動する方法は？"
```

`--fake-provider` を付けなければ実際のGemini CLIを使いますが、下記の
未解決事項1の通り、本環境では実認証が通らず成功パスを確認できていません。

## 次にやること(優先度順)

1. **Gemini CLIの実認証環境での動作確認**(最優先)
   - 本環境のGemini CLIアカウントは `IneligibleTierError`(利用ティア
     制限)で実際の問い合わせができませんでした。
   - 認証可能な環境で `python -m aifaq ask "<公開一般質問>"` を
     `--fake-provider` を付けずに実行し、`src/aifaq/providers/gemini_cli.py`
     の `_extract_app_json` が実際のCLI出力形式(JSON包みのキー名)と
     整合するか確認してください。ズレていれば同ファイルの
     `_extract_app_json` / `_SYSTEM_PROMPT` を調整。

2. **AI調査結果のナレッジ昇格コマンドの要否を判断**
   - instruction-001は `research approve <id>` 相当のコマンドを想定して
     いましたが、instruction-004のCLI一覧(完了条件の基準)には含まれて
     いなかったため未実装です。
   - 必要なら `aifaq research approve <research_run_id> --by NAME` を
     追加し、`research_runs` → `knowledge_articles(source_type=APPROVED_AI)`
     への昇格処理を実装する(`src/aifaq/repositories.py` の
     `ResearchRunRepository`・`KnowledgeRepository` を拡張)。

3. **`source_import_runs` テーブルへの書き込み**
   - スキーマ(`src/aifaq/db.py`)は作成済みですが、
     `src/aifaq/ingestion.py` の `import_all()` の集計結果を実際にこの
     テーブルへINSERTする処理が未実装です。戻り値としては取得できるので、
     `cli.py` の `cmd_knowledge_import` から書き込む形が簡単です。

4. **`check_source_conflicts` の高度化**
   - 現状は `routing.py`/`graph.py` 内で、`priority=authoritative` の
     資料が2件以上あり内容の類似度が低い場合のみ「矛盾」とする簡易実装
     です。意味的な矛盾検出ではありません。

5. **Python 3.12/3.13での動作確認**
   - 本環境にはPython 3.14.0のみが入っており、`pyproject.toml` が要求する
     3.12系では未検証です(3.12+互換の構文のみ使用しているはずですが
     未確認)。

6. **`cli.py` のテストカバレッジ向上(58%)**
   - `knowledge show/retire/status/rebuild-index` 等、一部サブコマンドの
     単体テストが未整備です(`tests/test_cli.py` に追加していく想定)。

## 参照ファイル

- 詳細な実装内容・設計判断・テスト結果: `result-2026-08-06-004.md`
- 使い方・アーキテクチャ・セキュリティ境界: `README.md`
- 作業中の気づき: `dream.md`(Dreamingタイム記録)
- 元の指示書: `instructions/instruction-2026-08-06-00{1,2,3,4}.md`

## 注意事項

- `knowledge/internal/` 等の実データはGit管理対象外です。実運用データを
  投入する場合は `MEMORY.md` の Source Map へ登録し、`aifaq knowledge
  import` を実行してください。
- `data/aifaq.db` はGit管理対象外です(SQLite本体・LangGraphチェック
  ポイント)。
- コマンド実行はリポジトリ直下(`C:\PROJECT\AI-FAQ`)から行ってください
  (`knowledge/`・`memory/`・`MEMORY.md` を相対パスで参照するため)。
