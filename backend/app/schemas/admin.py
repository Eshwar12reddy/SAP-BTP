from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.passwords import MAX_PASSWORD_LENGTH
from app.auth.permissions import RoleName, ScopeType
from app.models.user import DISPLAY_NAME_MAX_LENGTH, EMAIL_MAX_LENGTH
from app.schemas.auth import GrantRead, UserRead
from app.schemas.btp import BTPAccountRead, BTPEnvironmentRead


def _clean_display_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("display_name must not be blank")
    return value


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=EMAIL_MAX_LENGTH)
    display_name: str = Field(min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        value = value.strip().lower()
        local, _, domain = value.partition("@")
        if not local or "." not in domain or any(c.isspace() for c in value):
            raise ValueError("email must be a valid address")
        return value

    _display = field_validator("display_name")(_clean_display_name)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH)

    _display = field_validator("display_name")(_clean_display_name)


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: RoleName
    scope_type: ScopeType
    scope_id: uuid.UUID | None = None
    include_descendants: bool = False
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value


class AdminUserSummary(UserRead):
    roles: list[RoleName]


class AdminUserPage(BaseModel):
    items: list[AdminUserSummary]
    has_more: bool


class AdminUserDetail(BaseModel):
    user: UserRead
    grants: list[GrantRead]
    active_sessions: int
    # True when the caller may disable / update / revoke sessions for this user.
    manageable_account: bool


class SessionsRevokedResponse(BaseModel):
    sessions_revoked: int


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    event_type: str
    outcome: str
    actor_user_id: uuid.UUID | None
    target_user_id: uuid.UUID | None
    scope_type: str | None
    btp_account_id: uuid.UUID | None
    btp_environment_id: uuid.UUID | None
    request_id: str | None
    details: dict


class AuditEventPage(BaseModel):
    items: list[AuditEventRead]
    has_more: bool


class ManageableAccount(BaseModel):
    id: uuid.UUID
    name: str
    account_type: str
    parent_account_id: uuid.UUID | None


class ManageableEnvironment(BaseModel):
    id: uuid.UUID
    name: str
    runtime_type: str
    btp_account_id: uuid.UUID


class ManageableScopes(BaseModel):
    platform: bool
    accounts: list[ManageableAccount]
    environments: list[ManageableEnvironment]


class BTPAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_account_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    account_type: str
    region: str | None = Field(default=None, max_length=50)
    parent_account_id: uuid.UUID | None = None

    @field_validator("external_account_id", "name", "account_type", "region")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None


class BTPAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_account_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    region: str | None = Field(default=None, max_length=50)

    @field_validator("external_account_id", "name", "region")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None


class BTPEnvironmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    runtime_type: str = Field(min_length=1, max_length=100)
    external_runtime_id: str | None = Field(default=None, max_length=255)

    @field_validator("name", "runtime_type", "external_runtime_id")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None


class BTPEnvironmentUpdate(BTPEnvironmentCreate):
    pass


class BTPHierarchyRead(BaseModel):
    accounts: list[BTPAccountRead]
    environments: list[BTPEnvironmentRead]
