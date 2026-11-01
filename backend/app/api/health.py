from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db.health import DatabaseStatus, check_database

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    """The process is running and serving requests. Does not touch the database."""
    return {"status": "alive"}


@router.get("/ready", responses={503: {"description": "Database or schema not ready"}})
async def ready(request: Request) -> JSONResponse:
    """The database is reachable, credentials work and the schema is migrated."""
    state = request.app.state
    status = await check_database(
        state.engine, state.expected_schema_revision, state.settings.HEALTH_CHECK_TIMEOUT
    )
    if status is DatabaseStatus.CONNECTED:
        return JSONResponse({"status": "ready", "database": status.value})
    return JSONResponse({"status": "not_ready", "database": status.value}, status_code=503)
