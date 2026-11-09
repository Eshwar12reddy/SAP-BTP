from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.memory import MemoryCategory, MemoryVerification


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: MemoryCategory
    content: str = Field(min_length=1, max_length=8_000)
    btp_account_id: uuid.UUID | None = None
    btp_environment_id: uuid.UUID | None = None
    source_conversation_id: uuid.UUID | None = None
    source_investigation_reference: str | None = Field(default=None, max_length=255)
    source_evidence: list[str] = Field(default_factory=list, max_length=20)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value

    @model_validator(mode="after")
    def _single_scope(self) -> MemoryCreate:
        if self.btp_account_id is not None and self.btp_environment_id is not None:
            raise ValueError("set at most one BTP scope")
        return self


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=8_000)


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_user_id: uuid.UUID
    btp_account_id: uuid.UUID | None
    btp_environment_id: uuid.UUID | None
    category: MemoryCategory
    content: str
    source_conversation_id: uuid.UUID | None
    source_investigation_reference: str | None
    source_evidence: list[str]
    verification_status: MemoryVerification
    schema_version: int
    revision: int
    created_at: datetime
    updated_at: datetime
    last_verified_at: datetime | None
    expires_at: datetime | None


class MemoryPage(BaseModel):
    items: list[MemoryRead]
    has_more: bool
