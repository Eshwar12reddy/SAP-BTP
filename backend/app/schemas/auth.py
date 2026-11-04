from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.auth.passwords import MAX_PASSWORD_LENGTH
from app.auth.permissions import RoleName, ScopeType
from app.models.user import EMAIL_MAX_LENGTH


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=EMAIL_MAX_LENGTH)
    # No policy here: policy applies to new passwords only. Bounded to cap hashing cost.
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class GrantRead(BaseModel):
    id: uuid.UUID
    role: RoleName
    scope_type: ScopeType
    scope_id: uuid.UUID | None
    scope_name: str | None
    include_descendants: bool
    expires_at: datetime | None
    created_at: datetime | None = None
    revoked_at: datetime | None = None


class MeResponse(BaseModel):
    user: UserRead
    roles: list[RoleName]
    permissions: list[str]
    grants: list[GrantRead]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserRead


class ChangePasswordResponse(BaseModel):
    other_sessions_revoked: int


class PermissionScope(BaseModel):
    scope_type: ScopeType
    scope_id: uuid.UUID | None
    include_descendants: bool


class EffectivePermission(BaseModel):
    permission: str
    scopes: list[PermissionScope]


class ScopeAccount(BaseModel):
    id: uuid.UUID
    name: str
    account_type: str


class ScopeEnvironment(BaseModel):
    id: uuid.UUID
    name: str
    runtime_type: str
    # Only present when the user can also read that account.
    btp_account_id: uuid.UUID | None


class BTPReadScopes(BaseModel):
    accounts: list[ScopeAccount]
    environments: list[ScopeEnvironment]


class MyScopesResponse(BaseModel):
    grants: list[GrantRead]
    btp_read: BTPReadScopes
