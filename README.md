# AI-FAQ

社内IT管理部門向けの「学習型AI FAQ」MVP(CLIアプリケーション)です。

## 目的と対象利用者

- **対象利用者**: 社内IT管理部門(FAQへの回答・承認を行う管理者)と、
  それを利用する社員(質問者)。
- **目的**: よくある社内IT質問に対して、まず承認済みの社内知識で回答し、
  無ければ公開情報をWeb調査し、それでも無理なら人間(IT管理者)へ引き継ぐ。
  IT管理者が回答した内容は次回から自動的に再利用される。

Web UI・チャットボット・認証サーバー・ベクトルDB・クラウドAPI直接呼び出し
は本MVPの対象外です。

## 「学習」の意味

このプロジェクトでの「学習」は **LLMの再訓練・ファインチューニングでは
ありません**。人間(IT管理者)が回答・承認した内容を、版管理された
「承認済み知識」としてSQLiteへ蓄積し、次回以降の類似質問の検索に
再利用する仕組みを指します。AIが調査した内容や一般社員の質問は、
人間の承認なしに自動的に正式な知識へ昇格することはありません。

同様に `MEMORY.md` / `memory/*.md` の「メモリー」も、人間が管理する
Markdownを正とする索引・注記であり、モデルの学習データではありません。

## 全体アーキテクチャ

```text
CLI層 (aifaq/cli.py)
  ↓
LangGraphワークフロー層 (aifaq/graph.py, routing.py)
  ↓                                    ↑
Pydanticモデル層 (aifaq/models.py)     |
  ↓                                    |
SQLiteリポジトリ層 (aifaq/db.py, repositories.py) -- data/aifaq.db
  ↓                                    ↑
知識検索・取り込み層 (aifaq/ingestion.py) -- knowledge/*
メモリー索引層 (aifaq/memory.py)          -- MEMORY.md, memory/*.md
セキュリティ・外部送信可否判定層 (aifaq/security.py)
  ↓
AI CLIプロバイダー層 (aifaq/providers/*) -- Antigravity CLI / FakeProvider
```

`ResearchProvider` はProtocolで抽象化しており、`AntigravityProvider` を
将来 Claude Code CLI / Codex CLI / ローカルLLM 用のプロバイダーへ
差し替え可能です(`aifaq/providers/base.py`)。

```text
ResearchProvider (Protocol)
├─ AntigravityProvider     # 実環境用 (agy CLI)
└─ FakeResearchProvider    # 自動テスト用
```

### 回答フロー(LangGraph)

```text
START → validate_question → security_precheck → load_memory_context
      → search_approved_knowledge → search_imported_sources
      → check_source_conflicts → assess_answerability
          ├─ 承認済み知識/取り込み資料で回答可能 → compose_*_answer → record_history → END
          ├─ 情報不足かつ確認回数<3かつ安全に質問可能
          │     → create_clarification_question → ask_requester(interrupt)
          │     → merge_clarification → security_precheckへ戻る
          ├─ 公開一般質問として調査可能 → research_with_cli
          │     ├─ 十分な根拠 → compose_research_answer → record_history → END
          │     └─ 根拠不足/エラー → request_human(interrupt)
          └─ 内部/機密/矛盾/確認上限到達 → request_human(interrupt)
```

`request_human` と `ask_requester` はLangGraphの `interrupt()` を使い、
`thread_id` ごとにSQLite (`data/aifaq.db`) へチェックポイントを保存します。
CLIを一度終了しても、同じ `thread_id` を指定すれば別プロセスから再開
できます(`aifaq reply` で確認質問に回答、`aifaq pending answer` で
IT管理者が人間回答を登録)。

## セキュリティ境界

以下は決定論的ルール(`aifaq/security.py`)で検出し、外部AI(Antigravity CLI)
へは絶対に送信しません。判断に迷う場合も送信せず人間へ引き継ぎます。

- パスワード・秘密鍵・トークン・認証コードらしき語や値
- 個人情報(メールアドレス・電話番号・社員番号等)
- 社内IP・内部ホスト名・内部ドメイン
- 「社内」「弊社」「当社」「申請先」「担当者」「共有フォルダ」等の
  社内固有手続きを示す語、工場名・設備名・資産番号
