"""Application service for authorized, read-only Cloud Foundry observations."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Principal
from app.core.config import Settings
from app.core.exceptions import (
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    ServiceUnavailableError,
)
from app.integrations.sap_btp.cloud_foundry import (
    AuthorizedCloudFoundry,
    CFAppEvent,
    CFApplication,
    CFOrganization,
    CFProcess,
    CFProcessStat,
    CFQuota,
    CFServiceBinding,
    CFServiceInstance,
    CFServiceOffering,
    CFServicePlan,
    CFSpace,
    CFUsageSummary,
    CloudFoundryInventoryService,
    CloudFoundryProviderError,
    resolve_authorized_cloud_foundry,
)


def provider_error(error: CloudFoundryProviderError) -> Exception:
    messages = {
        "no_active_environment": "Select an active BTP environment before requesting live data.",
        "scope_mapping_missing": "The active environment has no trusted Cloud Foundry scope mapping.",
        "scope_mapping_invalid": "The active environment Cloud Foundry scope mapping is invalid.",
        "unsupported_runtime": "Live read-only connectors are not supported for this runtime.",
        "unsupported_authentication": "The active connection authentication method is not supported for Cloud Foundry reads.",
        "credentials_unavailable": "The active connection credentials are unavailable.",
        "credential_reference_unrecognized": "The active connection credentials are unavailable.",
        "resource_not_authorized": "The requested Cloud Foundry resource was not found.",
        "provider_not_found": "The requested Cloud Foundry resource was not found.",
        "provider_permission_denied": "The Cloud Foundry provider denied read access.",
        "authentication_expired": "The Cloud Foundry connection authentication has expired.",
        "invalid_endpoint": "The active Cloud Foundry endpoint is invalid.",
        "endpoint_not_allowlisted": "The active Cloud Foundry endpoint is not allowlisted.",
        "pagination_origin_rejected": "The Cloud Foundry pagination response was rejected.",
        "malformed_response": "Cloud Foundry returned an invalid response.",
        "response_too_large": "The Cloud Foundry response exceeded the safety limit.",
        "provider_timeout": "The Cloud Foundry provider did not respond in time.",
        "provider_unreachable": "The Cloud Foundry provider could not be reached.",
        "rate_limited": "The Cloud Foundry provider rate-limited this request.",
        "provider_unavailable": "The Cloud Foundry provider is temporarily unavailable.",
        "provider_request_failed": "The Cloud Foundry read request failed.",
    }
    message = messages.get(error.code, "The Cloud Foundry read request failed.")
    if error.code == "invalid_resource_id":
        return BadRequestError("The requested Cloud Foundry resource identifier is invalid.", code="btp_invalid_resource_id")
    if error.code in {"resource_not_authorized", "provider_not_found"}:
        return NotFoundError(message, code="btp_resource_not_found")
    if error.code == "provider_permission_denied":
        return PermissionDeniedError(message, code="btp_provider_permission_denied")
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
    return ServiceUnavailableError(message, code=f"btp_{error.code}", headers=headers)


class AuthorizedCloudFoundryService:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def inventory(
        self, session: AsyncSession, principal: Principal
    ) -> CloudFoundryInventoryService:
        try:
            authorized = await resolve_authorized_cloud_foundry(session, principal, self.settings)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return CloudFoundryInventoryService(
            authorized, self.settings, transport=self.transport
        )

    @staticmethod
    def evidence(authorized: AuthorizedCloudFoundry):
        from app.schemas.cloud_foundry import Evidence

        return Evidence(
            environment_id=authorized.context.btp_environment_id,
            connection_id=authorized.context.connection_id,
            observed_at=datetime.now(UTC),
        )

    async def organizations(self, session: AsyncSession, principal: Principal) -> tuple[AuthorizedCloudFoundry, list[CFOrganization], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.organizations()
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def organization(self, session: AsyncSession, principal: Principal, resource_id: str) -> tuple[AuthorizedCloudFoundry, CFOrganization]:
        inventory = await self.inventory(session, principal)
        try:
            item = await inventory.organization(resource_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, item

    async def spaces(self, session: AsyncSession, principal: Principal, organization_id: str | None = None) -> tuple[AuthorizedCloudFoundry, list[CFSpace], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.spaces(organization_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def space(self, session: AsyncSession, principal: Principal, resource_id: str) -> tuple[AuthorizedCloudFoundry, CFSpace]:
        inventory = await self.inventory(session, principal)
        try:
            item = await inventory.space(resource_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, item

    async def applications(self, session: AsyncSession, principal: Principal, space_id: str | None = None) -> tuple[AuthorizedCloudFoundry, list[CFApplication], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.applications(space_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def application(self, session: AsyncSession, principal: Principal, resource_id: str) -> tuple[AuthorizedCloudFoundry, CFApplication]:
        inventory = await self.inventory(session, principal)
        try:
            item = await inventory.application(resource_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, item

    async def processes(self, session: AsyncSession, principal: Principal, application_id: str) -> tuple[AuthorizedCloudFoundry, list[CFProcess], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.processes(application_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def process_stats(self, session: AsyncSession, principal: Principal, process_id: str) -> tuple[AuthorizedCloudFoundry, list[CFProcessStat]]:
        inventory = await self.inventory(session, principal)
        try:
            items = await inventory.process_stats(process_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items

    async def service_instances(self, session: AsyncSession, principal: Principal, space_id: str | None = None) -> tuple[AuthorizedCloudFoundry, list[CFServiceInstance], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.service_instances(space_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def service_bindings(self, session: AsyncSession, principal: Principal, application_id: str) -> tuple[AuthorizedCloudFoundry, list[CFServiceBinding], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.service_bindings(application_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def application_events(self, session: AsyncSession, principal: Principal, application_id: str) -> tuple[AuthorizedCloudFoundry, list[CFAppEvent], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.application_events(application_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def service_offerings(self, session: AsyncSession, principal: Principal) -> tuple[AuthorizedCloudFoundry, list[CFServiceOffering], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.service_offerings()
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def service_plans(self, session: AsyncSession, principal: Principal, service_offering_id: str | None = None) -> tuple[AuthorizedCloudFoundry, list[CFServicePlan], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.service_plans(service_offering_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def organization_quotas(self, session: AsyncSession, principal: Principal) -> tuple[AuthorizedCloudFoundry, list[CFQuota], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.organization_quotas()
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def space_quotas(self, session: AsyncSession, principal: Principal) -> tuple[AuthorizedCloudFoundry, list[CFQuota], bool]:
        inventory = await self.inventory(session, principal)
        try:
            items, more = await inventory.space_quotas()
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, items, more

    async def organization_usage_summary(self, session: AsyncSession, principal: Principal, organization_id: str) -> tuple[AuthorizedCloudFoundry, CFUsageSummary]:
        inventory = await self.inventory(session, principal)
        try:
            item = await inventory.organization_usage_summary(organization_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, item

    async def space_usage_summary(self, session: AsyncSession, principal: Principal, space_id: str) -> tuple[AuthorizedCloudFoundry, CFUsageSummary]:
        inventory = await self.inventory(session, principal)
        try:
            item = await inventory.space_usage_summary(space_id)
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        return inventory.authorized, item
