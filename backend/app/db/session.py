"""Async engine, session factory and the request-scoped session dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

APPLICATION_NAME = "btp-ai-operations-backend"


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_timeout=settings.DATABASE_POOL_TIMEOUT,
        pool_pre_ping=True,
        pool_recycle=1800,
        # Keep bound parameters (e.g. message contents) out of exception messages.
        hide_parameters=True,
        connect_args={
            "connect_timeout": settings.DATABASE_CONNECT_TIMEOUT,
            "application_name": APPLICATION_NAME,
            "options": f"-c statement_timeout={settings.DATABASE_STATEMENT_TIMEOUT_MS}",
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False lets committed objects be serialized without lazy reloads.
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Services commit explicitly; anything uncommitted is rolled back."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
