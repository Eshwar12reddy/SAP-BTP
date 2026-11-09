"""Shared fixtures.

Tests run against an isolated database (``<db>_test`` on the same server, or
TEST_DATABASE_URL). It is created and migrated automatically; the application database
is never written to. Destructive cleanup refuses any database not named ``*_test``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher, Type
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.auth import passwords
from app.core.config import BACKEND_DIR, Settings, get_settings, load_settings
from app.main import create_app

if sys.platform == "win32":
    # psycopg's async driver needs a selector loop on Windows.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Production Argon2 parameters are asserted in test_passwords.py; everywhere else a cheap
# argon2id instance keeps the suite fast.
PRODUCTION_HASHER = passwords._hasher
passwords._hasher = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1, type=Type.ID)
passwords._DUMMY_HASH = passwords._hasher.hash("dummy-password-for-timing")

TEST_JWT_SECRET = "test-only-jwt-secret-0123456789-abcdefghijklmnop"
# Every table the tests write to; seeded roles/permissions are left alone.
_DATA_TABLES = (
    "knowledge_child_chunks, knowledge_parent_chunks, knowledge_index_generations, "
    "knowledge_source_versions, knowledge_sources, agent_memories, agent_approvals, messages, conversations, access_grants, session_refresh_tokens, sessions, "
    "audit_events, auth_login_attempts, btp_active_connections, btp_connections, architecture_proposals, sizing_results, "
    "btp_environments, btp_accounts, users"
)


def _test_database_url() -> URL:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        url = make_url(explicit).set(drivername="postgresql+psycopg")
    else:
        base = get_settings().database_url
        url = base.set(database=f"{base.database}_test")
    if not (url.database or "").endswith("_test"):
        raise RuntimeError("Refusing to run tests against a database not named '*_test'.")
    return url


def _libpq_dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _ensure_database(url: URL) -> None:
    """CREATE DATABASE for the test database if missing (connects via the 'postgres' db)."""
    admin = _libpq_dsn(url.set(database="postgres"))
    with psycopg.connect(admin, autocommit=True, connect_timeout=5) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))


@pytest.fixture(scope="session")
def test_database_url() -> URL:
    url = _test_database_url()
    _ensure_database(url)
    # The vector extension is installed only in this isolated *_test database. The main
    # database is never changed by test setup.
    with psycopg.connect(_libpq_dsn(url), autocommit=True, connect_timeout=5) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


@pytest.fixture
def clean_database(test_database_url: URL) -> URL:
    assert test_database_url.database.endswith("_test")
    engine = create_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {_DATA_TABLES}"))
    engine.dispose()
    return test_database_url


def make_settings(url: URL, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "APP_ENV": "test",
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "DATABASE_CONNECT_TIMEOUT": 2,
        "HEALTH_CHECK_TIMEOUT": 3,
        "LOG_LEVEL": "INFO",
        "JWT_SECRET_KEY": TEST_JWT_SECRET,
        "AUTH_COOKIE_SECURE": True,
    }
    values.update(overrides)
    return load_settings(**values)


@pytest.fixture
def settings(clean_database: URL, tmp_path) -> Settings:
    return make_settings(clean_database, KNOWLEDGE_STORAGE_DIR=tmp_path / "knowledge")


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        # https so that Secure cookies are stored and sent, as in a browser.
        async with AsyncClient(transport=transport, base_url="https://testserver") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c
