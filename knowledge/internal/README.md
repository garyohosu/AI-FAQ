# knowledge/internal/

社内固有の内部資料を置く場所です。**このフォルダの実ファイルはGit管理対象
外です**(このREADME以外は `.gitignore` により除外されます)。

- 内部システム名、資産番号、社内手続きなど、社外に出せない情報を含む資料を
  ここに置いてください。
- `aifaq knowledge import` の対象にはなりますが、`scope` は既定で
  `INTERNAL` として扱われます。
- ここに置いた内容がGemini CLI(外部AI)へ送信されることはありません。
