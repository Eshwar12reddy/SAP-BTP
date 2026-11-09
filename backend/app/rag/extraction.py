"""Bounded, local-only extraction for Phase 9 PDF, TXT and Markdown files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from app.models.knowledge import KnowledgeMediaType

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_PARAGRAPH = re.compile(
    r"\S(?:.*?\S)?(?=(?:\r?\n[ \t]*\r?\n|[ \t\r\n]*\Z))", re.DOTALL
)


@dataclass(frozen=True)
class DocumentSection:
    text: str
    heading: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    source_offset_start: int | None = None
    source_offset_end: int | None = None


class ExtractionError(ValueError):
    """Safe extraction failure code; never includes document contents."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def extract_document(
    path: Path,
    media_type: str,
    *,
    max_bytes: int,
    max_pages: int,
    max_characters: int = 5_000_000,
) -> list[DocumentSection]:
    """Extract sections while enforcing byte/page/character bounds."""
    if path.stat().st_size > max_bytes:
        raise ExtractionError("document_too_large")
    if media_type == KnowledgeMediaType.PDF.value:
        sections = _pdf_sections(path, max_pages)
    elif media_type in (KnowledgeMediaType.TEXT.value, KnowledgeMediaType.MARKDOWN.value):
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            raise ExtractionError("text_extraction_failed") from None
        sections = _markdown_sections(content) if media_type == KnowledgeMediaType.MARKDOWN.value else _text_sections(content)
    else:
        raise ExtractionError("unsupported_media_type")
    if sum(len(section.text) for section in sections) > max_characters:
        raise ExtractionError("extracted_text_too_large")
    if not sections or not any(section.text.strip() for section in sections):
        raise ExtractionError("insufficient_extractable_text")
    return sections


def _pdf_sections(path: Path, max_pages: int) -> list[DocumentSection]:
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise ExtractionError("encrypted_pdf")
        if len(reader.pages) > max_pages:
            raise ExtractionError("pdf_page_limit")
        sections = []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                sections.append(DocumentSection(text=text, page_start=number, page_end=number))
    except ExtractionError:
        raise
    except Exception:  # noqa: BLE001 - parser errors are untrusted input failures
        raise ExtractionError("pdf_extraction_failed") from None
    return sections


def _text_sections(content: str) -> list[DocumentSection]:
    return [
        DocumentSection(
            text=match.group(0),
            source_offset_start=match.start(),
            source_offset_end=match.end(),
        )
        for match in _PARAGRAPH.finditer(content)
        if match.group(0).strip()
    ]


def _markdown_sections(content: str) -> list[DocumentSection]:
    matches = list(_HEADING.finditer(content))
    if not matches:
        return _text_sections(content)
    sections: list[DocumentSection] = []
    if content[: matches[0].start()].strip():
        sections.extend(_text_sections(content[: matches[0].start()]))
    parents: list[tuple[int, str]] = []
    for index, heading in enumerate(matches):
        level = len(heading.group(1))
        parents = [(depth, label) for depth, label in parents if depth < level]
        parents.append((level, heading.group(2).strip()))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = content[heading.start() : end]
        if body.strip():
            sections.append(
                DocumentSection(
                    text=body,
                    heading=" / ".join(label for _, label in parents),
                    source_offset_start=heading.start(),
                    source_offset_end=end,
                )
            )
    return sections
