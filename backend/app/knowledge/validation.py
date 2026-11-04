"""Content-based validation of uploaded knowledge documents.

Accepted: PDF (``.pdf``), plain text (``.txt``) and Markdown (``.md``, ``.markdown``).

- The extension must be one of these and the content must match it: a PDF header, or strict
  UTF-8 text without NUL or other control characters. The client's Content-Type is ignored.
- Archives, executables and other known binary formats are rejected by their signature.
- PDFs get a bounded structural check with pypdf: parseable, not encrypted, 1..max pages,
  and no JavaScript, launch actions, embedded files or rich media in the raw bytes. No text
  is extracted and nothing is executed.

This is format validation, NOT malware scanning: names hidden inside compressed object
streams are not visible to the byte check, and a well-formed file can still be malicious.
"""

from __future__ import annotations

import codecs
import io
import logging
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from app.models.knowledge import FILENAME_MAX_LENGTH, KnowledgeMediaType
from app.safety.input_guard import sanitize

_EXTENSIONS = {
    ".pdf": KnowledgeMediaType.PDF,
    ".txt": KnowledgeMediaType.TEXT,
    ".md": KnowledgeMediaType.MARKDOWN,
    ".markdown": KnowledgeMediaType.MARKDOWN,
}
_BLOCKED_SIGNATURES = (
    b"PK\x03\x04",  # zip, docx/xlsx, jar, apk
    b"PK\x05\x06",
    b"MZ",  # Windows executables
    b"\x7fELF",
    b"\xca\xfe\xba\xbe",  # Mach-O universal / Java class
    b"\xfe\xed\xfa",
    b"\xcf\xfa\xed\xfe",
    b"Rar!",
    b"7z\xbc\xaf\x27\x1c",
    b"\x1f\x8b",  # gzip
    b"BZh",
    b"\xfd7zXZ",
    b"\xd0\xcf\x11\xe0",  # OLE (legacy Office, may carry macros)
    b"#!",  # scripts
)
_PDF_ACTIVE_CONTENT = re.compile(rb"/(?:JavaScript|JS|Launch|EmbeddedFiles?|RichMedia)\b")
# Control characters other than tab, line feed, form feed and carriage return.
_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
_FILENAME_RESERVED = re.compile(r'[<>:"|?*]')

logging.getLogger("pypdf").setLevel(logging.ERROR)


class FileRejected(Exception):
    """``reason`` is a stable code, safe to return and to audit."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


REJECTION_MESSAGES = {
    "unsupported_type": "Only PDF (.pdf), text (.txt) and Markdown (.md) files are accepted.",
    "executable_or_archive": "Archives, executables and similar binary files are not accepted.",
    "content_mismatch": "The file content does not match its file type.",
    "invalid_text": "Text files must be UTF-8 without control characters.",
    "empty_content": "The document contains no text.",
    "malformed_pdf": "The PDF could not be read.",
    "encrypted_pdf": "Encrypted or password-protected PDFs are not accepted.",
    "too_many_pages": "The PDF has more pages than allowed.",
    "active_pdf_content": "PDFs with scripts, launch actions or embedded files are not accepted.",
    "validation_timeout": "The document took too long to validate.",
}


def clean_filename(name: str | None) -> str:
    """A safe display name. It is never used as a path: storage uses opaque keys."""
    raw = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    raw = unicodedata.normalize("NFC", sanitize(raw)[0])
    raw = _FILENAME_RESERVED.sub("_", raw)
    raw = " ".join(raw.split()).strip(" .")
    if not raw:
        return "document"
    if len(raw) > FILENAME_MAX_LENGTH:
        stem, dot, extension = raw.rpartition(".")
        if dot and len(extension) <= 10:
            raw = f"{stem[: FILENAME_MAX_LENGTH - len(extension) - 1]}.{extension}"
        else:
            raw = raw[:FILENAME_MAX_LENGTH]
    return raw


def validate_document(
    filename: str, path: Path, head: bytes, *, pdf_max_pages: int
) -> KnowledgeMediaType:
    """Return the detected media type, or raise ``FileRejected``. Blocking; run in a thread."""
    media_type = _EXTENSIONS.get(Path(filename).suffix.lower())
    if media_type is None:
        raise FileRejected("unsupported_type")
    if head.startswith(_BLOCKED_SIGNATURES):
        raise FileRejected("executable_or_archive")
    is_pdf = head.startswith(b"%PDF-")
    if (media_type is KnowledgeMediaType.PDF) != is_pdf:
        raise FileRejected("content_mismatch")
    if is_pdf:
        _validate_pdf(path.read_bytes(), pdf_max_pages)
    else:
        _validate_text(path)
    return media_type


def _validate_pdf(data: bytes, max_pages: int) -> None:
    if _PDF_ACTIVE_CONTENT.search(data):
        raise FileRejected("active_pdf_content")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise FileRejected("encrypted_pdf")
        pages = len(reader.pages)
    except FileRejected:
        raise
    except Exception:  # noqa: BLE001 - any parse failure (pypdf errors, RecursionError...) rejects
        # The file is untrusted input; the exception text is not needed or exposed.
        raise FileRejected("malformed_pdf") from None
    if pages < 1:
        raise FileRejected("malformed_pdf")
    if pages > max_pages:
        raise FileRejected("too_many_pages")


def _validate_text(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="strict")
    has_text = False
    try:
        with open(path, "rb") as source:
            while chunk := source.read(64 * 1024):
                text = decoder.decode(chunk)
                if _TEXT_CONTROL.search(text):
                    raise FileRejected("invalid_text")
                has_text = has_text or bool(text.strip())
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        raise FileRejected("invalid_text") from None
    if not has_text:
        raise FileRejected("empty_content")
