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
AI CLIプロバイダー層 (aifaq/providers/*) -- Gemini CLI / FakeProvider
```

`ResearchProvider` はProtocolで抽象化しており、`GeminiCLIProvider` を
将来 Claude Code CLI / Codex CLI / ローカルLLM 用のプロバイダーへ
差し替え可能です(`aifaq/providers/base.py`)。

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

以下は決定論的ルール(`aifaq/security.py`)で検出し、外部AI(Gemini CLI)
へは絶対に送信しません。判断に迷う場合も送信せず人間へ引き継ぎます。

- パスワード・秘密鍵・トークン・認証コードらしき語や値
- 個人情報(メールアドレス・電話番号・社員番号等)
- 社内IP・内部ホスト名・内部ドメイン
- 「社内」「弊社」「当社」「申請先」「担当者」「共有フォルダ」等の
  社内固有手続きを示す語、工場名・設備名・資産番号
- アカウント停止・権限変更・退職者処理などの重要操作

`knowledge/internal/` や `memory/` の内容がGemini CLIへ渡ることもありません
(Web調査プロンプトには質問文だけを渡します)。

Gemini CLIは `subprocess.run(shell=False)` で引数配列として呼び出し、
`--yolo` は使用しません。`.gemini/settings.json` でWeb検索・Web取得以外の
ツール(ファイル書換・シェル実行等)を無効化しています。

## セットアップ

```bash
# Python 3.12+ (開発・確認は Python 3.14.0 で実施。詳細は「既知の制約」参照)
python -m venv .venv
source .venv/Scripts/activate   # Windows PowerShellの場合は .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Gemini CLIの準備

このリポジトリは [Gemini CLI](https://github.com/google-gemini/gemini-cli)
をサブプロセスとして呼び出します。あらかじめ別途インストール・ログイン
してください。

```bash
npm install -g @google/gemini-cli
gemini --version
```

**重要**: Gemini CLI自体は内部でGoogleのクラウドサービスと通信します。
「手元のChatGPT Plus等の契約がそのままAPI利用料になる」という意味では
ありません。組織の情報管理規則・利用規約を確認したうえで利用してください。
このアプリは社内固有情報・機密情報・個人情報をGemini CLIへ送信しない
よう設計していますが、最終的な利用可否は組織のポリシーに従ってください。

`aifaq doctor` は、Gemini CLIの存在・バージョン・危険なツールが無効化
されているかを確認しますが、実際にAIへ問い合わせることはありません。

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
aifaq memory validate                       # MEMORY.mdの形式検証
aifaq memory show                           # MEMORY.mdの内容表示
aifaq memory sync                           # Markdown → DBへ同期
aifaq history THREAD
```

`--json` はサブコマンドより前に付けてください(例:
`aifaq --json knowledge list`)。`--fake-provider` はテスト・デモ用で、
Gemini CLIの代わりにFakeProviderを使います。

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

**AI調査結果(`INTERNET_RESEARCH`)を承認済み知識へ昇格させるCLIコマンドは
本MVPには未実装です**(「既知の制約」参照)。AI調査結果はその場の暫定回答
としてのみ提示され、自動的にも手動的にも承認済み知識(`knowledge_articles`)
へは保存されません。

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
- Gemini CLIは `shell=False` の引数配列で呼び出し、`--yolo` は使いません。
- `.gemini/settings.json` でWeb検索・Web取得以外のツールを無効化しています。
  ただし本設定のキー名はGemini CLI 0.50.0時点の確認内容であり、導入環境の
  バージョンに応じて公式ドキュメントで再確認してください。
- 危険な操作(アカウント削除・権限変更・ネットワーク遮断等)を自動実行する
  機能はありません。FAQとして手順を提示するだけです。

## テスト方法

```bash
python -m pytest -q
python -m pytest -q --cov=aifaq --cov-report=term-missing
```

外部ネットワークと実Gemini CLIは一切呼び出さず、`FakeResearchProvider`
または `unittest.mock` によるモックのみで完結します。

## 既知の制約

- **開発・動作確認のPythonバージョン**: `pyproject.toml` は
  `requires-python = ">=3.12"` としていますが、本リポジトリの開発・
  テスト実行環境には Python 3.12 系が存在せず、実際には **Python 3.14.0**
  で動作確認しました。3.12/3.13での動作確認はできていません
  (言語機能面では3.12+互換のコードのみを使用しています)。
- **Gemini CLIの実呼び出し未確認**: 本環境のGemini CLIアカウントは
  `IneligibleTierError`(利用ティア制限)のため、実際のWeb調査APIを
  伴う成功パスを確認できませんでした。`gemini --version` の実行、
  `--help` によるコマンド仕様の確認、`--skip-trust` が必要な
  trusted-folders仕様の確認は実施済みです。`GeminiCLIProvider` の
  JSON抽出・エラー分類ロジックは `unittest.mock` で単体テスト済みですが、
  実際のCLI成功時の出力形式(JSON包みの正確なキー名)は導入環境で
  一度確認することを推奨します。
- **AI調査結果のナレッジ昇格コマンド未実装**: instruction-001は
  `research approve <id>` 相当のコマンドを想定していましたが、
  instruction-004のCLI一覧には含まれていなかったため、本MVPでは
  未実装です。AI調査結果は暫定回答としてのみ提示されます。
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
