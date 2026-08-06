"""日本語の自然文からの検索語抽出と、段階的フォールバック検索のテスト。

背景: 質問文全体をFTS5の1フレーズとして検索していたため、
「スプーラーの状態を確認する方法」のような完全文では、資料に文がそのまま
現れない限り必ず0件になり、取り込み済み資料へ到達できなかった。
"""

import hashlib

import pytest

from aifaq import db as db_module
from aifaq.config import Settings
from aifaq.models import LocationType, SourceChunk, SourceFileRecord, SourceScope
from aifaq.repositories import Repositories
from aifaq.util import (
    extract_search_terms,
    fts_terms,
    is_relevant_match,
    matched_terms,
)
from datetime import UTC, datetime


# ---------------------------------------------------------------------------
# 検索語の抽出
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("スプーラーの状態を確認する方法", ["スプーラー", "状態", "確認"]),
        ("印刷が遅いときの対処を教えてください", ["印刷", "対処"]),
        ("ネットワークアダプターを再起動する手順は？", ["ネットワークアダプター", "再起動"]),
        ("VPNのパスワードを忘れた", ["VPN", "パスワード"]),
    ],
)
def test_extracts_content_words_from_natural_sentences(query, expected):
    assert extract_search_terms(query) == expected


def test_particles_and_polite_endings_are_dropped():
    """助詞・丁寧表現・疑問表現が混ざっていても内容語だけを取り出す。"""
    terms = extract_search_terms("Wi-Fiが繋がらないのですが、どうすればいいですか？")
    assert "Wi-Fi" in terms
    # 助詞や丁寧表現がそのまま検索語になっていない
    assert not any(t in {"が", "ですが", "ですか", "どう", "いい"} for t in terms)


def test_hiragana_verbs_are_not_split_into_fragments():
    """「繋がらない」の「が」で語を割らない(助詞分割方式の失敗パターン)。"""
    assert "らない" not in extract_search_terms("Wi-Fiが繋がらない")


def test_katakana_long_vowel_is_kept_inside_terms():
    assert "スプーラー" in extract_search_terms("スプーラーを再起動")
    assert "ネットワークアダプター" in extract_search_terms("ネットワークアダプターの設定")


def test_empty_and_stopword_only_queries_yield_no_terms():
    assert extract_search_terms("") == []
    assert extract_search_terms("   ") == []
    # 「方法」だけならフレーズ検索へフォールバックさせる
    assert extract_search_terms("方法") == []


def test_fts_terms_requires_three_characters():
    """trigramトークナイザーは3文字未満を索引できない。"""
    assert fts_terms("印刷が遅い") == []          # 印刷(2) は対象外
    assert fts_terms("スプーラーの確認") == ["スプーラー"]


def test_matched_terms_is_case_and_width_insensitive():
    assert matched_terms("VPN接続の設定", ["vpn"]) == ["vpn"]
    assert matched_terms("ＶＰＮ接続", ["VPN"]) == ["VPN"]


# ---------------------------------------------------------------------------
# 過剰一致の抑制
# ---------------------------------------------------------------------------


def test_single_short_term_is_not_enough():
    """2文字語1つだけの一致では関連ありとしない(拾いすぎ防止)。"""
    assert is_relevant_match("確認事項の一覧", ["確認", "スプーラー"]) is False


def test_two_short_terms_or_one_distinctive_term_is_enough():
    assert is_relevant_match("印刷の対処一覧", ["印刷", "対処"]) is True
    assert is_relevant_match("スプーラーの再起動", ["スプーラー"]) is True


def test_one_generic_term_in_a_long_question_is_not_enough():
    """「Windows」だけが当たった無関係な資料を回答に採用しない。"""
    terms = extract_search_terms("Windows 11のディスク空き容量を確認する公式手順は？")
    unrelated = "Windowsネットワークの一般的なトラブルシューティング(サンプル)"
    assert is_relevant_match(unrelated, terms) is False


def test_long_question_still_matches_a_genuinely_relevant_document():
    """一般語を含む長い質問でも、本題が一致していれば採用する。"""
    terms = extract_search_terms("Windows 11でネットワークアダプターを再起動する公式手順は？")
    relevant = "ネットワークアダプターを再起動する: 無効にしてから有効にします"
    assert is_relevant_match(relevant, terms) is True


