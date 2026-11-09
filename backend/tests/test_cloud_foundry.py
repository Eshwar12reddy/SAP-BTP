"""Mocked Cloud Foundry transport tests; no provider network calls are made."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.integrations.sap_btp.cloud_foundry import (
    CloudFoundryProviderError,
    CloudFoundryReadClient,
    scope_mapping,
)
from app.integrations.sap_btp.credentials import CredentialResolver
from app.services.btp_connection_service import BTPConnectionContext


def make_context(settings) -> BTPConnectionContext:
    return BTPConnectionContext(
        connection_id=uuid.uuid4(),
        runtime_type="cloudfoundry",
        btp_account_id=uuid.uuid4(),
        btp_environment_id=uuid.uuid4(),
        api_endpoint="https://api.example.test",
        credential_reference="env://cf-test",
        authentication_method="bearer_token",
        credential_resolver=CredentialResolver(settings),
        timeout_seconds=1,
        max_retries=1,
    )


@pytest.fixture
def cf_settings(monkeypatch, settings):
    monkeypatch.setenv("BTP_CF_TEST_SECRET", "test-only-secret")
    settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}
    settings.BTP_VALIDATION_ALLOWED_HOSTS = ["api.example.test"]
    settings.BTP_CLOUD_FOUNDRY_RETRY_DELAY_CAP_SECONDS = 0
    return settings


async def test_pagination_is_bounded_and_same_origin_only(cf_settings):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.params["page"] == "1":
            return httpx.Response(
                200,
                json={
                    "resources": [{"guid": "org-1"}],
                    "pagination": {
                        "next": {
                            "href": "https://api.example.test/v3/organizations?page=2&per_page=1"
                        }
                    },
                },
                request=request,
            )
        return httpx.Response(
            200, json={"resources": [], "pagination": {"next": None}}, request=request
        )

    context = make_context(cf_settings)
    async with CloudFoundryReadClient(
        context, cf_settings, transport=httpx.MockTransport(handler)
    ) as client:
        items, more = await client.list_resources(
            "organizations", parser=lambda resource: resource["guid"]
        )
    assert items == ["org-1"]
    assert more is False
    assert len(calls) == 2

    def cross_origin(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "resources": [],
                "pagination": {
                    "next": {"href": "https://evil.example/v3/organizations?page=2"}
                },
            },
            request=request,
        )

    async with CloudFoundryReadClient(
        context, cf_settings, transport=httpx.MockTransport(cross_origin)
    ) as client:
        with pytest.raises(CloudFoundryProviderError, match="pagination_origin_rejected"):
            await client.list_resources("organizations", parser=lambda resource: resource)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "authentication_expired"),
        (403, "provider_permission_denied"),
        (404, "provider_not_found"),
        (500, "provider_unavailable"),
    ],
)
async def test_provider_statuses_are_normalized_without_body_leaks(cf_settings, status, code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="secret provider response", request=request)

    cf_settings.BTP_CONNECTION_MAX_RETRIES = 0
    async with CloudFoundryReadClient(
        make_context(cf_settings), cf_settings, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(CloudFoundryProviderError) as error:
            await client.get("info", parser=lambda value: value)
    assert error.value.code == code
    assert "secret provider response" not in str(error.value)


async def test_rate_limit_retries_once_and_timeout_is_typed(cf_settings):
    attempts = 0

    def rate_limited(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async with CloudFoundryReadClient(
        make_context(cf_settings), cf_settings, transport=httpx.MockTransport(rate_limited)
    ) as client:
        assert await client.get("info", parser=lambda value: value) == {"ok": True}
    assert attempts == 2

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    cf_settings.BTP_CONNECTION_MAX_RETRIES = 0
    async with CloudFoundryReadClient(
        make_context(cf_settings), cf_settings, transport=httpx.MockTransport(timeout)
    ) as client:
        with pytest.raises(CloudFoundryProviderError, match="provider_timeout"):
            await client.get("info", parser=lambda value: value)


def test_scope_mapping_is_explicit_and_fail_closed(cf_settings):
    environment_id = str(uuid.uuid4())
    cf_settings.BTP_CLOUD_FOUNDRY_SCOPE_MAP = {
        environment_id: {"organization_ids": ["org-1"], "space_ids": ["space-1"]}
    }
    mapping = scope_mapping(cf_settings, environment_id)
    assert mapping.allows_organization("org-1")
    assert mapping.allows_space("space-1")
    with pytest.raises(CloudFoundryProviderError, match="scope_mapping_missing"):
        scope_mapping(cf_settings, str(uuid.uuid4()))
