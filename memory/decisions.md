# Decisions (管理者判断)

見出し(`##`)単位で1件の決定を表します。`aifaq memory sync` により
memory_type=DECISION として `memory_entries` へ取り込まれます。

## 確認質問の上限は3回とする

2026-08-06時点の方針。4回以上の聞き返しは利用者負担が大きく、FAQという
性質から外れるため、`AIFAQ_MAX_CLARIFICATION_ROUNDS` の上限は3を超えない
設定とする(instruction-2026-08-06-002)。

## AI調査結果は自動でナレッジ化しない

Gemini CLIによるWeb調査の結果は、暫定回答としてその場で提示するのみとし、
`knowledge_articles` へ自動的に昇格させない。ナレッジ化するには人間の
明示的な承認操作が必要とする。
