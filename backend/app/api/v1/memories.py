"""Authorized long-term agent memory endpoints.

These endpoints manage the caller's own explicit memories.  LangGraph checkpoints and
conversation messages are never exposed here.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.auth.authorization import Principal
from app.auth.dependencies import SessionDep, require_permission
from app.auth.permissions import Permission
from app.schemas.memory import MemoryCreate, MemoryPage, MemoryRead, MemoryUpdate
from app.services import memory_service

router = APIRouter(prefix="/memories", tags=["agent-memory"])
MemoryUser = Annotated[Principal, Depends(require_permission(Permission.CHAT_USE))]


@router.get("", response_model=MemoryPage)
async def list_saved_memories(
    session: SessionDep,
    principal: MemoryUser,
    limit: int = 20,
    offset: int = 0,
) -> MemoryPage:
    if not 1 <= limit <= 100 or not 0 <= offset <= 10_000:
        # Keep pagination bounded without exposing a validation echo.
        from app.core.exceptions import BadRequestError

        raise BadRequestError("Pagination is outside the allowed range.")
    rows, has_more = await memory_service.list_memories(
        session, principal, limit=limit, offset=offset
    )
    return MemoryPage(items=[MemoryRead.model_validate(row) for row in rows], has_more=has_more)


@router.post("", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def save_memory(
    payload: MemoryCreate,
    request: Request,
    session: SessionDep,
    principal: MemoryUser,
    response: Response,
) -> MemoryRead:
    memory, created = await memory_service.create_memory(
        session,
        principal,
        category=payload.category,
        content=payload.content,
        btp_account_id=payload.btp_account_id,
        btp_environment_id=payload.btp_environment_id,
        source_conversation_id=payload.source_conversation_id,
        source_investigation_reference=payload.source_investigation_reference,
        source_evidence=payload.source_evidence,
        expires_at=payload.expires_at,
        redactor=request.app.state.redactor,
        settings=request.app.state.settings,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return MemoryRead.model_validate(memory)


@router.get("/{memory_id}", response_model=MemoryRead)
async def read_saved_memory(
    memory_id: uuid.UUID, session: SessionDep, principal: MemoryUser
) -> MemoryRead:
    memory = await memory_service.get_memory(session, principal, memory_id)
    return MemoryRead.model_validate(memory)


@router.patch("/{memory_id}", response_model=MemoryRead)
async def update_saved_memory(
    memory_id: uuid.UUID,
    payload: MemoryUpdate,
    request: Request,
    session: SessionDep,
    principal: MemoryUser,
) -> MemoryRead:
    memory = await memory_service.update_memory(
        session,
        principal,
        memory_id,
        revision=payload.revision,
        content=payload.content,
        redactor=request.app.state.redactor,
    )
    return MemoryRead.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_saved_memory(
    memory_id: uuid.UUID, session: SessionDep, principal: MemoryUser
) -> None:
    await memory_service.delete_memory(session, principal, memory_id)
