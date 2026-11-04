from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.conversation import TITLE_MAX_LENGTH


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        max_length=TITLE_MAX_LENGTH,
        description='Optional title. Defaults to "New conversation".',
    )
    # Optional BTP context: one account *or* one environment the caller can read.
    btp_account_id: uuid.UUID | None = None
    btp_environment_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _single_context(self) -> ConversationCreate:
        if self.btp_account_id is not None and self.btp_environment_id is not None:
            raise ValueError("set at most one of btp_account_id and btp_environment_id")
        return self

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("title must not contain NUL characters")
        value = " ".join(value.split())
        if not value:
            raise ValueError("title must not be blank")
        return value


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    btp_account_id: uuid.UUID | None
    btp_environment_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ConversationPage(BaseModel):
    items: list[ConversationRead]
    has_more: bool
