"""Deterministic parent/child chunking measured with a real tiktoken encoding."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache

import tiktoken
from requests.exceptions import RequestException

from app.rag.extraction import DocumentSection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkConfig:
    parent_tokens: int = 1000
    child_tokens: int = 250
    child_overlap: int = 50
    tokenizer_name: str = "cl100k_base"
    version: str = "parent-child-v1"

    def __post_init__(self) -> None:
        if not 800 <= self.parent_tokens <= 1200:
            raise ValueError("parent target must be between 800 and 1,200 tokens")
        if not 180 <= self.child_tokens <= 300:
            raise ValueError("child target must be between 180 and 300 tokens")
        if not 40 <= self.child_overlap <= 50 or self.child_overlap >= self.child_tokens:
            raise ValueError("child overlap must be between 40 and 50 tokens")


@dataclass(frozen=True)
class ChildChunk:
    order: int
    text: str
    token_count: int
    fingerprint: str
    source_offset_start: int | None
    source_offset_end: int | None


@dataclass(frozen=True)
class ChunkedParent:
    order: int
    text: str
    token_count: int
    fingerprint: str
    heading: str | None
    page_start: int | None
    page_end: int | None
    source_offset_start: int | None
    source_offset_end: int | None
    children: tuple[ChildChunk, ...]


@lru_cache(maxsize=1)
def _encoding(name: str):
    try:
        return tiktoken.get_encoding(name)
    except RequestException:
        # Tiktoken's public encoding file is a small static tokenizer asset, but offline
        # lexical indexing must not depend on fetching it. One Unicode code point per
        # fallback token keeps chunks and evidence budgets bounded (usually conservatively).
        logger.warning("Tokenizer asset unavailable; using bounded character tokenization")
        return _CharacterEncoding()


class _CharacterEncoding:
    approximate = True

    def encode(self, value: str, disallowed_special=()) -> list[int]:
        return [ord(character) for character in value]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(token) for token in tokens)

    def decode_with_offsets(self, tokens: list[int]) -> tuple[str, list[int]]:
        return self.decode(tokens), list(range(len(tokens)))


def chunk_sections(
    sections: list[DocumentSection], config: ChunkConfig | None = None, *, max_chunks: int = 5000
) -> list[ChunkedParent]:
    """Chunk sections without crossing heading or PDF-page boundaries."""
    config = config or ChunkConfig()
    encoding = _encoding(config.tokenizer_name)
    parents: list[ChunkedParent] = []
    child_total = 0
    for section in sections:
        text = section.text
        if not text.strip():
            continue
        parent_windows = _windows(text, config.parent_tokens, 0, encoding)
        for parent_text, parent_start, parent_end, parent_count in parent_windows:
            parent_order = len(parents)
            child_windows = _windows(parent_text, config.child_tokens, config.child_overlap, encoding)
            children: list[ChildChunk] = []
            for child_text, child_start, child_end, child_count in child_windows:
                child_total += 1
                if child_total > max_chunks:
                    raise ValueError("document_exceeds_chunk_limit")
                children.append(
                    ChildChunk(
                        order=len(children),
                        text=child_text,
                        token_count=child_count,
                        fingerprint=_fingerprint(child_text),
                        source_offset_start=(section.source_offset_start + parent_start + child_start)
                        if section.source_offset_start is not None
                        else None,
                        source_offset_end=(section.source_offset_start + parent_start + child_end)
                        if section.source_offset_start is not None
                        else None,
                    )
                )
            if not children:
                continue
            parents.append(
                ChunkedParent(
                    order=parent_order,
                    text=parent_text,
                    token_count=parent_count,
                    fingerprint=_fingerprint(parent_text),
                    heading=section.heading,
                    page_start=section.page_start,
                    page_end=section.page_end,
                    source_offset_start=(section.source_offset_start + parent_start)
                    if section.source_offset_start is not None
                    else None,
                    source_offset_end=(section.source_offset_start + parent_end)
                    if section.source_offset_start is not None
                    else None,
                    children=tuple(children),
                )
            )
    return parents


def _windows(text: str, maximum: int, overlap: int, encoding) -> list[tuple[str, int, int, int]]:
    tokens = encoding.encode(text, disallowed_special=())
    if not tokens:
        return []
    decoded, offsets = encoding.decode_with_offsets(tokens)
    windows = []
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + maximum)
        if end < len(tokens):
            # Prefer a nearby paragraph/sentence/line boundary without exceeding target.
            floor = max(start + 1, end - max(24, maximum // 5))
            for candidate in range(end - 1, floor, -1):
                char_at = offsets[candidate] if candidate < len(offsets) else len(decoded)
                if char_at > 0 and decoded[char_at - 1 : char_at] in ".!?;:\n":
                    end = candidate + 1
                    break
        char_start = offsets[start]
        char_end = offsets[end] if end < len(offsets) else len(decoded)
        raw_piece = decoded[char_start:char_end]
        left = len(raw_piece) - len(raw_piece.lstrip())
        right = len(raw_piece.rstrip())
        piece = raw_piece[left:right]
        if piece:
            windows.append(
                (piece, char_start + left, char_start + right, len(tokens[start:end]))
            )
        if end >= len(tokens):
            break
        start = max(start + 1, end - overlap)
    return windows


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