# ---------------------------------------------------------------------------
# 取り込み資料の検索 (段階的フォールバック)
# ---------------------------------------------------------------------------


def _add_source(repos, relative_path, file_type, chunks):
    record = SourceFileRecord(
        relative_path=relative_path,
        file_type=file_type,
        scope=SourceScope.PUBLIC,
        category="troubleshooting",
        size_bytes=100,
        modified_at=datetime.now(UTC),
        sha256=hashlib.sha256(relative_path.encode()).hexdigest(),
    )
    file_id = repos.source_files.upsert(record)
    for chunk in chunks:
        chunk.source_file_id = file_id
    repos.source_chunks.replace_for_file(file_id, chunks)
    return file_id


@pytest.fixture
def populated(repos):
    _add_source(
        repos,
        "knowledge/public/troubleshooting.xlsx",
        "xlsx",
        [
            SourceChunk(
                source_file_id=0,
                location_type=LocationType.SHEET_ROWS,
                sheet_name="プリンター",
                row_start=2,
                row_end=2,
                content="症状: 印刷が遅い | 確認事項: スプーラーの状態 | 対処: 印刷スプーラーサービスを再起動する",
                content_hash="h1",
            ),
            SourceChunk(
                source_file_id=0,
                location_type=LocationType.SHEET_ROWS,
                sheet_name="ネットワーク",
                row_start=2,
                row_end=2,
                content="症状: Wi-Fiが繋がらない | 対処: ネットワークアダプターを無効化してから有効化する",
                content_hash="h2",
            ),
        ],
    )
    _add_source(
        repos,
        "knowledge/public/glossary.csv",
        "csv",
        [
            SourceChunk(
                source_file_id=0,
                location_type=LocationType.CSV_ROWS,
                row_start=3,
                row_end=3,
                content="用語: VPN | 説明: 社外から社内ネットワークへ安全に接続する仕組み",
                content_hash="h3",
            )
        ],
    )
    _add_source(
        repos,
        "knowledge/public/guide.md",
        "md",
        [
            SourceChunk(
                source_file_id=0,
                location_type=LocationType.MARKDOWN_HEADING,
                heading="ネットワークアダプターの再起動",
                row_start=10,
                row_end=20,
                content="設定アプリからネットワークアダプターを無効化し、数秒後に有効化します。",
                content_hash="h4",
            )
        ],
    )
    _add_source(
        repos,
        "knowledge/public/notes.txt",
        "txt",
        [
            SourceChunk(
                source_file_id=0,
                location_type=LocationType.TEXT_BLOCK,
                row_start=1,
                row_end=5,
                content="モニターが映らない場合はケーブルの接続を確認してください。",
                content_hash="h5",
            )
        ],
    )
    return repos


@pytest.mark.parametrize(
    "question,expected_path",
    [
        ("スプーラーの状態を確認する方法", "knowledge/public/troubleshooting.xlsx"),
        ("印刷が遅いときの対処を教えてください", "knowledge/public/troubleshooting.xlsx"),
        ("ネットワークアダプターを再起動する手順を教えてください", "knowledge/public/guide.md"),
        ("VPNとは何ですか？", "knowledge/public/glossary.csv"),
        ("モニターが映らないのですがどうすればいいですか", "knowledge/public/notes.txt"),
    ],
)
def test_full_sentence_questions_find_the_right_file(populated, question, expected_path):
    """完全文の質問でもExcel/CSV/Markdown/TXTが見つかる。"""
    rows = populated.source_chunks.search(question, limit=5)
    assert rows, f"{question!r} で資料が見つかりませんでした"
    assert rows[0]["relative_path"] == expected_path


def test_citation_fields_are_available_for_excel(populated):
    """回答へファイル名・シート名・行範囲を出典として付けられる。"""
    rows = populated.source_chunks.search("スプーラーの状態を確認する方法", limit=5)
    row = rows[0]
    assert row["relative_path"] == "knowledge/public/troubleshooting.xlsx"
    assert row["sheet_name"] == "プリンター"
    assert row["row_start"] == 2
    assert row["row_end"] == 2


def test_citation_fields_are_available_for_markdown(populated):
    rows = populated.source_chunks.search(
        "ネットワークアダプターを再起動する手順を教えてください", limit=5
    )
    row = rows[0]
    assert row["heading"] == "ネットワークアダプターの再起動"
    assert row["row_start"] == 10


