from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class BTPAccountRead(BaseModel):
    id: uuid.UUID
    external_account_id: str
    name: str
    account_type: str
    region: str | None
    # Null unless the caller can also read the parent account.
    parent_account_id: uuid.UUID | None
    created_at: datetime


class BTPAccountPage(BaseModel):
    items: list[BTPAccountRead]
    has_more: bool


class BTPEnvironmentRead(BaseModel):
    id: uuid.UUID
    btp_account_id: uuid.UUID
    name: str
    runtime_type: str
    external_runtime_id: str | None
    created_at: datetime


class BTPEnvironmentPage(BaseModel):
    items: list[BTPEnvironmentRead]
    has_more: bool


class BTPApplicationRead(BaseModel):
    id: str
    name: str
    state: str | None = None


class BTPApplicationPage(BaseModel):
    items: list[BTPApplicationRead]
    source: str
