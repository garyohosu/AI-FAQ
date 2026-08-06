# Terminology (社内用語)

見出し(`##`)単位で1用語を表します。`aifaq memory sync` はこの単位で
`memory_entries` (memory_type=TERMINOLOGY) へ取り込みます。

## AI-FAQ

このリポジトリが実装するCLIアプリケーションの名称。社内IT管理部門向けの
学習型FAQで、Web UIやチャットボットではない。

## 学習

このプロジェクトでの「学習」は、LLMの再訓練やファインチューニングでは
なく、人間(IT管理者)が回答・承認した内容を版管理された知識として
SQLiteへ蓄積し、次回以降の類似質問に再利用する仕組みを指す。

## 承認済み知識 (knowledge_articles)

IT管理者が回答し `status=APPROVED` として保存されたFAQエントリ。回答の
最優先根拠として扱う。

## 人間回答待ち (PENDING_HUMAN)

社内固有情報・機密性・根拠不足などの理由でAIが自動回答せず、IT管理者の
回答を待っている状態。
