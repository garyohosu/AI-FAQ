# Gaps (未解決・不足知識)

見出し(`##`)単位で1件を表します。`aifaq memory sync` により
memory_type=GAP として `memory_entries` へ取り込まれます。

## 内部資料のサンプルが未整備

現時点では `knowledge/internal/` 等に実データが投入されていない
(MVPリリース直後のため)。実運用時にIT管理部が資料を配置し、
`aifaq knowledge import` と `MEMORY.md` のSource Map登録を行う必要がある。

## Gemini CLIの実認証環境での動作未確認

本リポジトリの初期実装時点では、Gemini CLIの認証ティア制限により実際の
Web調査呼び出しの成功パスを確認できていない。導入環境で
`aifaq doctor` に加えて実際の `ask` 呼び出しを一度確認することを推奨する。
