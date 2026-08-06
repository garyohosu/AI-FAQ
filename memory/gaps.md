# Gaps (未解決・不足知識)

見出し(`##`)単位で1件を表します。`aifaq memory sync` により
memory_type=GAP として `memory_entries` へ取り込まれます。

## 内部資料のサンプルが未整備

現時点では `knowledge/internal/` 等に実データが投入されていない
(MVPリリース直後のため)。実運用時にIT管理部が資料を配置し、
`aifaq knowledge import` と `MEMORY.md` のSource Map登録を行う必要がある。

## 参照渡し(AIFAQ_RESEARCH_TRANSPORT=file)が実機未検証

Antigravity CLI のヘッドレス実行は `read_file` を自動拒否するため、
一時ファイルへ調査指示を書いて参照させる方式は、agy側の
`permissions.allow` を設定しない限り動作しない。利用者のagy全体設定を
変更しない判断をしたため、この経路は実機で確認できていない。
既定の `arg`(引数渡し)は実機で成功パスを確認済み。

## ひらがなだけの語が検索語にならない

`util.extract_search_terms` は漢字・カタカナ・英数字の連なりを内容語として
取り出し、ひらがなを文法要素とみなして捨てる。このため「ぱそこん」の
ようにひらがなのみで書かれた語は検索語にならない。実運用で問題になる場合は
`question_variants` に別表記を登録して吸収する。