- アカウント停止・権限変更・退職者処理などの重要操作

`knowledge/internal/` や `memory/` の内容がAntigravity CLIへ渡ることも
ありません(Web調査プロンプトには質問文だけを渡します)。

Antigravity CLIは `subprocess.run(shell=False)` で引数配列として呼び出し、
`--dangerously-skip-permissions` は使用しません。既定のヘッドレス実行では
ファイル書換・シェル実行などのツールはagy側で自動拒否されます
(Web検索・Web取得は権限フラグ無しで動作することを実機で確認済み)。

さらに、取り込み済み資料は「人間が回答として承認したもの」ではないため、
機密性の高い質問・人間対応の希望・行き詰まりの判定は、資料の一致より先に
行います(`routing.decide_route`)。資料の偶然の一致で人間引き継ぎを
握りつぶさないための順序です。

## セットアップ

```bash
# Python 3.12 / 3.13 で動作確認済み (CIでも両方をテストしています)
python -m venv .venv
source .venv/Scripts/activate   # Windows PowerShellの場合は .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Antigravity CLI の準備

このリポジトリは Antigravity CLI (`agy`) をサブプロセスとして呼び出します。
あらかじめ別途インストール・ログインしてください。

```bash
agy --version   # 動作確認は 1.1.9 / 1.1.10 で実施
```

### なぜ Gemini CLI から Antigravity へ移行したか

初期実装(instruction 001〜004)は Gemini CLI を前提にしていましたが、
利用環境のアカウントが `IneligibleTierError`(利用ティア制限)を返し、
実際の問い合わせができず、成功パスを一度も確認できませんでした。

一方 Antigravity CLI は、同じ利用者の既存プロジェクト
(`werewolf-game` の `config/agents.json`、`OracleCouncil` の
`adapters/agy.py`)で実運用の実績があり、本作業でも実機で
Web調査の成功パスを確認できたため、こちらへ移行しました。

### 呼び出し方式(実機で確認した内容)

```bash
agy --print "<調査指示>" --output-format json
```

`--output-format json` は次のエンベロープを返します。

```json
{"conversation_id": "...", "status": "SUCCESS", "response": "<本文>",
 "duration_seconds": 2.5, "num_turns": 1, "usage": {...}}
