from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.message import MessageRole

MESSAGE_MAX_LENGTH = 32_000


class MessageCreate(BaseModel):
    """A message submitted by the client. Only the ``user`` role may be created here."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)
    client_message_id: uuid.UUID = Field(
        description="Client-generated id. Retrying with the same id never creates a duplicate."
    )
    role: Literal["user"] = "user"

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        # Stored verbatim (multiline and indentation preserved); only rejected if blank.
        if not value.strip():
            raise ValueError("content must not be blank")
        if "\x00" in value:
            raise ValueError("content must not contain NUL characters")
        return value


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    client_message_id: uuid.UUID | None
    created_at: datetime


class MessagePage(BaseModel):
    """Messages in chronological order. ``has_more`` means older messages exist."""

    items: list[MessageRead]
    has_more: bool
