"""Adapter boundary for live SAP BTP data.

No live integration exists yet. ``UnavailableBTPProvider`` is the default and reports
that clearly instead of fabricating data. A real implementation (Cloud Foundry, Kyma,
BTP Accounts API) implements ``BTPProvider`` and is installed on ``app.state``.
Callers must authorize the scope *before* calling a provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import BTPEnvironment


@dataclass(frozen=True)
class BTPApplication:
    id: str
    name: str
    state: str | None = None


class BTPIntegrationUnavailable(Exception):
    """The live SAP BTP integration is not configured."""


class BTPProvider(Protocol):
    name: str

    async def list_applications(self, environment: BTPEnvironment) -> list[BTPApplication]: ...


class UnavailableBTPProvider:
    name = "unavailable"

    async def list_applications(self, environment: BTPEnvironment) -> list[BTPApplication]:
        raise BTPIntegrationUnavailable()
