"""Stable, non-sensitive agent diagnostics."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentError(BaseModel):
    """A safe failure contract; details never contain provider or input text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=240)
    retryable: bool = False
