# memory/

`MEMORY.md` の索引から分割された詳細メモです。人間が管理するMarkdownを
正とし、`aifaq memory sync` で `data/aifaq.db` の `memory_entries` へ
一方向に反映します(DBからMarkdownへ書き戻すことはありません)。

- `terminology.md`: 社内用語・略語・製品名・部門名の説明
- `source-notes.md`: 資料ごとの注意・優先順位・重複関係
- `decisions.md`: 管理者が決定した運用方針と理由
- `gaps.md`: 未回答・資料不足・要更新事項

## 安全規則

- パスワード・秘密鍵・APIトークン・認証コードを記録しない
- 個人情報そのものを記録しない
- 内部IP・ホスト名・資産番号などは必要性を確認し、記録する場合も外部AIへ
  送信しない(このリポジトリの `memory/` はAntigravity CLIへ渡されない)
- AIが変更案を提案する場合も、必ず提案状態にとどめ、人間の承認後に確定する
- 削除ではなく、原則として「廃止」「参照禁止」と理由を残す