def test_unrelated_documents_are_not_returned(populated):
    """語を1つ含むだけの無関係な資料を拾わない。"""
    rows = populated.source_chunks.search("スプーラーの状態を確認する方法", limit=5)
    paths = {r["relative_path"] for r in rows}
    assert "knowledge/public/notes.txt" not in paths
    assert "knowledge/public/glossary.csv" not in paths


def test_security_sensitive_question_is_not_answered_from_imported_file(populated):
    """機密質問を、資料の偶然の一致で回答してはならない。

    検索精度を上げた結果、「管理者」「社内」のような一般語で運用文書が
    当たるようになったため、引き継ぎ判定を取り込み資料より先に行う。
    """
    from aifaq.routing import decide_route

    _add_source(
        populated,
        "knowledge/public/operations.md",
        "md",
        [
            SourceChunk(
                source_file_id=0,
                location_type=LocationType.MARKDOWN_HEADING,
                heading="運用ルール",
                row_start=1,
                row_end=5,
                content="社内の管理者はサーバーの運用手順に従ってください。",
                content_hash="h9",
            )
        ],
    )
    question = "社内サーバーの管理者パスワードを教えてください"
    chunks = [dict(r) for r in populated.source_chunks.search(question, limit=5)]
    assert chunks, "前提: この質問は資料に一致する(だからこそ順序が重要)"

    decision = decide_route(
        {
            "classification": {
                "scope": "SECURITY_SENSITIVE",
                "reason": "秘密情報らしき語を検出したため",
                "category": "other",
            },
            "knowledge_matches": [],
            "source_chunks": chunks,
        },
        Settings(),
    )
    assert decision.route == "human"


def test_approved_knowledge_still_answers_sensitive_questions(repos):
    """人間が承認した知識は機密質問にも回答してよい(学習ループの成果)。"""
    from aifaq.routing import decide_route

    decision = decide_route(
        {
            "classification": {"scope": "SECURITY_SENSITIVE", "category": "other"},
            "knowledge_matches": [
                {"knowledge_id": 1, "score": 1.0, "canonical_question": "q", "answer": "a"}
            ],
            "source_chunks": [],
        },
        Settings(),
    )
    assert decision.route == "knowledge"


def test_readme_files_are_not_ingested(tmp_path, settings):
    """フォルダ説明用のREADMEは知識として取り込まない。"""
    from aifaq.ingestion import scan_supported_files

    knowledge = tmp_path / "knowledge" / "public"
    knowledge.mkdir(parents=True)
    (tmp_path / "knowledge" / "README.md").write_text("フォルダの説明", encoding="utf-8")
    (knowledge / "README.md").write_text("フォルダの説明", encoding="utf-8")
    (knowledge / "faq.md").write_text("# 手順\n\n内容\n", encoding="utf-8")

    found = {p.name for p in scan_supported_files(settings, tmp_path)}
    assert found == {"faq.md"}


def test_completely_unrelated_question_returns_nothing(populated):
    assert populated.source_chunks.search("経費精算の締切はいつですか", limit=5) == []


def test_generic_term_in_heading_does_not_pull_unrelated_document(populated):
    """見出しの一般語1つ(「Windows」)だけで資料を回答に採用しない。

    関連度をFTS5へ渡せる3文字以上の語だけで測ると、短い語が分母から消えて
    過大評価され、この資料を拾ってしまう。全検索語で測ること。
    """
    _add_source(
        populated,
        "knowledge/public/windows-intro.md",
        "md",
        [
            SourceChunk(
                source_file_id=0,
                location_type=LocationType.MARKDOWN_HEADING,
                heading="Windowsネットワークの一般的なトラブルシューティング(サンプル)",
                row_start=1,
                row_end=5,
                content="これは取り込みのサンプルとして用意した資料です。",
                content_hash="h8",
            )
        ],
    )
    rows = populated.source_chunks.search(
        "Windows 11のディスク空き容量を確認する公式手順は？", limit=5
    )
    assert "knowledge/public/windows-intro.md" not in {r["relative_path"] for r in rows}


