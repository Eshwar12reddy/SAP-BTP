"""Typed application settings shared by FastAPI, Alembic and the test suite.

Values are read from environment variables first, then from ``.env`` files
(repository root, then ``backend/``). Secrets are held in ``SecretStr`` and
validation errors never echo their input, so credentials cannot leak through
tracebacks or logs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

DRIVER = "postgresql+psycopg"
_ACCEPTED_SCHEMES = {"postgresql", "postgres", DRIVER}


class SettingsError(RuntimeError):
    """Raised when configuration is invalid. The message never contains secret values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    APP_NAME: str = "SAP BTP AI Operations Agent"
    APP_ENV: Literal["development", "test", "production"] = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = Field(default=8000, ge=1, le=65535)
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    API_V1_PREFIX: str = "/api/v1"

    # Either a full URL, or the individual POSTGRES_* parts below.
    DATABASE_URL: SecretStr | None = None
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = Field(default=5444, ge=1, le=65535)
    POSTGRES_DB: str | None = None
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: SecretStr | None = None

    DATABASE_POOL_SIZE: int = Field(default=5, ge=1, le=100)
    DATABASE_MAX_OVERFLOW: int = Field(default=10, ge=0, le=100)
    DATABASE_POOL_TIMEOUT: float = Field(default=10.0, gt=0, le=120)
    DATABASE_CONNECT_TIMEOUT: int = Field(default=5, ge=1, le=60)
    DATABASE_STATEMENT_TIMEOUT_MS: int = Field(default=15_000, ge=100, le=600_000)
    HEALTH_CHECK_TIMEOUT: float = Field(default=3.0, gt=0, le=30)

    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _reject_wildcard(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError("wildcard origins are not allowed; list explicit origins")
        return value

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        # Resolving the URL here makes a misconfigured database fail at startup.
        self.database_url  # noqa: B018
        if self.APP_ENV == "production":
            # Conversation endpoints have no authentication yet (see docs/foundation.md).
            raise ValueError(
                "APP_ENV=production is not supported until authentication is implemented; "
                "the conversation API is for trusted local development only"
            )
        return self

    @property
    def database_url(self) -> URL:
        """The SQLAlchemy URL for psycopg 3. Credentials stay inside the URL object."""
        if self.DATABASE_URL is not None:
            try:
                url = make_url(self.DATABASE_URL.get_secret_value())
            except (ArgumentError, ValueError):
                # The parser's own message can quote fragments of the password.
                raise ValueError(
                    "DATABASE_URL is not a valid database URL "
                    "(percent-encode special characters in the password)"
                ) from None
            if url.drivername not in _ACCEPTED_SCHEMES:
                raise ValueError(
                    f"DATABASE_URL must use the '{DRIVER}' driver (got '{url.drivername}')"
                )
            url = url.set(drivername=DRIVER)
        else:
            missing = [
                name
                for name in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    "set DATABASE_URL or all of POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD "
                    f"(missing: {', '.join(missing)})"
                )
            # URL.create escapes special characters in credentials correctly.
            url = URL.create(
                DRIVER,
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD.get_secret_value(),  # type: ignore[union-attr]
                host=self.POSTGRES_HOST,
                port=self.POSTGRES_PORT,
                database=self.POSTGRES_DB,
            )
        if not url.database:
            raise ValueError("the database URL must name a database")
        return url

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"


def load_settings(**overrides: object) -> Settings:
    """Build settings, converting validation failures into a sanitized ``SettingsError``."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'settings'}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise SettingsError(f"Invalid configuration: {problems}") from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()
