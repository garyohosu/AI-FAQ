# AI-FAQ

社内IT管理部門向けの「学習型AI FAQ」MVPです。

社員からの質問に対して、承認済みの社内知識を優先して回答します。回答できない場合は、安全に公開情報を調査するか、IT管理者へ引き継ぎます。IT管理者が回答した内容は承認済み知識として保存され、次回以降の同じ質問や登録済みの言い換えに再利用されます。

現在はWindows向けのCLIアプリケーションです。Web UI、認証サーバー、クラウドAPIの直接呼び出しはMVPの対象外です。

## まず使うコマンド

初回または更新後に、リポジトリを最新化します。

```powershell
cd C:\PROJECT\AI-FAQ
git pull
```

以後、通常利用では仮想環境の手動有効化や`pip install`は不要です。

### IT管理者として起動

```powershell
C:\PROJECT\AI-FAQ\start-admin.cmd
```

エクスプローラーから`start-admin.cmd`をダブルクリックしても起動できます。

### 質問者として起動

```powershell
C:\PROJECT\AI-FAQ\start-chat.cmd
```

### 起動メニューを表示

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.cmd
```

表示されるメニュー:

```text
AI-FAQ Launcher
  1. IT administrator
  2. User chat
  3. Environment check
  Q. Quit
```

起動スクリプトは次の処理を自動で行います。

1. AI-FAQのリポジトリへ移動
2. `.venv`が無ければPython仮想環境を作成
3. `aifaq`または`aifaq-admin`が未導入ならインストール
4. 管理者画面、質問者チャット、環境診断のいずれかを起動

通常起動では毎回インストールしません。

## 更新と再インストール

AI-FAQを更新してから管理者画面を開く場合:

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.ps1 -Update -Mode Admin
```

質問者画面を更新後に開く場合:

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.ps1 -Update -Mode Chat
```

強制的に再インストールする場合:

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.ps1 -Install -Mode Admin
```

更新または再インストール時は、先に実行中の`aifaq chat`、`aifaq watch`、`aifaq-admin`を終了してください。Windowsでは実行中の`.exe`を置き換えられず、`WinError 32`になることがあります。

## IT管理者の使い方

日常の回答作業には、従来の長い`aifaq pending answer`ではなく、管理者専用の`aifaq-admin`を使います。

### 対話式で回答

```powershell
aifaq-admin
```

未回答が1件だけなら自動選択されます。複数ある場合は、表示された受付番号を入力します。

```text
未回答の質問:
  #2  社内Wi-Fiにつながりません

受付番号 #2 を選択しました。
質問: 社内Wi-Fiにつながりません

回答> 情報システム担当へ電話で連絡してください。

この内容で登録しますか？ [Y/n] y

回答を登録しました。承認済み知識: KB-2
質問者へ回答済みです。
```

回答者名はWindowsのログインユーザー名を自動的に使用します。別名を記録する場合だけ`--by`を指定します。

```powershell
aifaq-admin --by hantani
```

### 1コマンドで回答

受付番号と回答が分かっている場合:

```powershell
aifaq-admin 2 "情報システム担当へ電話で連絡してください。"
```

この形式ではPowerShellのパイプや回答用ファイルは不要で、確認画面も省略されます。

### 未回答一覧だけ表示

```powershell
aifaq-admin --list
```

### 分類や言い換えも登録

通常は省略できます。検索精度を高めたい場合だけ指定します。

```powershell
aifaq-admin 2 "情報システム部へ連絡してください。" `
  --category network `
  --tags "wifi,連絡" `
  --variants "無線LANにつながらない,会社のWi-Fiが使えない"
```

主なオプション:

```text
--by NAME              回答者名
--category CATEGORY    分類。既定値はother
--tags TAGS            カンマ区切りのタグ
--variants VARIANTS    カンマ区切りの質問の別表現
--valid-until DATE     回答の有効期限
--reason TEXT          登録理由
--list                 未回答一覧だけ表示
--json                 JSON形式で出力
```

## 質問者の使い方

### 対話モード

```powershell
aifaq chat --requester 山田
```

起動スクリプトを使う場合は、次だけで開始できます。

```powershell
C:\PROJECT\AI-FAQ\start-chat.cmd
```

対話例:

```text
AI-FAQ CLI
終了: /quit
状態確認: /status