```

実機で確認した重要な挙動:

- Web検索・Web取得は**権限フラグ無しで動作**します。
- 調査中に1件でもツール呼び出しが失敗すると、最終回答が完全に得られて
  いても `status` が `ERROR` になり、失敗内容が `error` に入ります。
  本実装は本文が取り出せる限り回答を捨てず、`error` の内容を**警告として
  回答に添えて**人間に見せます。
- `read_file` などのツールは、ヘッドレス実行では自動拒否されます
  (`a tool required the "read_file" permission that headless mode cannot
  prompt for`)。このため参照渡し(後述の `transport=file`)には
  agy 側の `permissions.allow` 設定が別途必要です。

**重要**: Antigravity CLI 自体は内部でクラウドサービスと通信します。
組織の情報管理規則・利用規約を確認したうえで利用してください。
このアプリは社内固有情報・機密情報・個人情報を外部へ送信しないよう
設計していますが、最終的な利用可否は組織のポリシーに従ってください。

`aifaq doctor` は、Antigravity CLI の存在とバージョンを確認しますが、
実際にAIへ問い合わせることはありません。

## 設定(環境変数)

特定製品名に依存しない `AIFAQ_RESEARCH_*` に統一しています。

| 環境変数 | 既定値 | 説明 |
|---|---|---|
| `AIFAQ_RESEARCH_PROVIDER` | `antigravity` | `antigravity` または `fake` |
| `AIFAQ_RESEARCH_BIN` | `agy` | 実行ファイル名 |
| `AIFAQ_RESEARCH_TRANSPORT` | `arg` | `arg`(引数渡し) / `file`(参照渡し) |
| `AIFAQ_RESEARCH_TIMEOUT` | `180` | 秒 |
| `AIFAQ_RESEARCH_WORKDIR` | (OS既定) | 参照渡し時の一時ディレクトリの親 |
| `AIFAQ_RESEARCH_MODEL` | (未指定) | `--model` へ渡す値 |

旧 `AIFAQ_GEMINI_BIN` も当面読みますが、使用すると非推奨警告を出します。
`AIFAQ_RESEARCH_BIN` へ移行してください。

2ターミナル運用まわりの設定:

| 環境変数 | 既定値 | 説明 |
|---|---|---|
| `AIFAQ_DB_PATH` | `data/aifaq.db` | 共有するSQLite DB。試験用DBの指定にも使う |
| `AIFAQ_BUSY_TIMEOUT_MS` | `5000` | ロック競合時にSQLite側で待つ時間(ミリ秒) |
| `AIFAQ_WATCH_INTERVAL` | `2.0` | `aifaq watch` の既定ポーリング間隔(秒) |
| `AIFAQ_MAX_ANSWER_CHARS` | `20000` | 人間回答本文の最大文字数 |

実機試験の回答を本番の知識へ残したくない場合は、専用DBを指定します。

```bash
AIFAQ_DB_PATH=data/test.db aifaq ask "テスト質問" --thread-id t-001
```

### 調査指示の渡し方(`AIFAQ_RESEARCH_TRANSPORT`)

- **`arg`(既定・実機確認済み)**: 調査指示をコマンドライン引数として渡します。
  過去に稼働実績のある `werewolf-game` / `OracleCouncil` と同じ方式です。
  agy は stdin や `--prompt-file` を持たないため、プロンプトは必ず argv に
  載ります。Windows の `CreateProcess` は約32,767 UTF-16コード単位の
  上限があるため、超過する場合はプロセス起動前に検出してエラーにします。
- **`file`(未検証)**: 公開判定済みの調査指示だけを一時ファイルへ書き、
  短い参照指示だけを argv で渡します。リポジトリ本体から分離した一時
  ディレクトリを使い、成否にかかわらず削除します。
  **ただし、agy のヘッドレス実行は `read_file` を自動拒否する**ため、
  agy 側の `settings.json` に `permissions.allow` を設定しない限り動作しません。
  本作業環境では設定変更を行わなかったため**実機未検証**です。

### 実AI CLIを使わずFakeProviderで動かす

```bash
aifaq --fake-provider ask "質問文"        # 単発
export AIFAQ_RESEARCH_PROVIDER=fake       # セッション全体
```

CI(GitHub Actions)は `AIFAQ_RESEARCH_PROVIDER=fake` を設定し、
`agy` が入っていないことを明示的に検証したうえでテストします。
実AI CLIをCIから呼ぶことはありません。

## CLI使用例

主要コマンド(`aifaq` は `python -m aifaq` と同じ。`pip install -e .` 後は
`aifaq` コマンドも使えます):

```bash
aifaq init                                  # DB初期化
aifaq doctor                                # 環境診断
aifaq ask "Windows 11でネットワークアダプターを再起動する方法は？"
aifaq ask "質問文" --thread-id THREAD --requester 山田
aifaq reply THREAD "回答文"                  # 確認質問に回答
aifaq pending list                          # 人間回答待ち一覧
aifaq pending answer ID --approved-by 名前 --category network \
    --tags wifi,password --variants "別の聞き方" < answer.txt
