from __future__ import annotations

import pytest

from app.core.config import SettingsError, load_settings

SECRET = "s3cr3t-P@ss:w/rd%"
URL_SAFE_SECRET = "s3cr3t-password-value"
ENV_KEYS = (
    "DATABASE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "APP_ENV",
    "CORS_ORIGINS",
    "LANGGRAPH_CHECKPOINT_ENABLED",
    "LANGGRAPH_CHECKPOINT_DATABASE_URL",
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


JWT = "unit-test-jwt-secret-with-enough-entropy-0123"


def load(**values: object):
    values.setdefault("JWT_SECRET_KEY", JWT)
    return load_settings(_env_file=None, **values)


def test_loads_database_url_and_defaults() -> None:
    settings = load(DATABASE_URL="postgresql+psycopg://u:p@127.0.0.1:5444/btp_agent")
    url = settings.database_url
    assert (url.drivername, url.host, url.port, url.database) == (
        "postgresql+psycopg",
        "127.0.0.1",
        5444,
        "btp_agent",
    )
    assert settings.APP_PORT == 8000
    assert settings.APP_HOST == "127.0.0.1"
    assert settings.APP_ENV == "development"


def test_plain_postgresql_scheme_uses_psycopg() -> None:
    settings = load(DATABASE_URL="postgresql://u:p@localhost:5444/db")
    assert settings.database_url.drivername == "postgresql+psycopg"


def test_builds_url_from_parts_and_escapes_special_characters() -> None:
    settings = load(
        POSTGRES_HOST="127.0.0.1",
        POSTGRES_PORT=5444,
        POSTGRES_DB="btp_agent",
        POSTGRES_USER="btp_agent",
        POSTGRES_PASSWORD=SECRET,
    )
    url = settings.database_url
    assert url.password == SECRET
    rendered = url.render_as_string(hide_password=False)
    assert SECRET not in rendered  # percent-encoded in the rendered URL
    assert url.port == 5444


def test_missing_database_configuration_fails_clearly() -> None:
    with pytest.raises(SettingsError, match="POSTGRES_PASSWORD"):
        load(POSTGRES_DB="db", POSTGRES_USER="u")


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///local.db",
        f"mysql://u:{URL_SAFE_SECRET}@host/db",
        "not a url",
        # Unescaped special characters make the URL unparseable; no fragment may leak.
        f"postgresql://u:{SECRET}@host/db",
    ],
)
def test_rejects_non_postgres_urls_without_leaking_secrets(url: str) -> None:
    with pytest.raises(SettingsError) as info:
        load(DATABASE_URL=url)
    message = str(info.value)
    for fragment in (SECRET, URL_SAFE_SECRET, "rd%", "'w'"):
        assert fragment not in message
    assert "DATABASE_URL" in message


def test_invalid_values_fail_without_echoing_input() -> None:
    with pytest.raises(SettingsError) as info:
        load(DATABASE_URL=f"postgresql://u:{URL_SAFE_SECRET}@h/db", APP_PORT=70000, LOG_LEVEL="LOUD")
    message = str(info.value)
    assert "APP_PORT" in message and "LOG_LEVEL" in message
    assert URL_SAFE_SECRET not in message


def test_secrets_are_masked_in_repr() -> None:
    settings = load(DATABASE_URL=f"postgresql://u:{URL_SAFE_SECRET}@h/db")
    assert settings.database_url.password == URL_SAFE_SECRET
    assert URL_SAFE_SECRET not in repr(settings)
    assert URL_SAFE_SECRET not in repr(settings.database_url)


def test_cors_origins_from_comma_separated_string() -> None:
    settings = load(
        DATABASE_URL="postgresql://u:p@h/db",
        CORS_ORIGINS="http://localhost:5173, http://127.0.0.1:5173/",
    )
    assert settings.CORS_ORIGINS == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_wildcard_cors_rejected() -> None:
    with pytest.raises(SettingsError, match="CORS_ORIGINS"):
        load(DATABASE_URL="postgresql://u:p@h/db", CORS_ORIGINS="*")


def test_production_requires_secure_cookies() -> None:
    with pytest.raises(SettingsError, match="AUTH_COOKIE_SECURE"):
        load(DATABASE_URL="postgresql://u:p@h/db", APP_ENV="production", AUTH_COOKIE_SECURE=False)
    assert load(DATABASE_URL="postgresql://u:p@h/db", APP_ENV="production").APP_ENV == "production"


@pytest.mark.parametrize(
    "secret", [None, "short", "replace-with-at-least-32-random-characters", "change-this-" + "x" * 40]
)
def test_jwt_secret_required_and_not_a_placeholder(secret: str | None) -> None:
    with pytest.raises(SettingsError, match="JWT_SECRET_KEY") as info:
        load(DATABASE_URL="postgresql://u:p@h/db", JWT_SECRET_KEY=secret)
    if secret:
        assert secret not in str(info.value)


def test_refresh_idle_cannot_exceed_absolute_lifetime() -> None:
    with pytest.raises(SettingsError, match="REFRESH_IDLE_TTL_SECONDS"):
        load(
            DATABASE_URL="postgresql://u:p@h/db",
            REFRESH_IDLE_TTL_SECONDS=10_000,
            SESSION_ABSOLUTE_TTL_SECONDS=5_000,
        )


def test_langgraph_checkpointing_requires_a_dedicated_url_when_enabled() -> None:
    with pytest.raises(SettingsError, match="LANGGRAPH_CHECKPOINT_DATABASE_URL"):
        load(
            DATABASE_URL="postgresql://u:p@localhost:5444/btp_agent",
            LANGGRAPH_CHECKPOINT_ENABLED=True,
        )


def test_langgraph_checkpoint_url_is_separate_from_application_database() -> None:
    settings = load(
        DATABASE_URL="postgresql://u:p@localhost:5444/btp_agent",
        LANGGRAPH_CHECKPOINT_ENABLED=True,
        LANGGRAPH_CHECKPOINT_DATABASE_URL=(
            "postgresql://checkpoint:secret@localhost:5444/btp_agent_checkpoints_test"
        ),
    )
    assert settings.database_url.database == "btp_agent"
    assert settings.langgraph_checkpoint_database_url.database == "btp_agent_checkpoints_test"
