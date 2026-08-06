from aifaq import memory


VALID_MEMORY_MD = """# AI-FAQ Memory Index

## Source Map

### SRC-001
- path: knowledge/public/sample.csv
- contains: サンプル
- sheets:
- scope: PUBLIC
- category: network
- owner: IT管理部
- priority: high
- status: active
- last_reviewed: 2026-08-01
- valid_until:
- notes: テスト用

## Terminology
説明文

## Routing Rules
ルール本文

## Important Decisions
決定事項

## Known Gaps
不足事項

## Retired or Forbidden Sources
- なし
"""


def test_split_h2_sections_finds_all_required():
    sections = memory.split_h2_sections(VALID_MEMORY_MD)
    for name in memory.REQUIRED_SECTIONS:
        assert name in sections


def test_parse_source_map_valid_entry():
    sections = memory.split_h2_sections(VALID_MEMORY_MD)
    entries, errors = memory.parse_source_map(sections["Source Map"])
    assert errors == []
    assert len(entries) == 1
    assert entries[0].entry_id == "SRC-001"
    assert entries[0].scope.value == "PUBLIC"


def test_parse_source_map_rejects_duplicate_id():
    text = """### SRC-001
- path: knowledge/public/a.csv
- contains: x
- scope: PUBLIC
- status: active

### SRC-001
- path: knowledge/public/b.csv
- contains: y
- scope: PUBLIC
- status: active
"""
    entries, errors = memory.parse_source_map(text)
    assert len(entries) == 1
    assert any("重複" in e for e in errors)


def test_parse_source_map_rejects_traversal_path():
    text = """### SRC-001
- path: ../outside.csv
- contains: x
- scope: PUBLIC
- status: active
"""
    entries, errors = memory.parse_source_map(text)
    assert entries == []
    assert errors


def test_validate_memory_index_missing_file(tmp_path, settings):
    result = memory.validate_memory_index(tmp_path, settings)
    assert not result.ok
    assert any("見つかりません" in e for e in result.errors)


def test_validate_memory_index_missing_required_section(tmp_path, settings):
    (tmp_path / settings.memory_index_path).write_text(
        "# AI-FAQ Memory Index\n\n## Source Map\n", encoding="utf-8"
    )
    result = memory.validate_memory_index(tmp_path, settings)
    assert not result.ok
    assert any("Terminology" in e for e in result.errors)


def test_validate_memory_index_warns_on_missing_referenced_file(tmp_path, settings):
    (tmp_path / settings.memory_index_path).write_text(VALID_MEMORY_MD, encoding="utf-8")
    result = memory.validate_memory_index(tmp_path, settings)
    assert result.ok
    assert any("存在しません" in w for w in result.warnings)


def test_validate_memory_index_ok_when_file_exists(tmp_path, settings):
    (tmp_path / settings.memory_index_path).write_text(VALID_MEMORY_MD, encoding="utf-8")
    (tmp_path / "knowledge" / "public").mkdir(parents=True)
    (tmp_path / "knowledge" / "public" / "sample.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    result = memory.validate_memory_index(tmp_path, settings)
    assert result.ok
    assert not any("存在しません" in w for w in result.warnings)


def test_load_all_memory_entries_reads_markdown_files(tmp_path, settings):
    (tmp_path / settings.memory_index_path).write_text(VALID_MEMORY_MD, encoding="utf-8")
    mem_dir = tmp_path / settings.memory_dir
    mem_dir.mkdir(parents=True)
    (mem_dir / "terminology.md").write_text("## 用語A\n説明\n", encoding="utf-8")
    (mem_dir / "decisions.md").write_text("## 決定A\n理由\n", encoding="utf-8")

    entries = memory.load_all_memory_entries(tmp_path, settings)
    types = {e.memory_type.value for e in entries}
    assert "SOURCE_MAP" in types
    assert "TERMINOLOGY" in types
    assert "DECISION" in types