あなた> 社内Wi-Fiにつながりません
AI-FAQ> 接続方法は有線と無線のどちらですか？
  1. 有線(LAN)
  2. 無線(Wi-Fi)
  3. 両方で発生
```

回答に必要な情報が不足している場合、AI-FAQは1回に1問、最大3回まで確認します。3回で解決しない場合や、社内固有情報・機密情報を含む場合はIT管理者へ引き継ぎます。

IT管理者へ引き継がれた後は、そのまま回答を待つか、いったん終了して後から確認できます。

### 単発で質問

```powershell
aifaq ask "Windows 11でネットワークアダプターを再起動する方法は？"
```

質問者名とthread IDを指定する場合:

```powershell
aifaq ask "質問文" --thread-id demo-001 --requester 山田
```

確認質問へ回答する場合:

```powershell
aifaq reply demo-001 "無線LANです"
```

現在の状態を1回だけ確認:

```powershell
aifaq status demo-001
```

回答が入るまで待機:

```powershell
aifaq watch demo-001
```

待機時間を指定する場合:

```powershell
aifaq watch demo-001 --interval 1 --timeout 120
```

## 環境診断

```powershell
aifaq doctor
```

起動スクリプトから実行する場合:

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.ps1 -Mode Doctor
```

`doctor`はPython、SQLite、FTS5、データベース書き込み、Antigravity CLIの存在とバージョン、内部資料のGit追跡状態を確認します。実際のAI問い合わせは行いません。

## 手動セットアップ

起動スクリプトを使用しない場合だけ必要です。

```powershell
cd C:\PROJECT\AI-FAQ
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
aifaq init
aifaq doctor
```

Python 3.12または3.13で動作確認しています。

`aifaq`が見つからない場合は、仮想環境が有効か確認するか、次の形式で直接実行できます。

```powershell
python -m aifaq doctor
python -m aifaq chat --requester 山田
python -m aifaq.admin_cli
```

## Antigravity CLI

公開情報の調査にはAntigravity CLIの`agy`を使用します。あらかじめインストールとログインを済ませてください。

```powershell
agy --version
```

動作確認済みバージョン:

```text
1.1.9
1.1.10
```

AI-FAQは次の形式でAntigravityを呼び出します。

```powershell
agy --print "調査指示" --output-format json
```

社内固有情報、個人情報、認証情報、内部IP、工場名、設備名などは決定論的ルールで検出し、外部AIへ送信しません。判断に迷う場合も送信せず、IT管理者へ引き継ぎます。

実AIを使わずにテストする場合:

```powershell
aifaq --fake-provider ask "テスト質問"
```

## 承認済み知識

人間が回答した内容は、`source_type=HUMAN`、`status=APPROVED`の承認済み知識としてSQLiteへ保存されます。

一覧表示:

```powershell
aifaq knowledge list
```

検索:

```powershell
aifaq knowledge search "Wi-Fi"
```

詳細表示:

```powershell
aifaq knowledge show 1
```

廃止:

```powershell
aifaq knowledge retire 1 --by hantani
```

再利用される主な条件:

- 登録時と同じ質問
- `--variants`に登録した別表現
- 検索語がそのまま含まれる質問

曖昧な未登録の言い換えは、安全のため自動回答せず確認質問や人間引き継ぎへ進む場合があります。

## Excel・CSV・テキスト資料の取り込み

`knowledge/`以下へ資料を配置します。

対応形式:

- `.xlsx`
- `.xlsm`（セル値のみ。マクロは実行しない）
- `.csv`
- `.tsv`
- `.md`
- `.txt`

対象ファイルを確認:

```powershell
aifaq knowledge scan
```

全体を差分取り込み:

```powershell
aifaq knowledge import
```

1ファイルだけ取り込み:

```powershell
aifaq knowledge import knowledge/public/sample.md
```