aifaq knowledge scan                        # 対応ファイルの一覧(取り込みはしない)
aifaq knowledge import                      # 差分取り込み(knowledge/全体)
aifaq knowledge import knowledge/public/x.md  # 1ファイルだけ取り込み
aifaq knowledge search "検索語"
aifaq knowledge list
aifaq research list                         # AI調査結果の一覧(既定は全件)
aifaq research list --status PENDING        # 未レビューのみ
aifaq research show ID                      # 元のAI回答・出典・警告を確認
aifaq research approve ID --approved-by 名前 # 承認済み知識へ昇格
aifaq research approve ID --approved-by 名前 --answer-file corrected.md  # 修正して承認
aifaq research reject ID --approved-by 名前 --reason "情報が古い"
aifaq research reject ID --approved-by 名前 --expired                    # 期限切れ
aifaq status THREAD                         # 状態と回答を一度だけ確認
aifaq status --pending-id 12                # 受付番号で確認
aifaq watch THREAD                          # 回答が入るまで待つ
aifaq watch THREAD --interval 1 --timeout 120
aifaq chat --requester 山田                  # 対話モード
aifaq memory validate                       # MEMORY.mdの形式検証
aifaq memory show                           # MEMORY.mdの内容表示
aifaq memory sync                           # Markdown → DBへ同期
aifaq history THREAD
```

`--json` はサブコマンドより前に付けてください(例:
`aifaq --json knowledge list`)。`--fake-provider` はテスト・デモ用で、
実AI CLI(Antigravity)の代わりにFakeProviderを使います。

### コマンド名の対応関係(instruction 001/002/003 → 004)

指示書間でコマンド名に差異があったため、004(最新)を基準に統一し、
一部は互換エイリアスとして両方受け付けます。

| 001/002での表記 | 実装したコマンド |
|---|---|
| `init-db` | `init`(`init-db` もエイリアスとして使用可) |
| `pending answer <id> --by <name>` | `pending answer <id> --approved-by <name>`(`--by` もエイリアスとして使用可) |
| `reply --thread-id THREAD_ID "回答"` | `reply THREAD "回答"`(位置引数。004に合わせた) |

## 最大3回の確認質問

回答に必要な情報が不足している場合、AI-FAQは1ラウンド1問・最大3回まで
確認質問をします。設定 `AIFAQ_MAX_CLARIFICATION_ROUNDS` で減らすことは
できますが、3を超える値は設定できません(コード側で3にクランプします)。

- 選択肢を提示できる場合は2〜4個の具体的な選択肢を示します。
- パスワード・秘密鍵・認証コード・個人情報そのものを尋ねることはありません。
  そのような情報が必要に見える質問は、確認質問をせず直接IT管理者へ
  引き継ぎます。
- 「わからない」「不明」等の回答が2回連続した場合、3回を待たずに
  IT管理者へ引き継ぎます。
- 3回で解決しない場合はIT管理者への引き継ぎ(`PENDING_HUMAN`)になります。
  4回目の確認質問は生成されません。
- `thread_id` により、CLIを終了した後でも `aifaq reply` で再開できます。

## 人間回答の承認方法

```bash
aifaq pending list
aifaq pending show 12
echo "情報システム部の内線1234へ連絡してください。" | \
    aifaq pending answer 12 --approved-by hantani --category network \
    --tags wifi --variants "Wi-Fiがつながらない,パスワードを忘れた" \
    --reason "初回対応として登録"
```

回答本文は `--answer-file FILE` でファイルから読み込むこともできます。
省略時は標準入力から読み込みます。承認された回答は
`source_type=HUMAN, status=APPROVED` の承認済み知識として保存され、
次回以降の類似質問(完全一致・別表現・全文検索)で再利用されます。

AI調査結果(`INTERNET_RESEARCH`)は自動ではナレッジ化されません。人間が
`aifaq research approve` で確認・承認したものだけが承認済み知識になります
(「CLI使用例」の `research` コマンド参照)。

## 2ターミナルでの使い方(質問者とIT管理者)

同じリポジトリフォルダを2つのターミナルで開き、同じ `data/aifaq.db` を
共有して運用します。UIはまだありません。

```text
ターミナルA: 質問者        ターミナルB: IT管理者
```

### ターミナルA(質問者)

```bash
aifaq ask "第2工場のPC交換申請先は？" --thread-id demo-001 --requester 山田
```

人間引き継ぎになると、受付番号と次の操作が表示されます。

```text
IT管理者へ引き継ぎました。
受付番号: 12
thread_id: demo-001
状態: 回答待ち

後から次のコマンドで確認できます:
  aifaq status demo-001

回答を待ち続ける場合:
  aifaq watch demo-001
```

### ターミナルB(IT管理者)

```bash
aifaq pending list
aifaq pending show 12
echo "PC交換申請は情報システム部へ提出してください。" | \
    aifaq pending answer 12 --approved-by hantani --category procedure \
    --tags pc,申請 --reason "初回回答として承認"
