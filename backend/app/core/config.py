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
from urllib.parse import urlsplit

from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

DRIVER = "postgresql+psycopg"
_ACCEPTED_SCHEMES = {"postgresql", "postgres", DRIVER}
# The API key is sent to this host only.
DEEPSEEK_HOST = "api.deepseek.com"


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

    # Authentication. JWT_SECRET_KEY is required: there is deliberately no default.
    JWT_SECRET_KEY: SecretStr | None = None
    JWT_ISSUER: str = "btp-ai-operations-agent"
    JWT_AUDIENCE: str = "btp-ai-operations-api"
    ACCESS_TOKEN_TTL_SECONDS: int = Field(default=15 * 60, ge=60, le=60 * 60)
    REFRESH_IDLE_TTL_SECONDS: int = Field(default=7 * 24 * 3600, ge=300)
    SESSION_ABSOLUTE_TTL_SECONDS: int = Field(default=30 * 24 * 3600, ge=300)
    # A rotated refresh token presented again within this window is treated as a
    # concurrent request (409, nothing issued) rather than theft.
    REFRESH_REUSE_GRACE_SECONDS: int = Field(default=10, ge=0, le=60)
    AUTH_COOKIE_SECURE: bool = True
    LOGIN_WINDOW_SECONDS: int = Field(default=15 * 60, ge=60)
    LOGIN_MAX_FAILURES_PER_EMAIL: int = Field(default=5, ge=1)
    LOGIN_MAX_FAILURES_PER_IP: int = Field(default=20, ge=1)

    # DeepSeek, the only hosted AI provider. The key is optional so the app starts without it;
    # AI requests then fail with deepseek_not_configured. Limits apply per process.
    DEEPSEEK_API_KEY: SecretStr | None = None
    DEEPSEEK_BASE_URL: str = f"https://{DEEPSEEK_HOST}"
    DEEPSEEK_MODEL: str = "deepseek-flash"
    # Thinking tokens count toward max_tokens, and temperature is ignored while thinking.
    DEEPSEEK_THINKING_ENABLED: bool = False
    DEEPSEEK_CONNECT_TIMEOUT: float = Field(default=5.0, gt=0, le=60)
    DEEPSEEK_READ_TIMEOUT: float = Field(default=45.0, gt=0, le=600)
    DEEPSEEK_WRITE_TIMEOUT: float = Field(default=10.0, gt=0, le=60)
    # Upper bound for one chat() call: queueing, every attempt and every backoff.
    DEEPSEEK_REQUEST_DEADLINE: float = Field(default=90.0, gt=0, le=900)
    DEEPSEEK_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
    DEEPSEEK_MAX_CONCURRENT_REQUESTS: int = Field(default=4, ge=1, le=64)
    DEEPSEEK_REQUESTS_PER_MINUTE: int = Field(default=30, ge=1, le=10_000)
    DEEPSEEK_MAX_OUTPUT_TOKENS: int = Field(default=2048, ge=1, le=393_216)
    DEEPSEEK_MAX_INPUT_MESSAGES: int = Field(default=50, ge=1, le=1_000)
    DEEPSEEK_MAX_INPUT_CHARS: int = Field(default=100_000, ge=1, le=4_000_000)

    # OpenAI is used only for embeddings, never chat/reasoning. Off by default because
    # enabling it sends authorized document child chunks and user queries to OpenAI.
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_DIMENSIONS: int = Field(default=1536, ge=1536, le=1536)
    OPENAI_EMBEDDINGS_ENABLED: bool = False
    OPENAI_EMBEDDING_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0, le=120)
    OPENAI_EMBEDDING_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
    OPENAI_EMBEDDING_BATCH_SIZE: int = Field(default=64, ge=1, le=256)
    OPENAI_EMBEDDING_MAX_CONCURRENT_REQUESTS: int = Field(default=2, ge=1, le=8)

    # Knowledge base documents: a private directory that nothing serves statically.
    KNOWLEDGE_STORAGE_DIR: Path = BACKEND_DIR / "var" / "knowledge"
    KNOWLEDGE_MAX_UPLOAD_BYTES: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    KNOWLEDGE_PDF_MAX_PAGES: int = Field(default=1000, ge=1, le=10_000)

    # Lexical indexing always runs. OpenAI embeddings are opt-in; reranking stays off until
    # a separate cross-encoder model and runtime budget are approved.
    KNOWLEDGE_INDEX_MAX_CHARACTERS: int = Field(default=5_000_000, ge=10_000)
    KNOWLEDGE_INDEX_MAX_CHUNKS: int = Field(default=5_000, ge=10, le=100_000)
    KNOWLEDGE_INDEX_TIMEOUT_SECONDS: float = Field(default=120.0, gt=1, le=900)
    KNOWLEDGE_SEARCH_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0, le=60)
    KNOWLEDGE_PARENT_TOKENS: int = Field(default=1000, ge=800, le=1200)
    KNOWLEDGE_CHILD_TOKENS: int = Field(default=250, ge=180, le=300)
    KNOWLEDGE_CHILD_OVERLAP_TOKENS: int = Field(default=50, ge=40, le=50)
    KNOWLEDGE_LEXICAL_TOP_K: int = Field(default=20, ge=1, le=100)
    KNOWLEDGE_VECTOR_TOP_K: int = Field(default=20, ge=1, le=100)
    KNOWLEDGE_FUSION_CONSTANT: int = Field(default=60, ge=1, le=500)
    KNOWLEDGE_RERANK_CANDIDATES: int = Field(default=30, ge=1, le=100)
    KNOWLEDGE_FINAL_CHILD_TOP_K: int = Field(default=6, ge=1, le=8)
    KNOWLEDGE_EXPANDED_PARENT_TOP_K: int = Field(default=4, ge=1, le=5)
    KNOWLEDGE_EVIDENCE_BUDGET_TOKENS: int = Field(default=6000, ge=1000, le=8000)
    KNOWLEDGE_INDEX_CONFIG_VERSION: str = "parent-child-v1"
    KNOWLEDGE_TOKENIZER_ENCODING: str = Field(default="cl100k_base", pattern="^cl100k_base$")

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

    @field_validator("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "OPENAI_API_KEY", mode="before")
    @classmethod
    def _blank_means_default(cls, value: object, info: ValidationInfo) -> object:
        # .env files commonly contain "DEEPSEEK_API_KEY=" with no value.
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @field_validator("KNOWLEDGE_STORAGE_DIR")
    @classmethod
    def _private_storage_dir(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        # The Vite frontend serves its directory tree; documents must never land inside it.
        if path.is_relative_to(REPO_ROOT / "frontend"):
            raise ValueError("KNOWLEDGE_STORAGE_DIR must not be inside the frontend directory")
        return path

    @field_validator("DEEPSEEK_BASE_URL")
    @classmethod
    def _approved_deepseek_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.hostname != DEEPSEEK_HOST
            or url.port not in (None, 443)
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(f"DEEPSEEK_BASE_URL must be an https://{DEEPSEEK_HOST} URL")
        return value

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        # Resolving the URL here makes a misconfigured database fail at startup.
        self.database_url  # noqa: B018
        secret = self.JWT_SECRET_KEY.get_secret_value() if self.JWT_SECRET_KEY else ""
        if len(secret) < 32 or secret.lower().startswith(("replace-with", "change-this")):
            raise ValueError(
                "JWT_SECRET_KEY must be set to at least 32 random characters "
                "(python -c \"import secrets; print(secrets.token_urlsafe(48))\")"
            )
        if self.REFRESH_IDLE_TTL_SECONDS > self.SESSION_ABSOLUTE_TTL_SECONDS:
            raise ValueError("REFRESH_IDLE_TTL_SECONDS must not exceed SESSION_ABSOLUTE_TTL_SECONDS")
        if self.APP_ENV == "production" and not self.AUTH_COOKIE_SECURE:
            raise ValueError("AUTH_COOKIE_SECURE must be true in production")
        if self.OPENAI_EMBEDDING_MODEL != "text-embedding-3-small":
            raise ValueError("OPENAI_EMBEDDING_MODEL must be text-embedding-3-small")
        if self.OPENAI_EMBEDDING_DIMENSIONS != 1536:
            raise ValueError("OPENAI_EMBEDDING_DIMENSIONS must be 1536")
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
