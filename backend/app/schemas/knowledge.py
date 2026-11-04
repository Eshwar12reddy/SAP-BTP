"""Knowledge API contracts.

Requests forbid unknown fields, so identity fields (owner_id, created_by, uploaded_by),
checksums, version numbers and storage keys can never be supplied by a client: they are set
by backend code. Responses never include storage keys or filesystem paths.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.knowledge import (
    DESCRIPTION_MAX_LENGTH,
    MAX_TAGS,
    NOTE_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    KnowledgeMediaType,
    KnowledgeScope,
    SourceStatus,
    ValidationStatus,
)

_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+-]{0,49}$")
# Control characters; descriptions and notes may contain tabs and line breaks.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_BREAKS = re.compile(r"[\t\n\r]")


def _single_line(value: str, max_length: int) -> str:
    value = value.strip()
    if not value or len(value) > max_length:
        raise ValueError(f"must be 1-{max_length} characters")
    if _CONTROL.search(value) or _LINE_BREAKS.search(value):
        raise ValueError("must be a single line without control characters")
    return value


def _text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"must be at most {max_length} characters")
    if _CONTROL.search(value):
        raise ValueError("must not contain control characters")
    return value or None


def _tags(values: list[str]) -> list[str]:
    tags: list[str] = []
    for raw in values:
        tag = " ".join(raw.split())
        if not _TAG.match(tag):
            raise ValueError("tags are 1-50 characters: letters, digits, space . _ : / + -")
        if tag.lower() not in (t.lower() for t in tags):
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")
    return tags


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRegistration(_Request):
    """Multipart form fields sent with the first file (field ``file``)."""

    title: str
    description: str | None = None
    tags: list[str] = []
    scope_type: KnowledgeScope
    scope_id: uuid.UUID | None = None
    change_note: str | None = None

    _title = field_validator("title")(lambda v: _single_line(v, TITLE_MAX_LENGTH))
    _description = field_validator("description")(lambda v: _text(v, DESCRIPTION_MAX_LENGTH))
    _note = field_validator("change_note")(lambda v: _text(v, NOTE_MAX_LENGTH))
    _tag_list = field_validator("tags")(_tags)

    @model_validator(mode="after")
    def _scope_shape(self) -> SourceRegistration:
        if (self.scope_type is KnowledgeScope.GLOBAL) != (self.scope_id is None):
            raise ValueError("scope_id is required for BTP scopes and forbidden for global")
        return self


class VersionUpload(_Request):
    """Multipart form fields sent with a replacement file (field ``file``)."""

    change_note: str | None = None

    _note = field_validator("change_note")(lambda v: _text(v, NOTE_MAX_LENGTH))


class SourceUpdate(_Request):
    """Metadata changes. ``revision`` is the revision the client read (optimistic lock)."""

    revision: int = Field(ge=1)
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("title cannot be removed")
        return _single_line(value, TITLE_MAX_LENGTH)

    _description = field_validator("description")(lambda v: _text(v, DESCRIPTION_MAX_LENGTH))

    @field_validator("tags")
    @classmethod
    def _tag_list(cls, value: list[str] | None) -> list[str]:
        return _tags(value or [])

    @model_validator(mode="after")
    def _has_change(self) -> SourceUpdate:
        if not self.model_fields_set - {"revision"}:
            raise ValueError("provide at least one of title, description, tags")
        return self

    def changes(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in sorted(self.model_fields_set - {"revision"})}


class SourceRemoval(_Request):
    reason: str

    _reason = field_validator("reason")(lambda v: _single_line(v, NOTE_MAX_LENGTH))


class KnowledgeVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    version_number: int
    original_filename: str
    media_type: KnowledgeMediaType
    size_bytes: int
    sha256: str
    uploaded_by: uuid.UUID
    validation_status: ValidationStatus
    validated_at: datetime | None
    change_note: str | None
    created_at: datetime
    is_active: bool = False


class KnowledgeSourceRead(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    tags: list[str]
    scope_type: KnowledgeScope
    btp_account_id: uuid.UUID | None
    btp_environment_id: uuid.UUID | None
    owner_id: uuid.UUID
    created_by: uuid.UUID
    status: SourceStatus
    revision: int
    created_at: datetime
    updated_at: datetime
    active_version: KnowledgeVersionRead


class KnowledgeSourcePage(BaseModel):
    items: list[KnowledgeSourceRead]
    has_more: bool


class KnowledgeVersionPage(BaseModel):
    items: list[KnowledgeVersionRead]
    has_more: bool


class KnowledgeRemovalRead(BaseModel):
    id: uuid.UUID
    status: SourceStatus
    removed_at: datetime
    removed_by: uuid.UUID
