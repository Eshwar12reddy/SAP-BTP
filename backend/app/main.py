"""FastAPI application factory and the ASGI ``app`` used by uvicorn."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.db.health import DatabaseStatus, check_database, expected_schema_revision
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _require_compatible_event_loop()
        url = settings.database_url
        log_fields = {
            "app_env": settings.APP_ENV,
            "database": url.database,
            "database_host": url.host,
            "database_port": url.port,
        }
        engine = create_engine(settings)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.expected_schema_revision = expected_schema_revision()
        logger.info("Application starting", extra={"event": "startup", **log_fields})

        # Report database state without blocking startup; /health/ready stays authoritative.
        status = await check_database(
            engine, app.state.expected_schema_revision, settings.HEALTH_CHECK_TIMEOUT
        )
        if status is DatabaseStatus.CONNECTED:
            logger.info("Database connected and schema up to date", extra={"event": "database_ready"})
        elif status is DatabaseStatus.SCHEMA_NOT_READY:
            logger.warning(
                "Database schema is missing or outdated; run 'python -m alembic upgrade head'",
                extra={"event": "database_schema_not_ready"},
            )
        else:
            logger.error("Database unreachable at startup", extra={"event": "database_unavailable"})

        try:
            yield
        finally:
            await engine.dispose()
            logger.info("Application stopped", extra={"event": "shutdown"})

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )

    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    # Added last so it is outermost: error responses also carry CORS headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return app


def _require_compatible_event_loop() -> None:
    """psycopg's async driver cannot run on Windows' ProactorEventLoop; fail with a clear fix."""
    if sys.platform == "win32" and isinstance(
        asyncio.get_running_loop(), getattr(asyncio, "ProactorEventLoop", ())
    ):
        raise RuntimeError(
            "psycopg async requires a SelectorEventLoop on Windows. Start the API with "
            "'python -m app', or pass '--loop asyncio:SelectorEventLoop' (or --reload) to uvicorn."
        )


app = create_app()
