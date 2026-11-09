"""Strict API contracts for non-secret SAP BTP connection metadata."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.btp_connection import ConnectionValidationResult

_REFERENCE = re.compile(r"^env://[A-Za-z0-9_.-]{1,220}$")
_AUTH_METHODS = {"bearer_token", "client_secret_basic"}


class BTPConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    btp_environment_id: uuid.UUID
    api_endpoint: str = Field(min_length=1, max_length=2048)
    credential_reference: str = Field(min_length=1, max_length=255)
    authentication_method: str = Field(min_length=1, max_length=64)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("display_name must not be blank")
        return value

    @field_validator("credential_reference")
    @classmethod
    def _reference(cls, value: str) -> str:
        value = value.strip()
        if not _REFERENCE.fullmatch(value):
            raise ValueError("credential_reference must be a registered env:// reference")
        return value

    @field_validator("authentication_method")
    @classmethod
    def _auth_method(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in _AUTH_METHODS:
            raise ValueError("authentication_method is not supported")
        return value


class BTPConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    api_endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    credential_reference: str | None = Field(default=None, min_length=1, max_length=255)
    authentication_method: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            raise ValueError("display_name must not be blank")
        return value

    @field_validator("credential_reference")
    @classmethod
    def _reference(cls, value: str | None) -> str | None:
        if value is not None and not _REFERENCE.fullmatch(value.strip()):
            raise ValueError("credential_reference must be a registered env:// reference")
        return value.strip() if value is not None else None

    @field_validator("authentication_method")
    @classmethod
    def _auth_method(cls, value: str | None) -> str | None:
        value = value.strip().lower() if value is not None else None
        if value is not None and value not in _AUTH_METHODS:
            raise ValueError("authentication_method is not supported")
        return value

    @model_validator(mode="after")
    def _has_change(self) -> BTPConnectionUpdate:
        if not self.model_fields_set - {"revision"}:
            raise ValueError("provide at least one connection setting")
        return self


class BTPConnectionValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    live: bool = False


class BTPConnectionRead(BaseModel):
    id: uuid.UUID
    display_name: str
    btp_account_id: uuid.UUID | None
    btp_environment_id: uuid.UUID
    environment_name: str
    runtime_type: str
    api_endpoint: str
    authentication_method: str
    credential_configured: bool
    enabled: bool
    status: str
    last_validation_at: datetime | None
    last_validation_result: ConnectionValidationResult
    last_validation_error_category: str | None
    revision: int
    created_at: datetime
    updated_at: datetime


class BTPConnectionPage(BaseModel):
    items: list[BTPConnectionRead]
    has_more: bool


class BTPConnectionValidationRead(BaseModel):
    connection: BTPConnectionRead
    result: ConnectionValidationResult
    error_category: str | None
    live_check_performed: bool


class AvailableEnvironmentRead(BaseModel):
    id: uuid.UUID
    name: str
    btp_account_id: uuid.UUID | None
    runtime_type: str
    connection_id: uuid.UUID | None
    connection_name: str | None
    enabled: bool
    status: str
    validation_result: ConnectionValidationResult | None


class AvailableEnvironmentPage(BaseModel):
    items: list[AvailableEnvironmentRead]
    has_more: bool


class ActiveEnvironmentRead(BaseModel):
    connection: BTPConnectionRead | None


class ActiveEnvironmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: uuid.UUID