```

### ターミナルA(回答の受け取り)

```bash
aifaq status demo-001    # 今の状態を一度だけ確認する
aifaq watch demo-001     # 回答が入るまで待つ
```

### `status` と `watch` の違い

| | `status` | `watch` |
|---|---|---|
| 動作 | 現在の状態を1回表示して終了 | 回答が入るまでポーリングして待つ |
| 用途 | 後から確認する | その場で待ち続ける |
| 間隔 | — | `--interval`(既定2.0秒、最短0.5秒) |
| 上限 | — | `--timeout SECONDS`(省略時は無制限) |

`watch` の終了コード:

| 終了コード | 意味 |
|---|---|
| `0` | 回答済み・取り下げ・対応済みのいずれかを検知して終了 |
| `1` | 対象のthread_id / 受付番号が見つからない |
| `2` | 引数が不正(`--interval` が下限未満など) |
| `3` | `--timeout` に達した(状態は保持される) |
| `130` | Ctrl+Cで待機を中止(回答はDBに残る) |

`watch` の進捗表示は標準エラー出力へ出すため、`aifaq --json watch ...` の
標準出力はJSONとしてそのまま解析できます。

### 人間回答が質問者へ返る仕組み

`aifaq pending answer` は1つのトランザクションで次を行います。

1. 承認済み知識(`knowledge_articles`)として保存
2. **元の `pending_questions` 行へ回答本文・回答者・回答日時を保存**
3. pendingを `ANSWERED` にする
4. 元の `thread_id` へ紐付けて `query_history` へ記録

質問者の `status` / `watch` は、この pending 行を `thread_id`(または
受付番号)で引いて回答を返します。したがって質問者は、同じ質問を送り直す
ことなく、元のスレッドのまま回答を受け取れます。

#### LangGraphのチェックポイントを進めない理由

`request_human` は LangGraph の `interrupt()` でスレッドを中断します。
IT管理者が回答したとき、このチェックポイントを「人間回答を受け取った完了
状態」へ再開させる設計も考えられますが、**採用していません**。理由は次の
とおりです。

- 再開するには IT管理者側のプロセスが、質問者のグラフを
  `Command(resume=...)` で進めることになります。回答を書き込んだ人が、
  他人のワークフローを進めることになり、責務が逆転します。
- グラフの再開は業務状態の更新(pendingとknowledgeの保存)と同じ
  トランザクションに入れられません。片方だけ成功する状態が生まれます。
- 質問者が `status` を実行するのは、回答から数時間後かもしれません。
  そのときにグラフを再開しても、得られるのはSQLiteに既にある回答です。

そのため**業務状態はSQLite側を正**とし、`status` / `watch` は
`pending_questions` から回答を返します。チェックポイントは中断したまま
残りますが、`thread_id` は再利用されず、次の質問は新しいスレッドになるため
実害はありません。

回答の**保存**と、質問者が**読んだこと**は分離しています。`status` /
`watch` で受け取ると `delivered_at` が記録されますが、状態は `ANSWERED` の
ままで、回答は消えません。何度でも取得できます。

### 人間回答は次回以降の承認済み知識になる

同じ回答が `source_type=HUMAN` の承認済み知識として保存されるため、次回の
同じ質問では人間を待たずに即座に回答されます。これがこのプロジェクトで
いう「学習」です。

**言い換えた質問にも効かせるには `--variants` を登録してください。**

```bash
aifaq pending answer 12 --approved-by hantani --category procedure \
    --variants "PC交換の申請先はどこですか,パソコン交換の申請先,PC交換申請の提出先"
