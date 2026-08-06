## 2026-08-06 16:40 Dreamingタイム

### 今回やったこと
- instruction-2026-08-06-001〜004を統合し、学習型AI FAQ MVPをゼロから実装
- Pydantic v2モデル、決定論的セキュリティ判定、SQLite(FTS5)リポジトリ層、
  Excel/CSV/TSV/Markdown/TXTの知識取り込み、MEMORY.md索引パーサー、
  LangGraph(確認質問ループ・interrupt/resume)、CLI、pytestテスト84件を実装
- knowledge/・memory/の安全なサンプルとREADMEを作成し、コミット・push

### 気づいたこと
- SQLite FTS5の既定トークナイザー(unicode61)は日本語を単語分割できず、
  部分一致検索が機能しなかった。`tokenize='trigram'`に切り替えることで
  日本語の部分一致検索が実用的になった(ただし3文字未満のクエリは
  ヒットしないため、短いクエリはLIKEへフォールバックする設計にした)。
- 本環境のGemini CLIは`IneligibleTierError`(利用ティア制限)により
  実際のWeb調査呼び出しの成功パスを確認できなかった。CLI仕様
  (`--help`、trusted-folders要件)は確認できたが、成功時のJSON出力形式は
  ドキュメントベースの推測実装にとどまる。
- 本環境にはPython 3.14.0のみが入っており、指示書が指定する3.12での
  検証はできなかった。

### 改善点
- `cli.py`のテストカバレッジが58%とやや低い。`knowledge show/retire/
  status/rebuild-index`等のサブコマンド単体テストを追加すると良い。
- `check_source_conflicts`(複数資料の矛盾検出)は類似度ベースの簡易実装。
  本格的な意味的矛盾検出には至っていない。
- `source_import_runs`テーブルへの実書き込みが未実装(スキーマのみ)。

### 次に試すとよさそうなこと
- 認証可能なGemini CLI環境で`aifaq ask`を実際に1回実行し、
  `GeminiCLIProvider._extract_app_json`が実データと整合するか確認する
- AI調査結果を人間承認でナレッジ化する`research approve`コマンドの追加
  (instruction-001が想定していたが、instruction-004のCLI一覧には
  含まれておらず今回は未実装とした)
- Python 3.12/3.13環境での動作確認
