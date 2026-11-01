"""Conversation endpoints.

No authentication exists yet, so these endpoints are for trusted local development only
(the backend binds to 127.0.0.1 and refuses APP_ENV=production). Ownership checks will be
added here once the auth phase lands.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import error_body
from app.db.session import get_session
from app.schemas.conversation import ConversationCreate, ConversationPage, ConversationRead
from app.schemas.message import MessageCreate, MessagePage, MessageRead
from app.services import conversation_service

router = APIRouter(prefix="/conversations", tags=["conversations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_ERROR_SCHEMA = {
    "content": {
        "application/json": {"example": error_body("not_found", "Conversation not found.")}
    }
}
_NOT_FOUND = {404: {"description": "Conversation not found", **_ERROR_SCHEMA}}
_UNAVAILABLE = {503: {"description": "Database temporarily unavailable", **_ERROR_SCHEMA}}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ConversationRead,
    responses=_UNAVAILABLE,
)
async def create_conversation(
    session: SessionDep, payload: ConversationCreate | None = None
) -> ConversationRead:
    conversation = await conversation_service.create_conversation(
        session, payload.title if payload else None
    )
    return ConversationRead.model_validate(conversation)


@router.get("", response_model=ConversationPage, responses=_UNAVAILABLE)
async def list_conversations(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ConversationPage:
    """Conversations ordered by most recent activity."""
    conversations, has_more = await conversation_service.list_conversations(
        session, limit=limit, offset=offset
    )
    return ConversationPage(
        items=[ConversationRead.model_validate(c) for c in conversations], has_more=has_more
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationRead,
    responses={**_NOT_FOUND, **_UNAVAILABLE},
)
async def get_conversation(conversation_id: uuid.UUID, session: SessionDep) -> ConversationRead:
    conversation = await conversation_service.get_conversation(session, conversation_id)
    return ConversationRead.model_validate(conversation)


@router.get(
    "/{conversation_id}/messages",
    response_model=MessagePage,
    responses={**_NOT_FOUND, **_UNAVAILABLE},
)
async def list_messages(
    conversation_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    before: Annotated[
        uuid.UUID | None, Query(description="Return messages older than this message id.")
    ] = None,
) -> MessagePage:
    """The newest ``limit`` messages, oldest first. Page backwards with ``before``."""
    messages, has_more = await conversation_service.list_messages(
        session, conversation_id, limit=limit, before=before
    )
    return MessagePage(
        items=[MessageRead.model_validate(m) for m in messages], has_more=has_more
    )


@router.post(
    "/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageRead,
    responses={
        200: {"description": "Identical retry; the previously saved message is returned"},
        409: {"description": "client_message_id reused with different content", **_ERROR_SCHEMA},
        **_NOT_FOUND,
        **_UNAVAILABLE,
    },
)
async def create_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    session: SessionDep,
    response: Response,
) -> MessageRead:
    """Save a user message. Returns only after the transaction has committed."""
    saved = await conversation_service.add_user_message(
        session,
        conversation_id,
        content=payload.content,
        client_message_id=payload.client_message_id,
    )
    if not saved.created:
        response.status_code = status.HTTP_200_OK
    return MessageRead.model_validate(saved.message)
