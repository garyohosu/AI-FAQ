# 引き継ぎメモ (2026-08-06 instruction-005 完了時点)

## 現在の状態

- `instruction-2026-08-06-005.md`(Antigravity移行)の実装は完了し、
  `origin/main` へpush済みです。
- 判定: `SUCCESS_WITH_LIMITATIONS`(詳細は `result-2026-08-06-005.md`)
- テスト: `python -m pytest -q` → **193 passed**、全体カバレッジ **90%**
  (Python 3.12.10 / 3.13.1 の両方で確認済み)
- 実Antigravity(`agy`)によるWeb調査の**成功パスを実機で確認済み**。

## 何が動くか

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
python -m aifaq doctor
python -m aifaq init
python -m aifaq knowledge import --actor 名前
python -m aifaq ask "Windows 11でネットワークアダプターを再起動する公式手順は？"
python -m aifaq research list
python -m aifaq research approve 1 --approved-by 名前
```

`--fake-provider` を付けるか `AIFAQ_RESEARCH_PROVIDER=fake` を設定すると、
実AI CLIを呼ばずに動かせます。

## 004からの主な変更

- Gemini CLI → **Antigravity CLI (`agy`)** へ移行。
  呼び出しは `agy --print "<調査指示>" --output-format json`。
- 環境変数は `AIFAQ_RESEARCH_*` へ統一(`AIFAQ_GEMINI_BIN` は非推奨警告)。
- `aifaq research list/show/approve/reject` を追加。
- `source_import_runs` の実書き込み、DBスキーマv2(冪等マイグレーション付き)。
- GitHub Actions CI(Python 3.12 / 3.13、FakeProvider固定)。
- **検索の不具合を修正**(下記)。

## 特に知っておくべき挙動

1. **agyの `status: "ERROR"` は全体failureではない**。調査中に1件でも
   ツール呼び出しが失敗すると、最終回答が完全でも `status=ERROR` になる。
   本実装は本文が取れる限り回答を採用し、`error` を警告として添える。
   `providers/antigravity.py` の `_extract_response_text` を参照。
2. **検索は段階的フォールバック**。質問文全体のフレーズ検索 → 語のOR検索
   (FTS5) → 語のLIKE検索 → 全体LIKE。語の抽出は
   `util.extract_search_terms`(漢字・カタカナ・英数字を取り、ひらがなを
   捨てる)。関連度は**必ず全検索語**に対して測ること(3文字以上の語だけで
   測ると分母が縮んで過大評価され、無関係な資料を拾う)。
3. **`routing.decide_route` の順序は安全上重要**。
   承認済み知識 → **引き継ぎ判定** → 取り込み資料 の順。
   取り込み資料を先に見ると、機密質問が資料の偶然の一致で回答されてしまう
   (実際に `knowledge/README.md` で発生し修正済み)。順序を変えないこと。
4. **`knowledge/**/README.md` は取り込まない**(`ingestion.EXCLUDED_FILENAMES`)。

## 次にやること(優先度順)

1. **note記事の作成**(instruction-005 §7)
   - §7.1の作成条件はすべて満たしているが、本セッションでは検索・
     ルーティング修正の検証を優先したため未作成。
   - `note/README.md` と `note/learning-ai-faq-for-beginners.md` を作成する。
     構成は §7.3 / §7.4 をそのまま使える。
   - 実際に確認できていない機能を「動いた」と書かないこと(§7.5)。

2. **参照渡し(`AIFAQ_RESEARCH_TRANSPORT=file`)の実機検証**
   - agyのヘッドレス実行は `read_file` を自動拒否する。
     `C:\Users\garyo\.gemini\antigravity-cli\settings.json` に
     `permissions.allow` を追加すれば検証できる見込み。
   - 利用者のagy全体設定を変更する判断が必要なため今回は未実施。

3. **検索の関連度閾値の再評価**
   - `util._MIN_RELEVANCE_RATIO = 0.4` は手元の事例だけで決めた値。
     実データを増やして再調整する。

4. **薄いWeb UI**(instruction-005 §6の次段階)
   - 既存サービス層(`graph.run_ask` / `run_reply`、`repositories`)を
     そのまま呼ぶ形で追加する。

## 参照ファイル

- 今回の詳細(実機確認内容・修正した不具合): `result-2026-08-06-005.md`
- 使い方・アーキテクチャ・セキュリティ境界: `README.md`
- 作業中の気づき: `dream.md`
- 前回の結果: `result-2026-08-06-004.md`

## 注意事項

- `knowledge/internal/` 等の実データと `data/` はGit管理対象外です。
- コマンド実行はリポジトリ直下(`C:\project\AI-FAQ`)から行ってください。
- `agy` は作業中に 1.1.9 → 1.1.10 へ自動更新されました。バージョンにより
  `--output-format` の有無が変わるため、挙動が変わったら
  `agy --help` を先に確認してください。
