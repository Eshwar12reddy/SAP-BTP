"""Database readiness: connectivity, authentication and schema version."""

from __future__ import annotations

import asyncio
import enum
import logging

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import BACKEND_DIR

logger = logging.getLogger(__name__)

REQUIRED_TABLES = ("conversations", "messages")


class DatabaseStatus(enum.StrEnum):
    CONNECTED = "connected"
    UNAVAILABLE = "unavailable"
    SCHEMA_NOT_READY = "schema_not_ready"


def expected_schema_revision() -> str | None:
    """The Alembic head revision this code expects the database to be at."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


async def check_database(
    engine: AsyncEngine, expected_revision: str | None, timeout: float
) -> DatabaseStatus:
    try:
        return await asyncio.wait_for(_check(engine, expected_revision), timeout)
    except (TimeoutError, SQLAlchemyError, OSError) as exc:
        logger.warning(
            "Database readiness check failed",
            extra={"event": "readiness_failed", "error_type": type(exc).__name__},
        )
        return DatabaseStatus.UNAVAILABLE


async def _check(engine: AsyncEngine, expected_revision: str | None) -> DatabaseStatus:
    async with engine.connect() as connection:
        tables = (
            await connection.execute(
                text(
                    "SELECT to_regclass('conversations') IS NOT NULL, "
                    "to_regclass('messages') IS NOT NULL, "
                    "to_regclass('alembic_version') IS NOT NULL"
                )
            )
        ).one()
        if not (tables[0] and tables[1]):
            return DatabaseStatus.SCHEMA_NOT_READY
        if expected_revision is not None:
            if not tables[2]:
                return DatabaseStatus.SCHEMA_NOT_READY
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != expected_revision:
                return DatabaseStatus.SCHEMA_NOT_READY
    return DatabaseStatus.CONNECTED