取り込み資料と承認済み知識を横断検索:

```powershell
aifaq knowledge search "Wi-Fi"
```

ファイルのSHA-256を保存し、変更されたファイルだけ再取り込みします。削除されたファイルは履歴上`MISSING`として記録します。

## AI調査結果の承認

Antigravityによる調査結果は、自動では正式な知識になりません。人間が確認・承認した結果だけが承認済み知識になります。

一覧:

```powershell
aifaq research list
```

未レビューだけ表示:

```powershell
aifaq research list --status PENDING
```

詳細確認:

```powershell
aifaq research show 1
```

承認:

```powershell
aifaq research approve 1 --approved-by hantani
```

修正版ファイルを承認:

```powershell
aifaq research approve 1 --approved-by hantani --answer-file corrected.md
```

却下:

```powershell
aifaq research reject 1 --approved-by hantani --reason "情報が古い"
```

期限切れとして処理:

```powershell
aifaq research reject 1 --approved-by hantani --expired
```

## MEMORY.md

`MEMORY.md`と`memory/*.md`は、LLMの学習データではなく、人間が管理する索引・注記です。Markdownを正としてDBへ同期します。

形式確認:

```powershell
aifaq memory validate
```

内容表示:

```powershell
aifaq memory show
```

DBへ同期:

```powershell
aifaq memory sync
```

`MEMORY.md`には次の6章が必要です。

```markdown
# AI-FAQ Memory Index

## Source Map
## Terminology
## Routing Rules
## Important Decisions
## Known Gaps
## Retired or Forbidden Sources
```

## 主要コマンド一覧

```powershell
aifaq init
aifaq doctor
aifaq ask "質問文"
aifaq reply THREAD "回答文"
aifaq chat --requester 山田
aifaq status THREAD
aifaq status --pending-id 12
aifaq watch THREAD
aifaq history THREAD

aifaq-admin
aifaq-admin --list
aifaq-admin 12 "回答本文"

aifaq pending list
aifaq pending show 12

aifaq knowledge scan
aifaq knowledge import
aifaq knowledge search "検索語"
aifaq knowledge list

aifaq research list
aifaq research show 1
aifaq research approve 1 --approved-by hantani
aifaq research reject 1 --approved-by hantani --reason "理由"

aifaq memory validate
aifaq memory show
aifaq memory sync
```

`--json`はサブコマンドより前に指定します。

```powershell
aifaq --json knowledge list
```

## 全体アーキテクチャ

```text
質問者CLI / 管理者CLI
        ↓
LangGraphワークフロー層
        ↓
Pydanticモデル層
        ↓
SQLiteリポジトリ層 -- data/aifaq.db
        ↓
承認済み知識・取り込み資料・MEMORY.md
        ↓
セキュリティ判定
        ↓
Antigravity CLI / FakeProvider
```

回答フロー:

```text
START
  ↓
質問の検証・セキュリティ判定
  ↓
承認済み知識と取り込み資料を検索
  ├─ 回答可能 → 回答を返す
  ├─ 情報不足 → 最大3回の確認質問
  ├─ 公開情報として安全 → Antigravityで調査
  └─ 社内固有・機密・矛盾・解決不能 → IT管理者へ引き継ぎ
```

## セキュリティ境界

次の情報は外部AIへ送信しません。

- パスワード、秘密鍵、APIトークン、認証コード
- メールアドレス、電話番号、社員番号などの個人情報
- 社内IP、内部ホスト名、内部ドメイン
- 社内手続き、申請先、担当者、共有フォルダ
- 工場名、設備名、資産番号
- アカウント停止、権限変更、退職者処理などの重要操作

そのほかの制限:

- Antigravityは`shell=False`で呼び出す
- `--dangerously-skip-permissions`は使用しない
- 人間回答や内部資料をAntigravityへ渡さない
- 危険な操作を自動実行せず、FAQとして手順を示すだけ
- 回答本文の空文字、文字数超過、危険な制御文字を拒否

## 内部資料をGitへ入れない運用

実データをGitへ登録してよいのは`knowledge/public/`だけです。