def test_generic_term_plus_real_topic_still_matches(populated):
    """一般語を含む長い質問でも、本題が一致する資料は拾う。"""
    rows = populated.source_chunks.search(
        "Windows 11でネットワークアダプターを再起動する公式手順は？", limit=5
    )
    assert rows
    assert rows[0]["relative_path"] == "knowledge/public/guide.md"


def test_exact_phrase_still_wins(populated):
    """フレーズがそのまま含まれる場合は従来どおり最優先で当たる。"""
    rows = populated.source_chunks.search("印刷スプーラーサービスを再起動する", limit=5)
    assert rows[0]["relative_path"] == "knowledge/public/troubleshooting.xlsx"


def test_retired_sources_stay_excluded(populated):
    populated.source_files.mark_missing("knowledge/public/troubleshooting.xlsx")
    rows = populated.source_chunks.search("スプーラーの状態を確認する方法", limit=5)
    assert all(
        r["relative_path"] != "knowledge/public/troubleshooting.xlsx" for r in rows
    )


# ---------------------------------------------------------------------------
# FTS5が使えない環境 (LIKEフォールバック)
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_no_fts(populated, monkeypatch):
    """FTS5非対応環境を模す。索引は作らずLIKE経路だけを使わせる。"""
    monkeypatch.setattr(populated.source_chunks, "_fts", False)
    monkeypatch.setattr(populated.knowledge, "_fts", False)
    return populated


@pytest.mark.parametrize(
    "question,expected_path",
    [
        ("スプーラーの状態を確認する方法", "knowledge/public/troubleshooting.xlsx"),
        ("印刷が遅いときの対処を教えてください", "knowledge/public/troubleshooting.xlsx"),
        ("ネットワークアダプターを再起動する手順を教えてください", "knowledge/public/guide.md"),
    ],
)
def test_like_fallback_finds_documents(populated_no_fts, question, expected_path):
    rows = populated_no_fts.source_chunks.search(question, limit=5)
    assert rows, f"{question!r} がLIKEフォールバックで見つかりませんでした"
    assert rows[0]["relative_path"] == expected_path


def test_like_fallback_also_excludes_unrelated(populated_no_fts):
    rows = populated_no_fts.source_chunks.search("スプーラーの状態を確認する方法", limit=5)
    assert "knowledge/public/notes.txt" not in {r["relative_path"] for r in rows}


# ---------------------------------------------------------------------------
# 承認済み知識の優先順位を壊さない
# ---------------------------------------------------------------------------


def _make_article(repos, question, answer, variants=None):
    from aifaq.models import KnowledgeSourceType

    return repos.knowledge.create(
        canonical_question=question,
        answer=answer,
        category="network",
        tags=[],
        source_type=KnowledgeSourceType.HUMAN,
        approved_by="tester",
        variants=variants or [],
    )


def test_exact_match_still_scores_highest(repos):
    kid = _make_article(repos, "VPNのパスワードを忘れた", "申請フォームから再発行してください")
    _make_article(repos, "VPNの接続方法", "クライアントを起動してください")

    matches = repos.knowledge.search("VPNのパスワードを忘れた", limit=5)
    assert matches[0].knowledge_id == kid
    assert matches[0].score == 1.0


def test_variant_exact_match_still_wins(repos):
    kid = _make_article(
        repos,
        "VPNのパスワードを忘れた",
        "申請フォームから再発行してください",
        variants=["VPNパスワード再発行"],
    )
    matches = repos.knowledge.search("VPNパスワード再発行", limit=5)
    assert matches[0].knowledge_id == kid
    assert matches[0].score == 1.0


def test_term_matches_do_not_outrank_exact(repos):
    exact_id = _make_article(repos, "プリンターの印刷が遅い", "スプーラーを再起動してください")
    _make_article(repos, "プリンターの用紙詰まり", "用紙を取り除いてください")

    matches = repos.knowledge.search("プリンターの印刷が遅い", limit=5)
    assert matches[0].knowledge_id == exact_id
    assert matches[0].score == 1.0


def test_term_match_score_stays_below_routing_threshold(repos):
    """語のOR一致だけで確認質問や調査経路を飛ばさない。"""
    _make_article(repos, "プリンターの用紙詰まりの直し方", "用紙を取り除いてください")

    settings = Settings()
    matches = repos.knowledge.search("スプーラーの状態を確認したいのですが", limit=5)
    for match in matches:
        assert match.score < settings.knowledge_match_threshold
