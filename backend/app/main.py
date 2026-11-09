"""FastAPI application factory and the ASGI ``app`` used by uvicorn."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.checkpoint import open_checkpointer
from app.agents.orchestrator import AgentOrchestrator
from app.api import health
from app.api.v1.router import api_router
from app.auth.cookies import CSRF_HEADER
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    UploadSizeLimitMiddleware,
)
from app.db.health import DatabaseStatus, check_database, expected_schema_revision
from app.db.session import create_engine, create_session_factory
from app.integrations.deepseek_client import DeepSeekClient
from app.integrations.openai_embeddings import OpenAIEmbeddingsClient
from app.integrations.sap_btp.provider import UnavailableBTPProvider
from app.knowledge.storage import KnowledgeStorage
from app.safety.redaction import Redactor
from app.services.architecture_advisor_service import ArchitectureAdvisorService
from app.services.cloud_foundry_service import AuthorizedCloudFoundryService
from app.services.diagnosis_service import CloudFoundryDiagnosisService
from app.services.health_diagnosis_service import HealthDiagnosisService
from app.services.infrastructure_plan_service import InfrastructurePlanService
from app.services.service_recommendation_service import ServiceRecommendationService
from app.services.sizing_service import SizingService
from app.tools.btp import register_btp_tools
from app.tools.infrastructure import register_infrastructure_tools
from app.tools.knowledge import register_knowledge_tool
from app.tools.registry import ToolRegistry

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # One redactor for logs, tool output and prompts: masks configured secrets.
    redactor = Redactor.from_settings(settings)
    configure_logging(settings.LOG_LEVEL, redact=redactor.redact_text)

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
        # Keep the legacy provider unavailable; Phase 12 injects its read-only
        # Cloud Foundry service separately so older adapter tests remain explicit.
        app.state.btp_provider = UnavailableBTPProvider()
        app.state.btp_read_service = AuthorizedCloudFoundryService(settings)
        app.state.btp_diagnosis_service = CloudFoundryDiagnosisService(settings)
        app.state.btp_health_service = HealthDiagnosisService(settings)
        app.state.service_recommendation_service = ServiceRecommendationService(
            live_service=app.state.btp_read_service
        )
        app.state.architecture_advisor_service = ArchitectureAdvisorService(
            recommendation_service=app.state.service_recommendation_service,
            settings=settings,
        )
        app.state.sizing_service = SizingService(
            settings=settings,
            architecture_service=app.state.architecture_advisor_service,
            cloud_foundry_service=app.state.btp_read_service,
        )
        app.state.infrastructure_plan_service = InfrastructurePlanService(settings=settings)
        # Private document storage; directories are created on first upload.
        app.state.knowledge_storage = KnowledgeStorage(settings.KNOWLEDGE_STORAGE_DIR)
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

        # Shared by every AI caller. No DeepSeek request is made at startup or by health checks.
        deepseek_client = DeepSeekClient(settings)
        app.state.deepseek_client = deepseek_client
        logger.info(
            "DeepSeek client created",
            extra={
                "event": "deepseek_client_ready",
                "model": settings.DEEPSEEK_MODEL,
                "configured": deepseek_client.configured,
            },
        )
        # Reusable embeddings-only client; it never calls OpenAI unless explicitly enabled.
        embeddings_client = OpenAIEmbeddingsClient(settings)
        app.state.openai_embeddings = embeddings_client
        # Every tool the agent may run is registered here, explicitly.
        tool_registry = ToolRegistry(redactor)
        register_btp_tools(tool_registry)
        register_knowledge_tool(tool_registry)
        register_infrastructure_tools(tool_registry)
        tool_registry.freeze()
        app.state.tool_registry = tool_registry
        app.state.redactor = redactor
        async with open_checkpointer(settings) as checkpointer:
            app.state.checkpointer = checkpointer
            app.state.agent = AgentOrchestrator(
                deepseek_client,
                tool_registry,
                redactor,
                checkpointer=checkpointer,
                settings=settings,
            )
            try:
                yield
            finally:
                try:
                    await embeddings_client.close()
                finally:
                    try:
                        await deepseek_client.close()
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
    # Innermost: bounds upload bodies before the multipart parser spools them to disk.
    app.add_middleware(
        UploadSizeLimitMiddleware,
        path_prefix=f"{settings.API_V1_PREFIX}/knowledge",
        # The aggregate request body is bounded before multipart parsing. Pydantic then
        # bounds the allowed text fields after strict form parsing.
        max_body_bytes=settings.KNOWLEDGE_MAX_UPLOAD_BYTES + 512 * 1024,
    )
    app.add_middleware(RequestContextMiddleware)
    # Added last so it is outermost: error responses also carry CORS headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        # Needed only if the SPA is served from another origin (refresh cookie).
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", CSRF_HEADER, REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
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
