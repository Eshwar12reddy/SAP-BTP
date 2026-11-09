"""Bounded, read-only Cloud Foundry V3 connector.

The connector has no generic URL method and exposes only fixed GET resources. Provider
GUID mappings are server-controlled configuration, so names returned by Cloud Foundry
are never used to establish tenant authorization.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.auth.authorization import Principal, coverage_for
from app.auth.permissions import Permission
from app.core.config import Settings
from app.integrations.sap_btp.credentials import CredentialReferenceError
from app.services.btp_connection_service import BTPConnectionContext, authorized_connection_context

CF_API_VERSION = "v3"
CF_SOURCE = "cloud_foundry_v3"
_RESOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


class CloudFoundryProviderError(Exception):
    """Safe, typed provider failure; raw provider bodies are never retained."""

    def __init__(self, code: str, *, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def _safe_resource_id(value: str) -> str:
    if not isinstance(value, str) or not _RESOURCE_ID.fullmatch(value):
        raise CloudFoundryProviderError("invalid_resource_id")
    return value


@dataclass(frozen=True)
class CloudFoundryScopeMapping:
    organization_ids: frozenset[str]
    space_ids: frozenset[str]

    def allows_organization(self, organization_id: str) -> bool:
        return organization_id in self.organization_ids

    def allows_space(self, space_id: str) -> bool:
        return space_id in self.space_ids


def scope_mapping(settings: Settings, environment_id: str) -> CloudFoundryScopeMapping:
    raw = settings.BTP_CLOUD_FOUNDRY_SCOPE_MAP.get(environment_id)
    if not isinstance(raw, Mapping):
        raise CloudFoundryProviderError("scope_mapping_missing")

    def ids(key: str) -> frozenset[str]:
        values = raw.get(key, [])
        if not isinstance(values, list):
            raise CloudFoundryProviderError("scope_mapping_invalid")
        result: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                raise CloudFoundryProviderError("scope_mapping_invalid")
            normalized = value.strip()
            if not _RESOURCE_ID.fullmatch(normalized):
                raise CloudFoundryProviderError("scope_mapping_invalid")
            result.add(normalized)
        if len(result) != len(values):
            raise CloudFoundryProviderError("scope_mapping_invalid")
        return frozenset(result)

    organization_ids = ids("organization_ids")
    space_ids = ids("space_ids")
    if not organization_ids and not space_ids:
        raise CloudFoundryProviderError("scope_mapping_missing")
    return CloudFoundryScopeMapping(organization_ids, space_ids)


@dataclass(frozen=True)
class CFOrganization:
    id: str
    name: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class CFSpace:
    id: str
    name: str
    organization_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class CFApplication:
    id: str
    name: str
    state: str | None
    lifecycle: str | None
    space_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class CFProcess:
    id: str
    process_type: str
    state: str | None
    instances: int | None
    app_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    memory_in_mb: int | None = None
    disk_in_mb: int | None = None
    health_check_type: str | None = None


@dataclass(frozen=True)
class CFProcessStat:
    process_type: str | None
    index: int | None
    state: str | None
    observed_at: datetime | None
    cpu: float | None
    memory_bytes: int | None
    disk_bytes: int | None
    uptime_seconds: int | None
    memory_quota_bytes: int | None = None
    disk_quota_bytes: int | None = None


@dataclass(frozen=True)
class CFServiceInstance:
    id: str
    name: str
    space_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class CFServiceBinding:
    id: str
    binding_type: str
    app_id: str | None
    service_instance_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class CFAppEvent:
    id: str
    event_type: str | None
    app_id: str | None
    app_name: str | None
    process_type: str | None
    current_state: str | None
    previous_state: str | None
    created_at: datetime | None
    instance_count: int | None


@dataclass(frozen=True)
class CFServiceOffering:
    id: str
    name: str
    description: str | None
    available: bool | None
    service_broker_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class CFServicePlan:
    id: str
    name: str
    description: str | None
    available: bool | None
    service_offering_id: str | None
    service_broker_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class CFQuotaValue:
    value: int | None
    state: str


@dataclass(frozen=True)
class CFQuota:
    id: str
    name: str
    quota_type: str
    organization_ids: tuple[str, ...]
    space_ids: tuple[str, ...]
    total_memory_mb: CFQuotaValue
    per_process_memory_mb: CFQuotaValue
    total_instances: CFQuotaValue
    per_app_tasks: CFQuotaValue
    total_service_instances: CFQuotaValue
    total_service_keys: CFQuotaValue
    total_routes: CFQuotaValue
    total_reserved_ports: CFQuotaValue
    total_domains: CFQuotaValue
    log_rate_limit_bytes_per_second: CFQuotaValue
    paid_services_allowed: bool | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class CFUsageSummary:
    resource_id: str
    resource_type: str
    started_instances: int | None
    memory_in_mb: int | None
    observed_at: datetime


@dataclass(frozen=True)
class AuthorizedCloudFoundry:
    context: BTPConnectionContext
    mapping: CloudFoundryScopeMapping


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _guid(value: object) -> str:
    result = _string(value)
    if result is None or not _RESOURCE_ID.fullmatch(result):
        raise CloudFoundryProviderError("malformed_response")
    return result


def _relationship_guid(resource: Mapping[str, Any], name: str) -> str | None:
    relationships = resource.get("relationships")
    if not isinstance(relationships, Mapping):
        return None
    relationship = relationships.get(name)
    if not isinstance(relationship, Mapping):
        return None
    data = relationship.get("data")
    if not isinstance(data, Mapping):
        return None
    return _string(data.get("guid"))


def _parse_organization(resource: Mapping[str, Any]) -> CFOrganization:
    return CFOrganization(
        id=_guid(resource.get("guid")),
        name=_string(resource.get("name")) or "",
        created_at=_timestamp(resource.get("created_at")),
        updated_at=_timestamp(resource.get("updated_at")),
    )


def _parse_space(resource: Mapping[str, Any]) -> CFSpace:
    return CFSpace(
        id=_guid(resource.get("guid")),
        name=_string(resource.get("name")) or "",
        organization_id=_relationship_guid(resource, "organization"),
        created_at=_timestamp(resource.get("created_at")),
        updated_at=_timestamp(resource.get("updated_at")),
    )


def _parse_application(resource: Mapping[str, Any]) -> CFApplication:
    lifecycle = resource.get("lifecycle")
    lifecycle_type = lifecycle.get("type") if isinstance(lifecycle, Mapping) else None
    return CFApplication(
        id=_guid(resource.get("guid")),
        name=_string(resource.get("name")) or "",
        state=_string(resource.get("state")),
        lifecycle=_string(lifecycle_type),
        space_id=_relationship_guid(resource, "space"),
        created_at=_timestamp(resource.get("created_at")),
        updated_at=_timestamp(resource.get("updated_at")),
    )


def _parse_process(resource: Mapping[str, Any]) -> CFProcess:
    instances = resource.get("instances")
    health_check = resource.get("health_check")
    health_check_type = health_check.get("type") if isinstance(health_check, Mapping) else None
    return CFProcess(
        id=_guid(resource.get("guid")),
        process_type=_string(resource.get("type")) or "",
        state=_string(resource.get("state")),
        instances=instances if isinstance(instances, int) and not isinstance(instances, bool) else None,
        app_id=_relationship_guid(resource, "app"),
        memory_in_mb=resource.get("memory_in_mb") if isinstance(resource.get("memory_in_mb"), int) else None,
        disk_in_mb=resource.get("disk_in_mb") if isinstance(resource.get("disk_in_mb"), int) else None,
        health_check_type=_string(health_check_type),
        created_at=_timestamp(resource.get("created_at")),
        updated_at=_timestamp(resource.get("updated_at")),
    )


def _parse_process_stat(resource: Mapping[str, Any]) -> CFProcessStat:
    usage = resource.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    memory = usage.get("mem")
    disk = usage.get("disk")
    cpu = usage.get("cpu")
    return CFProcessStat(
        process_type=_string(resource.get("type")),
        index=resource.get("index") if isinstance(resource.get("index"), int) else None,
        state=_string(resource.get("state")),
        observed_at=_timestamp(usage.get("time")),
        cpu=cpu if isinstance(cpu, (float, int)) and not isinstance(cpu, bool) else None,
        memory_bytes=memory if isinstance(memory, int) and not isinstance(memory, bool) else None,
        disk_bytes=disk if isinstance(disk, int) and not isinstance(disk, bool) else None,
        memory_quota_bytes=(
            resource.get("mem_quota")
            if isinstance(resource.get("mem_quota"), int) and not isinstance(resource.get("mem_quota"), bool)
            else None
        ),
        disk_quota_bytes=(
            resource.get("disk_quota")
            if isinstance(resource.get("disk_quota"), int) and not isinstance(resource.get("disk_quota"), bool)
            else None
        ),
        uptime_seconds=(
            resource.get("uptime")
            if isinstance(resource.get("uptime"), int) and not isinstance(resource.get("uptime"), bool)
            else None
        ),
    )


def _parse_service_instance(resource: Mapping[str, Any]) -> CFServiceInstance:
    return CFServiceInstance(
        id=_guid(resource.get("guid")),
        name=_string(resource.get("name")) or "",
        space_id=_relationship_guid(resource, "space"),
        created_at=_timestamp(resource.get("created_at")),
        updated_at=_timestamp(resource.get("updated_at")),
    )


def _parse_binding(resource: Mapping[str, Any]) -> CFServiceBinding:
    binding_type = _string(resource.get("type")) or "unknown"
    return CFServiceBinding(
        id=_guid(resource.get("guid")),
        binding_type=binding_type,
        app_id=_relationship_guid(resource, "app"),
        service_instance_id=_relationship_guid(resource, "service_instance"),
        created_at=_timestamp(resource.get("created_at")),
        updated_at=_timestamp(resource.get("updated_at")),
    )


def _parse_event(resource: Mapping[str, Any]) -> CFAppEvent:
    state = resource.get("state")
    state = state if isinstance(state, Mapping) else {}
    instance_count = resource.get("instance_count")
    instance_count = instance_count if isinstance(instance_count, Mapping) else {}
    return CFAppEvent(
        id=_guid(resource.get("guid")),
        event_type=_string(resource.get("type")),
        app_id=(resource.get("app", {}).get("guid") if isinstance(resource.get("app"), Mapping) else None),
        app_name=(resource.get("app", {}).get("name") if isinstance(resource.get("app"), Mapping) else None),
        process_type=(resource.get("process", {}).get("type") if isinstance(resource.get("process"), Mapping) else None),
        current_state=_string(state.get("current")),
        previous_state=_string(state.get("previous")),
        created_at=_timestamp(resource.get("created_at")),
        instance_count=(
            instance_count.get("current")
            if isinstance(instance_count.get("current"), int)
            else None
        ),
    )


def _relationship_guids(resource: Mapping[str, Any], name: str) -> tuple[str, ...]:
    relationships = resource.get("relationships")
    relationship = relationships.get(name) if isinstance(relationships, Mapping) else None
    data = relationship.get("data") if isinstance(relationship, Mapping) else None
    if isinstance(data, Mapping):
        guid = _string(data.get("guid"))
        return (guid,) if guid else ()
    if isinstance(data, list):
        return tuple(
            guid for item in data if isinstance(item, Mapping)
            for guid in [_string(item.get("guid"))] if guid
        )
    return ()


def _parse_service_offering(resource: Mapping[str, Any]) -> CFServiceOffering:
    return CFServiceOffering(
        id=_guid(resource.get("guid")),
        name=_string(resource.get("name")) or "",
        description=_string(resource.get("description")),
        available=resource.get("available") if isinstance(resource.get("available"), bool) else None,
        service_broker_id=_relationship_guid(resource, "service_broker"),
        created_at=_timestamp(resource.get("created_at")),
        updated_at=_timestamp(resource.get("updated_at")),
    )


def _parse_service_plan(resource: Mapping[str, Any]) -> CFServicePlan:
    return CFServicePlan(
        id=_guid(resource.get("guid")),
        name=_string(resource.get("name")) or "",
        description=_string(resource.get("description")),
        available=resource.get("available") if isinstance(resource.get("available"), bool) else None,
        service_offering_id=_relationship_guid(resource, "service_offering"),
        service_broker_id=_relationship_guid(resource, "service_broker"),
        created_at=_timestamp(resource.get("created_at")),
        updated_at=_timestamp(resource.get("updated_at")),
    )


def _quota_value(value: object) -> CFQuotaValue:
    if value is None:
        return CFQuotaValue(None, "unknown")
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            return CFQuotaValue(None, "unlimited")
        return CFQuotaValue(value, "value")
    return CFQuotaValue(None, "unknown")


def _nested_value(resource: Mapping[str, Any], section: str, key: str) -> object:
    value = resource.get(section)
    return value.get(key) if isinstance(value, Mapping) else None


def _parse_quota(resource: Mapping[str, Any], quota_type: str) -> CFQuota:
    return CFQuota(
        id=_guid(resource.get("guid")),
        name=_string(resource.get("name")) or "",
        quota_type=quota_type,
        organization_ids=_relationship_guids(resource, "organizations"),
        space_ids=_relationship_guids(resource, "spaces"),
        total_memory_mb=_quota_value(_nested_value(resource, "apps", "total_memory_in_mb")),
        per_process_memory_mb=_quota_value(_nested_value(resource, "apps", "per_process_memory_in_mb")),
        total_instances=_quota_value(_nested_value(resource, "apps", "total_instances")),
        per_app_tasks=_quota_value(_nested_value(resource, "apps", "per_app_tasks")),
        total_service_instances=_quota_value(_nested_value(resource, "services", "total_service_instances")),
        total_service_keys=_quota_value(_nested_value(resource, "services", "total_service_keys")),
        total_routes=_quota_value(_nested_value(resource, "routes", "total_routes")),
        total_reserved_ports=_quota_value(_nested_value(resource, "routes", "total_reserved_ports")),
        total_domains=_quota_value(_nested_value(resource, "domains", "total_domains")),
        log_rate_limit_bytes_per_second=_quota_value(
            _nested_value(resource, "apps", "log_rate_limit_in_bytes_per_second")
        ),
        paid_services_allowed=(
            _nested_value(resource, "services", "paid_services_allowed")
            if isinstance(_nested_value(resource, "services", "paid_services_allowed"), bool)
            else None
        ),
        created_at=_timestamp(resource.get("created_at")),
        updated_at=_timestamp(resource.get("updated_at")),
    )


def _parse_usage_summary(payload: Mapping[str, Any], resource_id: str, resource_type: str) -> CFUsageSummary:
    usage = payload.get("usage_summary")
    usage = usage if isinstance(usage, Mapping) else {}
    started = usage.get("started_instances")
    memory = usage.get("memory_in_mb")
    return CFUsageSummary(
        resource_id=resource_id,
        resource_type=resource_type,
        started_instances=started if isinstance(started, int) and not isinstance(started, bool) else None,
        memory_in_mb=memory if isinstance(memory, int) and not isinstance(memory, bool) else None,
        observed_at=datetime.now(UTC),
    )


Parser = Callable[[Mapping[str, Any]], Any]


class CloudFoundryReadClient:
    """One authenticated, fixed-origin Cloud Foundry GET client."""

    def __init__(
        self,
        context: BTPConnectionContext,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.context = context
        self.settings = settings
        self.transport = transport
        self._client: httpx.AsyncClient | None = None
        self._origin = self._origin_for(context.api_endpoint)

    def _origin_for(self, endpoint: str) -> tuple[str, str, int | None]:
        try:
            parsed = urlsplit(endpoint)
            port = parsed.port
        except ValueError:
            raise CloudFoundryProviderError("invalid_endpoint") from None
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise CloudFoundryProviderError("invalid_endpoint")
        if (
            parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or port not in (None, 443)
        ):
            raise CloudFoundryProviderError("invalid_endpoint")
        host = parsed.hostname.lower().rstrip(".")
        allowed = {
            value.rstrip(".").lower() for value in self.settings.BTP_VALIDATION_ALLOWED_HOSTS
        }
        if not allowed or host not in allowed:
            raise CloudFoundryProviderError("endpoint_not_allowlisted")
        return parsed.scheme, host, port or 443

    async def __aenter__(self) -> Self:
        if self.context.runtime_type != "cloudfoundry":
            raise CloudFoundryProviderError("unsupported_runtime")
        if self.context.credential_resolver is None:
            raise CloudFoundryProviderError("credentials_unavailable")
        try:
            credential = self.context.resolve_credentials()
        except CredentialReferenceError as exc:
            raise CloudFoundryProviderError(exc.code) from None
        if self.context.authentication_method != "bearer_token":
            raise CloudFoundryProviderError("unsupported_authentication")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.context.timeout_seconds),
            verify=True,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=self.settings.BTP_CLOUD_FOUNDRY_MAX_CONCURRENCY,
                max_keepalive_connections=self.settings.BTP_CLOUD_FOUNDRY_MAX_CONCURRENCY,
            ),
            transport=self.transport,
            headers={"Authorization": f"Bearer {credential.value}"},
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _url(self, path: str) -> str:
        return self.context.api_endpoint.rstrip("/") + "/" + CF_API_VERSION + "/" + path.lstrip("/")

    def _validate_follow_url(self, value: str) -> str:
        parsed = urlsplit(value)
        scheme, host, port = self._origin
        if (
            parsed.scheme != scheme
            or (parsed.hostname or "").lower().rstrip(".") != host
            or (parsed.port or 443) != port
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not parsed.path.startswith(f"/{CF_API_VERSION}/")
        ):
            raise CloudFoundryProviderError("pagination_origin_rejected")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))

    async def _get_json(self, url: str, params: Mapping[str, str | int] | None = None) -> Any:
        if self._client is None:
            raise RuntimeError("CloudFoundryReadClient must be used as an async context manager")
        attempts = self.settings.BTP_CONNECTION_MAX_RETRIES + 1
        for attempt in range(attempts):
            try:
                async with self._client.stream("GET", url, params=params) as response:
                    retry_after = self._retry_after(response.headers.get("Retry-After"))
                    if response.status_code == 401:
                        raise CloudFoundryProviderError("authentication_expired")
                    if response.status_code == 403:
                        raise CloudFoundryProviderError("provider_permission_denied")
                    if response.status_code == 404:
                        raise CloudFoundryProviderError("provider_not_found")
                    if response.status_code == 429:
                        if attempt + 1 < attempts:
                            await self._sleep_retry(retry_after)
                            continue
                        raise CloudFoundryProviderError("rate_limited", retry_after=retry_after)
                    if response.status_code >= 500:
                        if attempt + 1 < attempts:
                            await self._sleep_retry(retry_after)
                            continue
                        raise CloudFoundryProviderError("provider_unavailable")
                    if response.status_code < 200 or response.status_code >= 300:
                        raise CloudFoundryProviderError("provider_request_failed")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > self.settings.BTP_CLOUD_FOUNDRY_MAX_RESPONSE_BYTES:
                            raise CloudFoundryProviderError("response_too_large")
                    try:
                        return json.loads(content)
                    except (TypeError, json.JSONDecodeError):
                        raise CloudFoundryProviderError("malformed_response") from None
            except CloudFoundryProviderError:
                raise
            except httpx.TimeoutException:
                if attempt + 1 < attempts:
                    continue
                raise CloudFoundryProviderError("provider_timeout") from None
            except httpx.ConnectError:
                raise CloudFoundryProviderError("provider_unreachable") from None
            except httpx.RequestError:
                raise CloudFoundryProviderError("provider_unreachable") from None
        raise CloudFoundryProviderError("provider_unavailable")

    async def _sleep_retry(self, retry_after: int | None) -> None:
        delay = min(
            float(retry_after or 0), self.settings.BTP_CLOUD_FOUNDRY_RETRY_DELAY_CAP_SECONDS
        )
        if delay > 0:
            await asyncio.sleep(delay)

    @staticmethod
    def _retry_after(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return max(0, min(parsed, 3600))

    async def get(self, path: str, *, params: Mapping[str, str | int] | None = None, parser: Parser) -> Any:
        return parser(await self._get_json(self._url(path), params))

    async def list_resources(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        parser: Parser,
    ) -> tuple[list[Any], bool]:
        page = 1
        next_url: str | None = None
        items: list[Any] = []
        has_more = False
        per_page = min(100, self.settings.BTP_CLOUD_FOUNDRY_MAX_RESULTS)
        while page <= self.settings.BTP_CLOUD_FOUNDRY_MAX_PAGES and len(items) < self.settings.BTP_CLOUD_FOUNDRY_MAX_RESULTS:
            query = None if next_url else {**(params or {}), "page": str(page), "per_page": str(per_page)}
            payload = await self._get_json(next_url or self._url(path), query)
            if not isinstance(payload, Mapping) or not isinstance(payload.get("resources"), list):
                raise CloudFoundryProviderError("malformed_response")
            for resource in payload["resources"]:
                if not isinstance(resource, Mapping):
                    raise CloudFoundryProviderError("malformed_response")
                items.append(parser(resource))
                if len(items) >= self.settings.BTP_CLOUD_FOUNDRY_MAX_RESULTS:
                    has_more = True
                    break
            pagination = payload.get("pagination")
            next_href = pagination.get("next") if isinstance(pagination, Mapping) else None
            next_href = next_href.get("href") if isinstance(next_href, Mapping) else next_href
            if not isinstance(next_href, str) or not next_href:
                next_url = None
                break
            next_url = self._validate_follow_url(next_href)
            page += 1
            has_more = True
        return items[: self.settings.BTP_CLOUD_FOUNDRY_MAX_RESULTS], has_more and next_url is not None


class CloudFoundryInventoryService:
    """Authorization-aware inventory facade built on the fixed GET client."""

    def __init__(
        self,
        authorized: AuthorizedCloudFoundry,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.authorized = authorized
        self.settings = settings
        self.transport = transport

    async def _spaces_for_mapping(self, client: CloudFoundryReadClient) -> list[CFSpace]:
        params: dict[str, str] = {}
        if self.authorized.mapping.space_ids:
            params["guids"] = ",".join(sorted(self.authorized.mapping.space_ids))
        elif self.authorized.mapping.organization_ids:
            params["organization_guids"] = ",".join(
                sorted(self.authorized.mapping.organization_ids)
            )
        spaces, _ = await client.list_resources("spaces", params=params, parser=_parse_space)
        return [
            space
            for space in spaces
            if (
                space.id in self.authorized.mapping.space_ids
                or space.organization_id in self.authorized.mapping.organization_ids
            )
        ]

    def _assert_org(self, organization_id: str) -> None:
        if not self.authorized.mapping.allows_organization(organization_id):
            raise CloudFoundryProviderError("resource_not_authorized")

    async def _assert_space(self, client: CloudFoundryReadClient, space_id: str) -> CFSpace:
        spaces = await self._spaces_for_mapping(client)
        for space in spaces:
            if space.id == space_id:
                return space
        raise CloudFoundryProviderError("resource_not_authorized")

    async def organizations(self) -> tuple[list[CFOrganization], bool]:
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            organization_ids = set(self.authorized.mapping.organization_ids)
            if not organization_ids:
                spaces = await self._spaces_for_mapping(client)
                organization_ids = {space.organization_id for space in spaces if space.organization_id}
            if not organization_ids:
                return [], False
            return await client.list_resources(
                "organizations",
                params={"guids": ",".join(sorted(organization_ids))},
                parser=_parse_organization,
            )

    async def organization(self, organization_id: str) -> CFOrganization:
        organization_id = _safe_resource_id(organization_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            if not self.authorized.mapping.allows_organization(organization_id):
                spaces = await self._spaces_for_mapping(client)
                if not any(space.organization_id == organization_id for space in spaces):
                    raise CloudFoundryProviderError("resource_not_authorized")
            return await client.get(f"organizations/{organization_id}", parser=_parse_organization)

    async def spaces(self, organization_id: str | None = None) -> tuple[list[CFSpace], bool]:
        if organization_id is not None:
            organization_id = _safe_resource_id(organization_id)
            self._assert_org(organization_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            if organization_id:
                params = {"organization_guids": organization_id}
            else:
                params = {}
                if self.authorized.mapping.space_ids:
                    params["guids"] = ",".join(sorted(self.authorized.mapping.space_ids))
                elif self.authorized.mapping.organization_ids:
                    params["organization_guids"] = ",".join(
                        sorted(self.authorized.mapping.organization_ids)
                    )
            result = await client.list_resources("spaces", params=params, parser=_parse_space)
            return (
                [
                    space
                    for space in result[0]
                    if space.id in self.authorized.mapping.space_ids
                    or space.organization_id in self.authorized.mapping.organization_ids
                ],
                result[1],
            )

    async def space(self, space_id: str) -> CFSpace:
        space_id = _safe_resource_id(space_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            await self._assert_space(client, space_id)
            return await client.get(f"spaces/{space_id}", parser=_parse_space)

    async def applications(self, space_id: str | None = None) -> tuple[list[CFApplication], bool]:
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            if space_id:
                space_id = _safe_resource_id(space_id)
                await self._assert_space(client, space_id)
                space_ids = [space_id]
            else:
                space_ids = [space.id for space in await self._spaces_for_mapping(client)]
            if not space_ids:
                return [], False
            result = await client.list_resources(
                "apps",
                params={"space_guids": ",".join(space_ids), "include": "space"},
                parser=_parse_application,
            )
            return [app for app in result[0] if app.space_id in set(space_ids)], result[1]

    async def application(self, application_id: str) -> CFApplication:
        application_id = _safe_resource_id(application_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            application = await client.get(
                f"apps/{application_id}",
                params={"include": "space"},
                parser=_parse_application,
            )
            if application.space_id is None:
                raise CloudFoundryProviderError("malformed_response")
            await self._assert_space(client, application.space_id)
            return application

    async def processes(self, application_id: str) -> tuple[list[CFProcess], bool]:
        application_id = _safe_resource_id(application_id)
        application = await self.application(application_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            result = await client.list_resources(
                f"apps/{application.id}/processes", parser=_parse_process
            )
            return [process for process in result[0] if process.app_id in (None, application.id)], result[1]

    async def process_stats(self, process_id: str) -> list[CFProcessStat]:
        process_id = _safe_resource_id(process_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            process = await client.get(f"processes/{process_id}", parser=_parse_process)
            if not process.app_id:
                raise CloudFoundryProviderError("malformed_response")
            await self.application(process.app_id)
            payload = await client.get(f"processes/{process_id}/stats", parser=lambda value: value)
            if not isinstance(payload, Mapping) or not isinstance(payload.get("resources"), list):
                raise CloudFoundryProviderError("malformed_response")
            return [
                _parse_process_stat(item)
                for item in payload["resources"]
                if isinstance(item, Mapping)
            ]

    async def service_instances(self, space_id: str | None = None) -> tuple[list[CFServiceInstance], bool]:
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            if space_id:
                space_id = _safe_resource_id(space_id)
                await self._assert_space(client, space_id)
                space_ids = [space_id]
            else:
                space_ids = [space.id for space in await self._spaces_for_mapping(client)]
            if not space_ids:
                return [], False
            result = await client.list_resources(
                "service_instances",
                params={"space_guids": ",".join(space_ids)},
                parser=_parse_service_instance,
            )
            return [item for item in result[0] if item.space_id in set(space_ids)], result[1]

    async def service_bindings(self, application_id: str) -> tuple[list[CFServiceBinding], bool]:
        application_id = _safe_resource_id(application_id)
        await self.application(application_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            result = await client.list_resources(
                "service_bindings", params={"app_guids": application_id}, parser=_parse_binding
            )
            return [item for item in result[0] if item.app_id in (None, application_id)], result[1]

    async def application_events(self, application_id: str) -> tuple[list[CFAppEvent], bool]:
        application_id = _safe_resource_id(application_id)
        await self.application(application_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            events, has_more = await client.list_resources(
                "app_usage_events",
                params={"order_by": "-created_at"},
                parser=_parse_event,
            )
            filtered = [event for event in events if event.app_id == application_id]
            return filtered, has_more

    async def service_offerings(self) -> tuple[list[CFServiceOffering], bool]:
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            return await client.list_resources("service_offerings", parser=_parse_service_offering)

    async def service_plans(
        self, service_offering_id: str | None = None
    ) -> tuple[list[CFServicePlan], bool]:
        params: dict[str, str] = {}
        if service_offering_id is not None:
            params["service_offering_guids"] = _safe_resource_id(service_offering_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            return await client.list_resources(
                "service_plans", params=params, parser=_parse_service_plan
            )

    async def organization_quotas(self) -> tuple[list[CFQuota], bool]:
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            allowed = set(self.authorized.mapping.organization_ids)
            if not allowed and self.authorized.mapping.space_ids:
                spaces = await self._spaces_for_mapping(client)
                allowed.update(space.organization_id for space in spaces if space.organization_id)
            quotas, more = await client.list_resources(
                "organization_quotas", parser=lambda item: _parse_quota(item, "organization")
            )
        filtered = [quota for quota in quotas if set(quota.organization_ids) & allowed]
        return filtered, more

    async def space_quotas(self) -> tuple[list[CFQuota], bool]:
        allowed_orgs = self.authorized.mapping.organization_ids
        allowed_spaces = self.authorized.mapping.space_ids
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            quotas, more = await client.list_resources(
                "space_quotas", parser=lambda item: _parse_quota(item, "space")
            )
        filtered = [
            quota
            for quota in quotas
            if (set(quota.space_ids) & allowed_spaces)
            or (set(quota.organization_ids) & allowed_orgs)
        ]
        return filtered, more

    async def organization_usage_summary(self, organization_id: str) -> CFUsageSummary:
        organization_id = _safe_resource_id(organization_id)
        self._assert_org(organization_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            payload = await client.get(
                f"organizations/{organization_id}/usage_summary", parser=lambda value: value
            )
        if not isinstance(payload, Mapping):
            raise CloudFoundryProviderError("malformed_response")
        return _parse_usage_summary(payload, organization_id, "organization")

    async def space_usage_summary(self, space_id: str) -> CFUsageSummary:
        space_id = _safe_resource_id(space_id)
        async with CloudFoundryReadClient(self.authorized.context, self.settings, transport=self.transport) as client:
            await self._assert_space(client, space_id)
            payload = await client.get(f"spaces/{space_id}/usage_summary", parser=lambda value: value)
        if not isinstance(payload, Mapping):
            raise CloudFoundryProviderError("malformed_response")
        return _parse_usage_summary(payload, space_id, "space")


async def resolve_authorized_cloud_foundry(
    session: Any, principal: Principal, settings: Settings
) -> AuthorizedCloudFoundry:
    coverage = await coverage_for(session, principal, Permission.BTP_READ)
    context = await authorized_connection_context(session, principal, settings)
    if context is None:
        raise CloudFoundryProviderError("no_active_environment")
    if not coverage.covers_environment(context.btp_environment_id):
        raise CloudFoundryProviderError("resource_not_authorized")
    if context.runtime_type != "cloudfoundry":
        raise CloudFoundryProviderError("unsupported_runtime")
    return AuthorizedCloudFoundry(
        context=context,
        mapping=scope_mapping(settings, str(context.btp_environment_id)),
    )
