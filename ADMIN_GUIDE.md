# AI-FAQ 管理者向け簡易操作

日常の回答作業では、従来の `aifaq pending answer` ではなく、管理者専用の `aifaq-admin` を使います。

## 更新後の準備

```powershell
git pull
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## いちばん簡単な使い方

```powershell
aifaq-admin
```

未回答が1件だけなら自動的に選ばれます。複数ある場合は、表示された受付番号を入力します。

画面例:

```text
未回答の質問:
  #2  社内Wi-Fiにつながりません

受付番号 #2 を選択しました。

質問: 社内Wi-Fiにつながりません
確認履歴:
  1. 接続方法は有線と無線のどちらですか？
     回答: 1

回答> わたしに電話で連絡してください。

この内容で登録しますか？ [Y/n] y

回答を登録しました。承認済み知識: KB-2
質問者へ回答済みです。
未回答の残り: 0件
```

回答者名は、Windowsのログインユーザー名を自動的に使います。別の名前を記録する場合だけ `--by` を付けます。

```powershell
aifaq-admin --by hantani
```

## 1コマンドで回答する

受付番号と回答が分かっている場合は、次の1行だけで登録できます。

```powershell
aifaq-admin 2 "わたしに電話で連絡してください。"
```

この形式では、PowerShellのパイプも回答用の一時ファイルも不要です。回答本文をコマンドに明示しているため、確認画面も省略します。

## 未回答一覧だけ見る

```powershell
aifaq-admin --list
```

## 必要な場合だけ詳細情報を付ける

通常は省略して構いません。

```powershell
aifaq-admin 2 "情報システム部へ連絡してください。" `
  --category network `
  --tags "wifi,連絡" `
  --variants "無線LANにつながらない,会社のWi-Fiが使えない"
```

## 従来コマンドについて

`aifaq pending answer` は、自動処理や詳細な管理情報を付けたい場合のために残しています。普段の人間回答では `aifaq-admin` を使ってください。