```

再利用が効くのは次の場合です。

| 質問 | 再利用 |
|---|---|
| 登録時と同じ文言 | ○(完全一致) |
| `--variants` に登録した別表現 | ○(完全一致) |
| 語がそのまま含まれる質問(部分一致) | ○(全文検索) |
| 未登録の言い換え | △ 確認質問へ進む場合があります |

未登録の言い換えは、語のOR検索では一致しても意図的にスコアを閾値未満に
抑えています。曖昧な一致だけで確認質問や人間引き継ぎを飛ばさないための
安全側の設計です。運用では、よくある言い換えを `--variants` へ足していく
ことで精度が上がります。

### 対話モード(`aifaq chat`)

確認質問を繰り返す対話モードもあります。

```bash
aifaq chat --requester 山田
```

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

人間引き継ぎになった場合は、そのまま待つか終了するかを選べます。
FAQロジックは `ask` / `reply` と同じものを再利用しています。

### SQLiteを共有するときの注意

2つのターミナルが同じ `data/aifaq.db` へ別プロセスからアクセスします。
そのため接続ごとに次を設定しています。

- `PRAGMA journal_mode=WAL`: 質問者の `watch`(読み)が、IT管理者の
  `pending answer`(書き)をブロックしません。
- `PRAGMA busy_timeout`(既定5000ms、`AIFAQ_BUSY_TIMEOUT_MS` で変更可):
  ロック競合時にSQLite側で待ちます。
- `watch` はポーリングのたびに接続を開いて短い読み取りで閉じるため、
  DBを長時間ロックしません。

それでもロックが解消しない場合は、無限に再試行せず、対処方法を含む
エラーメッセージを表示して終了します。

同じpendingへ2人が同時に回答した場合は、**先に書き込んだ側だけが成功**し、
後から来た側は明確なエラー(終了コード2)になります。既存の回答が上書き
されることはありません。

## Excel・テキスト知識の置き方

`knowledge/` 以下に資料を置きます(詳細は `knowledge/README.md`)。

- 対応形式: `.xlsx`, `.xlsm`(セル値のみ・マクロ不実行), `.csv`, `.tsv`,
  `.md`, `.txt`
- Excelは1行目をヘッダーとして扱い、各行を「ヘッダー: 値」形式で
  チャンク化します。非表示シートは既定で取り込みません。
- CSV/TSV/テキストはUTF-8を優先し、失敗時のみCP932へフォールバックします
  (取り込み結果に警告として記録されます)。
- ファイルのSHA-256を保存し、内容が変わったファイルだけ再取り込みします。
  削除されたファイルは `MISSING` として履歴に残ります(即削除しません)。

```bash
aifaq knowledge import          # knowledge/ 全体を差分取り込み
aifaq knowledge search "Wi-Fi"  # 承認済み知識 + 取り込み資料を横断検索
```

## MEMORY.mdの書式と例

`MEMORY.md` は次の6章を持つ索引です(詳細は `memory/README.md`)。

```markdown
# AI-FAQ Memory Index

## Source Map
## Terminology
## Routing Rules
## Important Decisions
## Known Gaps
## Retired or Forbidden Sources
```

`## Source Map` の各エントリは `### SRC-xxx` 見出し + 箇条書きです。

```markdown
### SRC-001
- path: knowledge/procedures/account-request.xlsx
- contains: アカウント申請、権限変更、退職者処理の手順
- sheets: 申請先, 権限一覧
- scope: INTERNAL
- category: account
- owner: IT管理部
- priority: high
- status: active
- last_reviewed: 2026-08-01
- valid_until:
- notes: 申請先は「申請先」シートを正とする
```

`aifaq memory validate` は、必須章の存在・重複ID・相対パスの安全性
(`..`/絶対パス/UNCパスの拒否)・参照ファイルの存在・レビュー期限切れなどを
検査します。`aifaq memory sync` はMarkdownを正としてDBへ一方向反映します
(DBからMarkdownを書き換えることはありません)。

## 内部資料をGitへ入れない運用

- 実データを置いてよいのは `knowledge/public/` だけです。
- `knowledge/internal/`, `knowledge/policies/`, `knowledge/procedures/`,
  `knowledge/troubleshooting/`, `knowledge/inventory/`, `knowledge/inbox/`
  はREADME以外 `.gitignore` で除外されています。
- `data/`(SQLite DB)も `.gitignore` 対象です。
- `aifaq doctor` は、内部資料がGitで追跡対象になっていないか
  (`git ls-files knowledge/internal knowledge/inbox`)を警告します。

## セキュリティ上の制限

- 「セキュリティ境界」章の情報は外部AIへ送信しません。
- Antigravity CLIは `shell=False` の引数配列で呼び出し、
  `--dangerously-skip-permissions` は使いません。ヘッドレス実行では
  ファイル書換・シェル実行などのツールがagy側で自動拒否されます。
- 取り込み済み資料の一致より先に、機密性・人間対応希望・行き詰まりの
  判定を行います(資料の偶然の一致で人間引き継ぎを握りつぶさないため)。
- `knowledge/**/README.md` はフォルダ説明用の付属文書なので取り込みません。
- 危険な操作(アカウント削除・権限変更・ネットワーク遮断等)を自動実行する
  機能はありません。FAQとして手順を提示するだけです。
- 人間回答は外部AIへ送信しません。IT管理者が書いた回答本文はSQLiteに
  保存され、Antigravityへ渡ることはありません。
