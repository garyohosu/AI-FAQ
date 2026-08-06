# AI-FAQ Memory Index

このファイルは `knowledge/` 配下の資料がどこに何を持っているか、用語、
優先順位、判断、未解決事項をまとめた総合索引です。

ここでいう「メモリー」はLLMの再学習ではありません。人間が管理する
Markdownを正とし、SQLiteの `memory_entries` はその構造化コピーに過ぎません。
AIはこのファイルへの変更案を提案できますが、人間の承認なしに書き換えて
確定させることはありません。

詳細は以下へ分割しています。

- 用語: [memory/terminology.md](memory/terminology.md)
- 資料ごとの注意・優先順位: [memory/source-notes.md](memory/source-notes.md)
- 管理者の判断: [memory/decisions.md](memory/decisions.md)
- 未解決・不足知識: [memory/gaps.md](memory/gaps.md)

## Source Map

### SRC-001
- path: knowledge/public/windows-network-troubleshooting.md
- contains: Windowsのネットワークアダプター再起動、Wi-Fi接続不可時の一般的な確認手順
- sheets:
- scope: PUBLIC
- category: network
- owner: IT管理部
- priority: normal
- status: active
- last_reviewed: 2026-08-06
- valid_until:
- notes: 一般公開情報のみ。社内固有の申請先等は含まない。

### SRC-002
- path: knowledge/public/general-it-glossary.csv
- contains: VPN・DHCP・DNS・Wi-Fi・ファイアウォール等の一般IT用語集
- sheets:
- scope: PUBLIC
- category: other
- owner: IT管理部
- priority: low
- status: active
- last_reviewed: 2026-08-06
- valid_until:
- notes: 用語の一般的な説明のみ。社内特有の運用ルールは含まない。

### SRC-003
- path: knowledge/public/sample-troubleshooting.xlsx
- contains: 架空サンプルのトラブルシューティング一覧(プリンター・ネットワーク)
- sheets: プリンター, ネットワーク
- scope: PUBLIC
- category: troubleshooting
- owner: IT管理部
- priority: normal
- status: active
- last_reviewed: 2026-08-06
- valid_until:
- notes: 動作確認用の架空データ。実在の機器・担当者・資産番号は含まない。非表示シート「非表示メモ」は取り込み対象外。

## Terminology

社内用語・略語は [memory/terminology.md](memory/terminology.md) を参照してください。

## Routing Rules

- 承認済み `knowledge_articles` を最優先で検索する。
- 次に `authoritative`/`high` の取り込み資料、その後通常優先度の資料を検索する。
- 上記で回答できない場合のみ、`MEMORY.md`/`memory/*.md` を用語・所在確認の
  補助記憶として使う(回答本文の唯一の根拠にはしない)。
- 公開一般質問かつ `safe_for_external_research=true` の場合のみ
  Antigravity CLI (`agy`) でWeb調査する。
- 機密性の高い質問・人間対応の希望・行き詰まりは、取り込み資料の一致より
  先に判定し、人間へ引き継ぐ(資料の偶然の一致で握りつぶさない)。
- 判断に迷う場合は外部送信せず人間へ引き継ぐ。

## Important Decisions

管理者判断の詳細は [memory/decisions.md](memory/decisions.md) を参照してください。

## Known Gaps

未解決事項の詳細は [memory/gaps.md](memory/gaps.md) を参照してください。

## Retired or Forbidden Sources

現時点で `retired` または `forbidden` に指定された資料はありません。
資料を廃止・参照禁止にする場合は、Source Mapの該当エントリの `status` を
`retired`/`forbidden` に変更し、理由をここへ追記してください(削除はしない)。
