"""`MEMORY.md` と `memory/*.md` の読み込み・検証・DB同期。

ここでの「メモリー」はLLMの再学習ではなく、人間が管理するMarkdownを正とする
索引・注記であり、SQLiteの `memory_entries` はその構造化コピーに過ぎない。
AIはメモリーの変更案を提案できるが、Markdownを直接書き換えて確定させることは
しない (`memory sync` はMarkdown → DBの一方向のみ)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from aifaq.models import MemoryEntry, MemoryType, SourceIndexEntry, SourcePriority, SourceScope, SourceStatus
from aifaq.util import now_iso

REQUIRED_SECTIONS = [
    "Source Map",
    "Terminology",
    "Routing Rules",
    "Important Decisions",
    "Known Gaps",
    "Retired or Forbidden Sources",
]

_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^-\s*([A-Za-z0-9_]+)\s*:\s*(.*)$")


@dataclass
class MemoryValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entries: list[SourceIndexEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def split_h2_sections(markdown_text: str) -> dict[str, str]:
    """`## 見出し` 単位でMarkdownを分割する。"""
    matches = list(_H2_RE.finditer(markdown_text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        sections[m.group(1).strip()] = markdown_text[start:end].strip()
    return sections


def split_h3_blocks(section_text: str) -> list[tuple[str, str]]:
    """`### 見出し` 単位でセクションをさらに分割する。"""
    lines = section_text.splitlines()
    blocks: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    for line in lines:
        m = _H3_RE.match(line)
        if m:
            if current_heading is not None:
                blocks.append((current_heading, current_lines))
            current_heading = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_heading is not None:
        blocks.append((current_heading, current_lines))
    return [(h, "\n".join(lines_).strip()) for h, lines_ in blocks]


def _parse_bullets(block_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    notes_lines: list[str] = []
    in_notes = False
    for line in block_text.splitlines():
        m = _BULLET_RE.match(line.strip())
        if m:
            key, value = m.group(1), m.group(2).strip()
            if key == "notes":
                in_notes = True
                notes_lines = [value] if value else []
            else:
                in_notes = False
                fields[key] = value
        elif in_notes:
            notes_lines.append(line.strip())
    if notes_lines:
        fields["notes"] = "\n".join(n for n in notes_lines if n)
    return fields


def parse_source_map(section_text: str) -> tuple[list[SourceIndexEntry], list[str]]:
    entries: list[SourceIndexEntry] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for entry_id, block in split_h3_blocks(section_text):
        if entry_id in seen_ids:
            errors.append(f"Source Mapに重複ID: {entry_id}")
            continue
        seen_ids.add(entry_id)
        fields = _parse_bullets(block)
        try:
            entries.append(
                SourceIndexEntry(
                    entry_id=entry_id,
                    path=fields.get("path", ""),
                    contains=fields.get("contains", ""),
                    sheets=[s.strip() for s in fields.get("sheets", "").split(",") if s.strip()],
                    scope=SourceScope(fields.get("scope", "INTERNAL")),
                    category=fields.get("category", "other"),
                    owner=fields.get("owner") or None,
                    priority=SourcePriority(fields.get("priority", "normal")),
                    status=SourceStatus(fields.get("status", "active")),
                    last_reviewed=fields.get("last_reviewed") or None,
                    valid_until=fields.get("valid_until") or None,
                    notes=fields.get("notes", ""),
                )
            )
        except ValueError as exc:
            errors.append(f"Source Map エントリ {entry_id} の検証に失敗: {exc}")
    return entries, errors


def validate_memory_index(repo_root: Path, settings) -> MemoryValidationResult:
    result = MemoryValidationResult()
    index_path = repo_root / settings.memory_index_path
    if not index_path.exists():
        result.errors.append(f"{settings.memory_index_path} が見つかりません")
        return result

    text = index_path.read_text(encoding="utf-8")
    sections = split_h2_sections(text)

    for required in REQUIRED_SECTIONS:
        if required not in sections:
            result.errors.append(f"必須章が見つかりません: ## {required}")

    if "Source Map" not in sections:
        return result

    entries, parse_errors = parse_source_map(sections["Source Map"])
    result.errors.extend(parse_errors)
    result.entries = entries

    now = datetime.now(UTC)
    for entry in entries:
        full_path = repo_root / entry.path
        if not full_path.exists() and entry.status not in (
            SourceStatus.MISSING,
            SourceStatus.RETIRED,
            SourceStatus.FORBIDDEN,
        ):
            result.warnings.append(
                f"{entry.entry_id}: 参照ファイルが存在しません ({entry.path})"
            )
        if not entry.last_reviewed:
            result.warnings.append(f"{entry.entry_id}: last_reviewed が未記入です")
        else:
            try:
                reviewed = datetime.fromisoformat(entry.last_reviewed).replace(tzinfo=UTC)
                if (now - reviewed).days > 365:
                    result.warnings.append(
                        f"{entry.entry_id}: 最終レビューから1年以上経過しています"
                    )
            except ValueError:
                result.warnings.append(
                    f"{entry.entry_id}: last_reviewed の日付形式が不正です"
                )
        if entry.valid_until:
            try:
                valid_until = datetime.fromisoformat(entry.valid_until).replace(tzinfo=UTC)
                if valid_until < now:
                    result.warnings.append(f"{entry.entry_id}: 有効期限が切れています")
            except ValueError:
                result.warnings.append(
                    f"{entry.entry_id}: valid_until の日付形式が不正です"
                )

    return result


def build_index_lookup(entries: list[SourceIndexEntry]) -> dict[str, SourceIndexEntry]:
    return {entry.path: entry for entry in entries}


def _parse_flat_markdown(
    text: str, memory_type: MemoryType, source_path: str
) -> list[MemoryEntry]:
    """`## 見出し` 単位を1エントリとして memory/*.md を解釈する。"""
    entries: list[MemoryEntry] = []
    sections = split_h2_sections(text) or {"(root)": text}
    ts = datetime.now(UTC)
    for heading, body in sections.items():
        body = body.strip()
        if not body:
            continue
        entries.append(
            MemoryEntry(
                memory_type=memory_type,
                key=heading,
                value=body,
                source_markdown_path=source_path,
                heading=heading,
                status="active",
                approved_by=None,
                updated_at=ts,
            )
        )
    return entries


def load_all_memory_entries(repo_root: Path, settings) -> list[MemoryEntry]:
    """`MEMORY.md` と `memory/*.md` からDB同期用のエントリ一覧を作る。

    Markdownが常に正であり、この関数はMarkdown→構造化データの一方向変換のみ
    を行う。DBの内容がMarkdownへ書き戻されることはない。
    """
    ts = datetime.now(UTC)
    entries: list[MemoryEntry] = []

    index_path = repo_root / settings.memory_index_path
    if index_path.exists():
        text = index_path.read_text(encoding="utf-8")
        sections = split_h2_sections(text)

        if "Source Map" in sections:
            src_entries, _ = parse_source_map(sections["Source Map"])
            for e in src_entries:
                entries.append(
                    MemoryEntry(
                        memory_type=MemoryType.SOURCE_MAP,
                        key=e.entry_id,
                        value=e.model_dump_json(),
                        source_markdown_path=str(settings.memory_index_path),
                        heading=e.entry_id,
                        status=e.status.value,
                        approved_by=None,
                        updated_at=ts,
                    )
                )

        if "Routing Rules" in sections and sections["Routing Rules"]:
            entries.append(
                MemoryEntry(
                    memory_type=MemoryType.ROUTING_RULE,
                    key="Routing Rules",
                    value=sections["Routing Rules"],
                    source_markdown_path=str(settings.memory_index_path),
                    heading="Routing Rules",
                    updated_at=ts,
                )
            )

        if "Important Decisions" in sections and sections["Important Decisions"]:
            entries.append(
                MemoryEntry(
                    memory_type=MemoryType.DECISION,
                    key="Important Decisions (index)",
                    value=sections["Important Decisions"],
                    source_markdown_path=str(settings.memory_index_path),
                    heading="Important Decisions",
                    updated_at=ts,
                )
            )

        if "Known Gaps" in sections and sections["Known Gaps"]:
            entries.append(
                MemoryEntry(
                    memory_type=MemoryType.GAP,
                    key="Known Gaps (index)",
                    value=sections["Known Gaps"],
                    source_markdown_path=str(settings.memory_index_path),
                    heading="Known Gaps",
                    updated_at=ts,
                )
            )

        if "Retired or Forbidden Sources" in sections and sections["Retired or Forbidden Sources"]:
            for i, line in enumerate(
                l for l in sections["Retired or Forbidden Sources"].splitlines() if l.strip()
            ):
                entries.append(
                    MemoryEntry(
                        memory_type=MemoryType.FORBIDDEN_SOURCE,
                        key=f"retired-forbidden-{i}",
                        value=line.strip(),
                        source_markdown_path=str(settings.memory_index_path),
                        heading="Retired or Forbidden Sources",
                        updated_at=ts,
                    )
                )

    terminology_path = repo_root / settings.memory_dir / "terminology.md"
    if terminology_path.exists():
        entries.extend(
            _parse_flat_markdown(
                terminology_path.read_text(encoding="utf-8"),
                MemoryType.TERMINOLOGY,
                str(Path(settings.memory_dir) / "terminology.md"),
            )
        )

    # source-notes.md: 資料の優先順位・重複・注意点は、複数資料が競合した際の
    # 判断材料として使うためROUTING_RULEとして扱う。
    source_notes_path = repo_root / settings.memory_dir / "source-notes.md"
    if source_notes_path.exists():
        entries.extend(
            _parse_flat_markdown(
                source_notes_path.read_text(encoding="utf-8"),
                MemoryType.ROUTING_RULE,
                str(Path(settings.memory_dir) / "source-notes.md"),
            )
        )

    decisions_path = repo_root / settings.memory_dir / "decisions.md"
    if decisions_path.exists():
        entries.extend(
            _parse_flat_markdown(
                decisions_path.read_text(encoding="utf-8"),
                MemoryType.DECISION,
                str(Path(settings.memory_dir) / "decisions.md"),
            )
        )

    gaps_path = repo_root / settings.memory_dir / "gaps.md"
    if gaps_path.exists():
        entries.extend(
            _parse_flat_markdown(
                gaps_path.read_text(encoding="utf-8"),
                MemoryType.GAP,
                str(Path(settings.memory_dir) / "gaps.md"),
            )
        )

    return entries