- 回答本文は保存前に検証します。空文字、上限(既定20,000文字、
  `AIFAQ_MAX_ANSWER_CHARS`)超過、改行・タブ以外の制御文字は拒否します。
- `status` / `watch` は指定された `thread_id`(または受付番号)の情報だけを
  返します。thread_id・受付番号はSQLのプレースホルダーで渡し、SQL文字列へ
  連結しません。

## 認証なしCLI版の利用範囲

**このCLI版には認証・アクセス制御がありません。** 次を前提としています。

- 同じPC・同じOSユーザー、または信頼された管理環境での利用
- `data/aifaq.db` を読める利用者は、**すべての質問・回答履歴を参照できます**
  (`aifaq status` は任意の `thread_id` / 受付番号を指定できます)
- 誰が質問者で誰がIT管理者かを、システムは検証しません。
  `--approved-by` は自己申告の記録項目です

複数の社員へ配布して本番運用する前には、認証付きWeb UI、またはOSの
ファイル権限やネットワーク分離といった別のアクセス制御が必要です。

なお、`ask` / `reply` / `pending answer` / `status` の業務ロジックは
サービス層(`graph.run_ask` / `run_reply`、`repositories`)に置いてあり、
CLIはその薄い呼び出しです。将来Web UIへ移行する際も、このサービス層を
そのまま再利用できます。

## テスト方法

```bash
python -m pytest -q
python -m pytest -q --cov=aifaq --cov-report=term-missing
```

外部ネットワークと実AI CLI(Antigravity)は一切呼び出さず、
`FakeResearchProvider` または `unittest.mock` によるモックのみで完結します。

2ターミナル運用は `tests/test_two_terminal_integration.py` で、実際に
`subprocess` でCLIを別プロセス起動して検証しています(`watch` 起動中に
別プロセスから回答し、`watch` が検知して終了することを確認)。
別プロセス起動を伴うため、このファイルだけは実行に1分程度かかります。

CI(GitHub Actions)は Python 3.12 / 3.13 の両方で実行し、
`AIFAQ_RESEARCH_PROVIDER=fake` を設定したうえで `agy` が
インストールされていないことを検証します。

## 既知の制約

- **参照渡し(`AIFAQ_RESEARCH_TRANSPORT=file`)は実機未検証**: agyの
  ヘッドレス実行が `read_file` を自動拒否するため、agy側の
  `permissions.allow` を設定しない限り動作しません。本作業では利用者の
  agy全体設定を変更しない判断をしたため、この経路は実機で確認して
  いません。既定の `arg` は実機確認済みです。
- **日本語の検索語抽出は簡易実装**: 形態素解析器を使わず、漢字・
  カタカナ・英数字の連なりを内容語として取り出し、ひらがなを捨てる
  ヒューリスティックです(`util.extract_search_terms`)。ひらがなだけの
  語(「ぱそこん」等)は検索語になりません。
- **`check_source_conflicts` は簡易実装**: 複数の `authoritative` 資料が
  同一検索結果に含まれ、内容の類似度が低い場合にのみ「矛盾」と判定する
  簡易ヒューリスティックです。意味的な矛盾検出ではありません。
- **`load_memory_context` は軽量実装**: 用語・ルーティング規則をLLMへの
  文脈注入として使う高度な処理はせず、`memory show`/`memory sync` で
  人間・DBが参照できる形にとどめています。
- **FTS5には trigram トークナイザーを使用**: 既定の `unicode61`
  トークナイザーは日本語(CJK)を単語分割できないため、部分一致検索に
  `tokenize='trigram'` を採用しました。3文字未満の検索語はLIKE検索へ
  フォールバックします。
- **CLIの実行ディレクトリ**: `knowledge/`, `memory/`, `MEMORY.md` は
  リポジトリ直下からの相対パスとして扱うため、`aifaq` はリポジトリ
  ルートで実行してください。

## 将来拡張の候補

- Claude Code CLI / Codex CLI / ローカルLLM 用の `ResearchProvider` 実装追加
- AI調査結果の人間承認によるナレッジ昇格コマンド
- `check_source_conflicts` の高度化(意味的な矛盾検出)
- PDF/Word/PowerPoint取り込み対応
- Web UI化
