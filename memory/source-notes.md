# Source Notes (資料ごとの注意・優先順位・重複関係)

見出し(`##`)単位で1トピックを表します。複数資料が競合した場合の判断材料
として、`aifaq memory sync` により memory_type=ROUTING_RULE として
`memory_entries` へ取り込まれます。

## 優先順位の基本方針

同じ内容について複数の資料がある場合、次の順で優先する。

1. `priority: authoritative` に指定された資料
2. `priority: high` の資料
3. 通常優先度(`normal`)の資料
4. `status: stale` の資料(回答時に警告を付ける)

`status: retired` / `forbidden` の資料は検索対象から除外する。

## サンプル資料 (SRC-001, SRC-002) について

`knowledge/public/` のサンプルは一般公開情報のみを含み、社内固有の手順・
連絡先は含まない。実運用では `knowledge/internal/` 等に実データを置き、
`MEMORY.md` のSource Mapへ登録すること。
