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
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.core.config import BACKEND_DIR, Settings, get_settings, load_settings
from app.main import create_app

if sys.platform == "win32":
    # psycopg's async driver needs a selector loop on Windows.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


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
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


@pytest.fixture
def clean_database(test_database_url: URL) -> URL:
    assert test_database_url.database.endswith("_test")
    engine = create_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE messages, conversations"))
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
    }
    values.update(overrides)
    return load_settings(**values)


@pytest.fixture
def settings(clean_database: URL) -> Settings:
    return make_settings(clean_database)


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c
