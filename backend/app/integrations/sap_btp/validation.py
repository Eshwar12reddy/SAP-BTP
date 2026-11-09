"""Bounded, secret-safe SAP BTP connection validation."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.integrations.sap_btp.credentials import CredentialReferenceError, CredentialResolver
from app.models.btp_account import RuntimeType
from app.models.btp_connection import BTPConnection, ConnectionValidationResult

SUPPORTED_AUTHENTICATION_METHODS = frozenset({"bearer_token", "client_secret_basic"})
MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True)
class ValidationOutcome:
    result: ConnectionValidationResult
    error_category: str | None = None
    live_check_performed: bool = False


def endpoint_error(
    endpoint: str, settings: Settings, *, require_allowlist: bool = False
) -> str | None:
    """Validate URL shape and reject obvious/private SSRF targets without DNS calls."""
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return "invalid_endpoint"
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
    ):
        return "invalid_endpoint"
    normalized = host.rstrip(".").lower()
    if require_allowlist and not settings.BTP_VALIDATION_ALLOWED_HOSTS:
        return "endpoint_allowlist_required"
    if settings.BTP_VALIDATION_ALLOWED_HOSTS and normalized not in {
        value.rstrip(".").lower() for value in settings.BTP_VALIDATION_ALLOWED_HOSTS
    }:
        return "endpoint_not_allowed"
    if normalized in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        return "private_endpoint"
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return "private_endpoint"
    return None


def validate_configuration(
    connection: BTPConnection,
    runtime_type: str,
    resolver: CredentialResolver,
    settings: Settings,
) -> ValidationOutcome:
    if endpoint_error(connection.api_endpoint, settings):
        return ValidationOutcome(ConnectionValidationResult.INVALID_CONFIGURATION, "invalid_endpoint")
    if runtime_type not in {runtime.value for runtime in RuntimeType}:
        return ValidationOutcome(ConnectionValidationResult.UNSUPPORTED_RUNTIME, "runtime_unsupported")
    if runtime_type == RuntimeType.NEO.value:
        return ValidationOutcome(ConnectionValidationResult.UNSUPPORTED_RUNTIME, "runtime_unsupported")
    if connection.authentication_method not in SUPPORTED_AUTHENTICATION_METHODS:
        return ValidationOutcome(
            ConnectionValidationResult.UNSUPPORTED_AUTHENTICATION, "authentication_unsupported"
        )
    if not resolver.is_registered(connection.credential_reference):
        return ValidationOutcome(
            ConnectionValidationResult.INVALID_CONFIGURATION, "credential_reference_unrecognized"
        )
    return ValidationOutcome(ConnectionValidationResult.VALID_CONFIGURATION)


async def validate_live(
    connection: BTPConnection,
    runtime_type: str,
    resolver: CredentialResolver,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ValidationOutcome:
    configuration = validate_configuration(connection, runtime_type, resolver, settings)
    if configuration.result is not ConnectionValidationResult.VALID_CONFIGURATION:
        return configuration
    if endpoint_error(connection.api_endpoint, settings, require_allowlist=True):
        return ValidationOutcome(
            ConnectionValidationResult.INVALID_CONFIGURATION, "endpoint_not_allowlisted"
        )
    if not connection.enabled:
        return ValidationOutcome(ConnectionValidationResult.CONNECTION_DISABLED, "connection_disabled")
    if runtime_type != RuntimeType.CLOUD_FOUNDRY.value:
        return ValidationOutcome(ConnectionValidationResult.UNSUPPORTED_RUNTIME, "runtime_unsupported")
    if connection.authentication_method != "bearer_token":
        return ValidationOutcome(
            ConnectionValidationResult.UNSUPPORTED_AUTHENTICATION, "authentication_unsupported"
        )
    try:
        credential = resolver.resolve(connection.credential_reference)
    except CredentialReferenceError as exc:
        return ValidationOutcome(ConnectionValidationResult.CREDENTIALS_UNAVAILABLE, exc.code)

    health_url = connection.api_endpoint.rstrip("/") + "/v3/info"
    timeout = httpx.Timeout(settings.BTP_CONNECTION_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            verify=True,
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": f"Bearer {credential.value}"},
        ) as client:
            for attempt in range(settings.BTP_CONNECTION_MAX_RETRIES + 1):
                try:
                    async with client.stream("GET", health_url) as response:
                        consumed = 0
                        async for chunk in response.aiter_bytes():
                            consumed += len(chunk)
                            if consumed > MAX_RESPONSE_BYTES:
                                return ValidationOutcome(
                                    ConnectionValidationResult.ENDPOINT_UNREACHABLE,
                                    "response_too_large",
                                    True,
                                )
                        if response.status_code in (401, 403):
                            return ValidationOutcome(
                                ConnectionValidationResult.AUTHENTICATION_FAILED,
                                "authentication_failed",
                                True,
                            )
                        if 200 <= response.status_code < 300:
                            return ValidationOutcome(
                                ConnectionValidationResult.VALIDATED, None, True
                            )
                        if response.status_code >= 500 and attempt < settings.BTP_CONNECTION_MAX_RETRIES:
                            continue
                        return ValidationOutcome(
                            ConnectionValidationResult.ENDPOINT_UNREACHABLE,
                            "endpoint_http_error",
                            True,
                        )
                except httpx.TimeoutException:
                    if attempt >= settings.BTP_CONNECTION_MAX_RETRIES:
                        return ValidationOutcome(
                            ConnectionValidationResult.ENDPOINT_UNREACHABLE, "timeout", True
                        )
                except httpx.ConnectError:
                    return ValidationOutcome(
                        ConnectionValidationResult.TLS_FAILURE, "tls_or_transport_error", True
                    )
                except httpx.RequestError:
                    return ValidationOutcome(
                        ConnectionValidationResult.ENDPOINT_UNREACHABLE, "endpoint_unreachable", True
                    )
    except httpx.HTTPError:
        # Provider/library details and URLs are never returned to clients or logs.
        return ValidationOutcome(
            ConnectionValidationResult.ENDPOINT_UNREACHABLE, "endpoint_unreachable", True
        )
    return ValidationOutcome(ConnectionValidationResult.ENDPOINT_UNREACHABLE, "endpoint_unreachable", True)
