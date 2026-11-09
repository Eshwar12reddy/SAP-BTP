"""Typed status boundary for SAP services that are not configured in this deployment.

The adapter deliberately does not fall back to a Cloud Foundry token or permit a
caller-supplied URL. A future Destination/Entitlements implementation must register
its own official contract and credential mapping here before it can return live data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.schemas.cloud_foundry import InventoryStatusRead


class EnterpriseInventoryService:
    @staticmethod
    def destinations(environment_id: UUID | None = None) -> InventoryStatusRead:
        return InventoryStatusRead(
            resource_type="destination",
            source_type="LIVE_PROVIDER",
            retrieval_status="NOT_CONFIGURED",
            observed_at=datetime.now(UTC),
            environment_id=environment_id,
            reason="A dedicated authorized SAP Destination Service list adapter is not configured.",
        )

    @staticmethod
    def entitlements(environment_id: UUID | None = None) -> InventoryStatusRead:
        return InventoryStatusRead(
            resource_type="entitlement",
            source_type="LIVE_PROVIDER",
            retrieval_status="UNSUPPORTED",
            observed_at=datetime.now(UTC),
            environment_id=environment_id,
            reason="No supported official SAP Entitlements Service contract and credential adapter is configured.",
        )