次のフォルダはREADME以外を`.gitignore`で除外します。

```text
knowledge/internal/
knowledge/policies/
knowledge/procedures/
knowledge/troubleshooting/
knowledge/inventory/
knowledge/inbox/
data/
```

`aifaq doctor`は、内部資料が誤ってGitで追跡されていないか確認します。

## 認証なしCLI版の利用範囲

このCLI版には認証・アクセス制御がありません。

次の環境を前提としています。

- 同じPC・同じOSユーザー
- 信頼された管理環境
- `data/aifaq.db`へのアクセスが管理された環境

データベースを読める利用者は、質問・回答履歴を参照できます。複数社員へ本番配布する前に、認証付きWeb UI、OS権限、ネットワーク分離などの追加対策が必要です。

## 環境変数

主な設定:

```text
AIFAQ_RESEARCH_PROVIDER     antigravity または fake
AIFAQ_RESEARCH_BIN          既定: agy
AIFAQ_RESEARCH_TRANSPORT    既定: arg
AIFAQ_RESEARCH_TIMEOUT      既定: 180秒
AIFAQ_RESEARCH_MODEL        使用モデル
AIFAQ_DB_PATH               既定: data/aifaq.db
AIFAQ_BUSY_TIMEOUT_MS       既定: 5000
AIFAQ_WATCH_INTERVAL        既定: 2.0秒
AIFAQ_MAX_ANSWER_CHARS      既定: 20000
AIFAQ_ADMIN_NAME            管理者名の既定値
```

試験用の別DBを使う例:

```powershell
$env:AIFAQ_DB_PATH = "data/test.db"
aifaq ask "テスト質問" --thread-id test-001
```

## テスト

```powershell
python -m pytest -q
python -m pytest -q --cov=aifaq --cov-report=term-missing
```

自動テストでは実AI CLIを呼びません。`FakeResearchProvider`またはモックを使用します。

GitHub ActionsはPython 3.12と3.13で実行します。

## トラブルシューティング

### `aifaq-admin`が見つからない

```powershell
cd C:\PROJECT\AI-FAQ
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
aifaq-admin --help
```

または起動スクリプトで再インストールします。

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.ps1 -Install -Mode Admin
```

### `WinError 32`でインストールできない

実行中のAI-FAQプロセスが`.venv\Scripts\aifaq.exe`を使用しています。

1. 質問者画面で`/quit`を入力
2. `aifaq watch`を`Ctrl+C`で終了
3. 管理者画面を終了
4. PowerShellを閉じて再実行

```powershell
C:\PROJECT\AI-FAQ\start-aifaq.ps1 -Install -Mode Admin
```

### Gitの`dubious ownership`エラー

自分で管理しているリポジトリであることを確認してから実行します。

```powershell
git config --global --add safe.directory C:/PROJECT/AI-FAQ
```

### PowerShellのパイプで日本語が壊れる

Windows PowerShell 5.1ではパイプ入力の文字コードが原因で日本語が壊れる場合があります。日常の管理者回答にはパイプを使わず、次を利用してください。

```powershell
aifaq-admin 2 "日本語の回答本文"
```

または対話式:

```powershell
aifaq-admin
```

## 既知の制約

- Web UIと認証機能は未実装
- PDF、Word、PowerPointの取り込みは未対応
- 日本語検索語抽出は簡易ヒューリスティック
- 意味的な矛盾検出ではなく簡易ルール
- `AIFAQ_RESEARCH_TRANSPORT=file`はAntigravity側の`read_file`許可が必要で、実機未検証
- `knowledge/`、`memory/`、`MEMORY.md`はリポジトリルートからの相対パスとして扱う

## 関連ドキュメント

- `START_GUIDE.md`: 起動スクリプトの詳細
- `ADMIN_GUIDE.md`: IT管理者向けの簡易操作
- `knowledge/README.md`: 知識資料の配置方法
- `memory/README.md`: MEMORY.mdの書式
- `instructions/`: 実装指示書
- `result-YYYY-MM-DD-NNN.md`: 実装結果
