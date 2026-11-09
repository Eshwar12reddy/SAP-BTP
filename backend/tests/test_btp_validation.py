"""Unit tests for secret-safe BTP configuration and mocked live validation."""

from __future__ import annotations

import httpx

from app.integrations.sap_btp.credentials import CredentialReferenceError, CredentialResolver
from app.integrations.sap_btp.validation import validate_configuration, validate_live
from app.models.btp_connection import BTPConnection, ConnectionValidationResult


def connection() -> BTPConnection:
    return BTPConnection(
        display_name="Test connection",
        btp_account_id="00000000-0000-0000-0000-000000000001",
        btp_environment_id="00000000-0000-0000-0000-000000000002",
        api_endpoint="https://api.example.test",
        credential_reference="env://cf-test",
        authentication_method="bearer_token",
        enabled=True,
    )


def test_credential_resolver_requires_server_mapping_and_never_accepts_arbitrary_env(monkeypatch, settings):
    monkeypatch.setenv("BTP_CF_TEST_SECRET", "test-only-secret")
    settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}
    settings.BTP_VALIDATION_ALLOWED_HOSTS = ["api.example.test"]
    resolver = CredentialResolver(settings)

    assert resolver.is_registered("env://cf-test")
    assert not resolver.is_registered("env://BTP_CF_TEST_SECRET")
    assert resolver.resolve("env://cf-test").value == "test-only-secret"

    try:
        resolver.resolve("env://missing")
    except CredentialReferenceError as error:
        assert error.code == "credential_reference_unrecognized"
    else:
        raise AssertionError("unregistered references must be rejected")


def test_configuration_validation_is_local_and_secret_free(monkeypatch, settings):
    monkeypatch.setenv("BTP_CF_TEST_SECRET", "test-only-secret")
    settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}
    settings.BTP_VALIDATION_ALLOWED_HOSTS = ["api.example.test"]
    outcome = validate_configuration(
        connection(), "cloudfoundry", CredentialResolver(settings), settings
    )
    assert outcome.result is ConnectionValidationResult.VALID_CONFIGURATION
    assert "test-only-secret" not in repr(outcome)


async def test_live_validation_uses_mock_transport_and_bounded_result(monkeypatch, settings):
    monkeypatch.setenv("BTP_CF_TEST_SECRET", "test-only-secret")
    settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}
    settings.BTP_VALIDATION_ALLOWED_HOSTS = ["api.example.test"]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/info"
        assert request.headers["authorization"] == "Bearer test-only-secret"
        return httpx.Response(200, json={"ok": True}, request=request)

    result = await validate_live(
        connection(),
        "cloudfoundry",
        CredentialResolver(settings),
        settings,
        transport=httpx.MockTransport(handler),
    )
    assert result.result is ConnectionValidationResult.VALIDATED
    assert result.live_check_performed
    assert "test-only-secret" not in repr(result)


async def test_live_validation_maps_authentication_failure_without_provider_details(
    monkeypatch, settings
):
    monkeypatch.setenv("BTP_CF_TEST_SECRET", "test-only-secret")
    settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}
    settings.BTP_VALIDATION_ALLOWED_HOSTS = ["api.example.test"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret provider detail", request=request)

    result = await validate_live(
        connection(),
        "cloudfoundry",
        CredentialResolver(settings),
        settings,
        transport=httpx.MockTransport(handler),
    )
    assert result.result is ConnectionValidationResult.AUTHENTICATION_FAILED
    assert result.error_category == "authentication_failed"
    assert "secret provider detail" not in repr(result)
