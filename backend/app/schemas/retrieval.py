"""Strict request and response schemas for lexical knowledge retrieval."""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=2, max_length=1000)
    max_results: int = Field(default=6, ge=1, le=8)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        value = value.strip()
        if not value or _CONTROL.search(value):
            raise ValueError("query must contain readable text")
        return value


class KnowledgeIndexRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["published", "failed", "unavailable"]
    generation_id: uuid.UUID | None
    parent_count: int = 0
    child_count: int = 0
    failure_code: str | None = None


class KnowledgeCitation(BaseModel):
    model_config = ConfigDict(frozen=True)

    citation_id: str
    source_id: uuid.UUID
    source_title: str
    source_version_id: uuid.UUID
    version_number: int
    page_start: int | None
    page_end: int | None
    section_heading: str | None
    parent_id: uuid.UUID
    child_id: uuid.UUID
    download_url: str


class KnowledgeEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    citation: KnowledgeCitation
    excerpt: str
    parent_context: str | None
    lexical_rank: int | None
    vector_rank: int | None = None
    fused_score: float


class KnowledgeSearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    retrieval_mode: Literal["hybrid", "lexical", "lexical_fallback", "lexical_no_match"]
    semantic_status: Literal[
        "available", "disabled_by_configuration", "openai_not_configured",
        "openai_unavailable", "model_mismatch", "pgvector_unavailable", "no_accessible_sources"
    ] = "disabled_by_configuration"
    reranking_status: Literal["disabled_model_not_approved"] = "disabled_model_not_approved"
    indexing_status: Literal["ready", "no_accessible_sources", "index_missing", "no_matching_evidence"]
    answer_mode: Literal["extractive_only"] = "extractive_only"
    items: list[KnowledgeEvidence]
