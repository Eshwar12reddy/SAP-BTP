"""Regression tests for bounded text extraction and source offsets."""

from __future__ import annotations

import pytest

from app.rag.extraction import ExtractionError, extract_document


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("First paragraph.\n\nSecond paragraph.  \n", ["First paragraph.", "Second paragraph."]),
        ("First paragraph.\r\n\r\nSecond paragraph. \r\n", ["First paragraph.", "Second paragraph."]),
        ("First paragraph.\n\nSecond paragraph.", ["First paragraph.", "Second paragraph."]),
        ("Unicode résumé.\n\nदूसरा अनुच्छेद।", ["Unicode résumé.", "दूसरा अनुच्छेद।"]),
    ],
)
def test_text_extraction_keeps_final_paragraph_with_trailing_whitespace(
    tmp_path, content: str, expected: list[str]
) -> None:
    path = tmp_path / "notes.txt"
    path.write_text(content, encoding="utf-8")

    sections = extract_document(
        path,
        "text/plain",
        max_bytes=10_000,
        max_pages=10,
        max_characters=10_000,
    )

    assert [section.text for section in sections] == expected
    assert all(section.source_offset_start is not None for section in sections)
    assert all(section.source_offset_end is not None for section in sections)


def test_whitespace_only_text_is_rejected(tmp_path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text(" \r\n\n\t", encoding="utf-8")

    with pytest.raises(ExtractionError, match="insufficient_extractable_text"):
        extract_document(path, "text/plain", max_bytes=10_000, max_pages=10)
