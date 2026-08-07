# AI-FAQ 簡単起動ガイド

## 更新後に一度だけ行うこと

```powershell
cd C:\PROJECT\AI-FAQ
git pull
```

以後は、仮想環境の有効化や `cd` は不要です。

## IT管理者として起動

PowerShellから:

```powershell
C:\PROJECT\AI-FAQ\start-admin.cmd
```

エクスプローラーから `start-admin.cmd` をダブルクリックしても起動できます。

## 質問者として起動

```powershell
C:\PROJECT\AI-FAQ\start-chat.cmd
```

## 起動メニューを表示

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.cmd
```

次のメニューが表示されます。

```text
AI-FAQ Launcher
  1. IT administrator
  2. User chat
  3. Environment check
  Q. Quit
```

## スクリプトが自動で行う処理

1. リポジトリのフォルダへ移動
2. `.venv` が無ければPython仮想環境を作成
3. `aifaq` / `aifaq-admin` が未インストールならインストール
4. 指定された画面を起動

通常起動では、毎回 `pip install` は実行しません。

## 更新と再インストールもまとめて行う

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.ps1 -Update -Mode Admin
```

これは `git pull --ff-only` と再インストールを行ってから管理者画面を開きます。ほかのAI-FAQ画面が起動中だと実行ファイルを更新できないため、先に終了してください。

## 強制的に再インストールする

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.ps1 -Install -Mode Admin
```

## 質問者名を指定する

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.ps1 -Mode Chat -Requester 山田
```
