"""Conversation endpoints.

Callers need ``chat:use``. Every conversation belongs to the authenticated user (never a
client-supplied id); other users' and legacy conversations answer 404. A conversation's
optional BTP context is re-authorized against the caller's live grants on every access.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, or_, select

from app.agents.contracts import AgentContext, AgentRequest
from app.auth.authorization import Principal, coverage_for, knowledge_coverage_for
from app.auth.dependencies import SessionDep, deny, require_permission
from app.auth.permissions import Permission
from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError, error_body
from app.integrations.deepseek_client import DeepSeekTimeoutError
from app.models import (
    Conversation,
    KnowledgeScope,
    KnowledgeSource,
    KnowledgeSourceVersion,
    Message,
    SourceStatus,
    ValidationStatus,
)
from app.models.message import ReplyStatus
from app.schemas.agent import AgentReplyMetadata, AgentReplyRead, ReplyStatusRead
from app.schemas.conversation import ConversationCreate, ConversationPage, ConversationRead
from app.schemas.message import MessageCreate, MessagePage, MessageRead
from app.services import conversation_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])

# Suggested wait before asking again whether a reply in progress has finished.
REPLY_POLL_SECONDS = 3

ChatUser = Annotated[Principal, Depends(require_permission(Permission.CHAT_USE))]

_ERROR_SCHEMA = {
    "content": {
        "application/json": {"example": error_body("not_found", "Conversation not found.")}
    }
}
_AUTH = {
    401: {"description": "Not authenticated", **_ERROR_SCHEMA},
    403: {"description": "Missing permission or BTP scope", **_ERROR_SCHEMA},
}
_NOT_FOUND = {404: {"description": "Conversation not found", **_ERROR_SCHEMA}}
_UNAVAILABLE = {503: {"description": "Database temporarily unavailable", **_ERROR_SCHEMA}}


async def _authorized_conversation(
    request: Request, session: SessionDep, principal: Principal, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await conversation_service.get_conversation(
        session, conversation_id, owner_id=principal.user_id
    )
    coverage = await coverage_for(session, principal, Permission.BTP_READ)
    if not conversation_service.context_authorized(conversation, coverage):
        await deny(
            request,
            principal,
            reason="conversation_context_not_authorized",
            permission=Permission.BTP_READ,
            error=PermissionDeniedError(
                "You no longer have access to this conversation's BTP context.",
                code="btp_scope_revoked",
            ),
            btp_account_id=conversation.btp_account_id,
            btp_environment_id=conversation.btp_environment_id,
        )
    return conversation


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ConversationRead,
    responses={**_AUTH, **_UNAVAILABLE},
)
async def create_conversation(
    request: Request,
    session: SessionDep,
    principal: ChatUser,
    payload: ConversationCreate | None = None,
) -> ConversationRead:
    payload = payload or ConversationCreate()
    if payload.btp_account_id or payload.btp_environment_id:
        coverage = await coverage_for(session, principal, Permission.BTP_READ)
        allowed = (
            coverage.covers_account(payload.btp_account_id)
            if payload.btp_account_id
            else coverage.covers_environment(payload.btp_environment_id)  # type: ignore[arg-type]
        )
        if not allowed:
            await deny(
                request,
                principal,
                reason="conversation_context_not_authorized",
                permission=Permission.BTP_READ,
                error=NotFoundError("BTP scope not found."),
                btp_account_id=payload.btp_account_id,
                btp_environment_id=payload.btp_environment_id,
            )
    conversation = await conversation_service.create_conversation(
        session,
        owner_id=principal.user_id,
        title=payload.title,
        btp_account_id=payload.btp_account_id,
        btp_environment_id=payload.btp_environment_id,
    )
    return ConversationRead.model_validate(conversation)


@router.get("", response_model=ConversationPage, responses={**_AUTH, **_UNAVAILABLE})
async def list_conversations(
    session: SessionDep,
    principal: ChatUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ConversationPage:
    """The caller's conversations, most recent activity first."""
    coverage = await coverage_for(session, principal, Permission.BTP_READ)
    conversations, has_more = await conversation_service.list_conversations(
        session, owner_id=principal.user_id, coverage=coverage, limit=limit, offset=offset
    )
    return ConversationPage(
        items=[ConversationRead.model_validate(c) for c in conversations], has_more=has_more
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationRead,
    responses={**_AUTH, **_NOT_FOUND, **_UNAVAILABLE},
)
async def get_conversation(
    conversation_id: uuid.UUID, request: Request, session: SessionDep, principal: ChatUser
) -> ConversationRead:
    conversation = await _authorized_conversation(request, session, principal, conversation_id)
    return ConversationRead.model_validate(conversation)


@router.get(
    "/{conversation_id}/messages",
    response_model=MessagePage,
    responses={**_AUTH, **_NOT_FOUND, **_UNAVAILABLE},
)
async def list_messages(
    conversation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: ChatUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    before: Annotated[
        uuid.UUID | None, Query(description="Return messages older than this message id.")
    ] = None,
) -> MessagePage:
    """The newest ``limit`` messages, oldest first. Page backwards with ``before``."""
    conversation = await _authorized_conversation(request, session, principal, conversation_id)
    messages, has_more = await conversation_service.list_messages(
        session, conversation, limit=limit, before=before
    )
    _, stale_before = _reply_limits(request.app.state.settings)
    return MessagePage(
        items=[await _authorized_message_read(session, principal, m, stale_before) for m in messages],
        has_more=has_more,
    )


@router.post(
    "/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageRead,
    responses={
        200: {"description": "Identical retry; the previously saved message is returned"},
        409: {"description": "client_message_id reused with different content", **_ERROR_SCHEMA},
        **_AUTH,
        **_NOT_FOUND,
        **_UNAVAILABLE,
    },
)
async def create_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    request: Request,
    session: SessionDep,
    principal: ChatUser,
    response: Response,
) -> MessageRead:
    """Save a user message. Returns only after the transaction has committed."""
    conversation = await _authorized_conversation(request, session, principal, conversation_id)
    saved = await conversation_service.add_user_message(
        session,
        conversation,
        content=payload.content,
        client_message_id=payload.client_message_id,
    )
    if not saved.created:
        response.status_code = status.HTTP_200_OK
    return MessageRead.model_validate(saved.message)


@router.post(
    "/{conversation_id}/messages/{message_id}/reply",
    status_code=status.HTTP_201_CREATED,
    response_model=AgentReplyRead,
    responses={
        200: {"description": "A reply already exists; it is returned without a new AI call"},
        409: {
            "description": "reply_in_progress: another request is generating this reply. "
            "Poll GET .../reply (Retry-After) instead of retrying.",
            **_ERROR_SCHEMA,
        },
        429: {"description": "AI service rate limit (deepseek_rate_limited)", **_ERROR_SCHEMA},
        502: {"description": "Invalid AI response (deepseek_invalid_response)", **_ERROR_SCHEMA},
        503: {"description": "AI service or database unavailable", **_ERROR_SCHEMA},
        504: {"description": "AI service timed out (deepseek_timeout)", **_ERROR_SCHEMA},
        **_AUTH,
        **_NOT_FOUND,
    },
)
async def create_reply(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: ChatUser,
    response: Response,
) -> AgentReplyRead:
    """Answer a saved user message with the AI agent.

    At most one reply is stored per user message: a repeated request returns it (200)
    without calling the AI again. Starting a reply is claimed in the database, so while
    one request generates it, others (any worker) get 409 ``reply_in_progress`` and should
    poll ``GET .../reply``. If the agent fails, nothing is stored, the attempt is recorded
    as failed, and the error is returned as is.
    """
    conversation = await _authorized_conversation(request, session, principal, conversation_id)
    message = await conversation_service.get_user_message(session, conversation, message_id)
    existing = await conversation_service.find_reply(session, message)
    if existing is not None:
        await session.commit()
        response.status_code = status.HTTP_200_OK
        visible = await _knowledge_refs_visible(session, principal, existing)
        return _reply_read(existing, hide_knowledge=not visible)

    run_limit, stale_before = _reply_limits(request.app.state.settings)
    # Commits, so no transaction stays open while the AI works.
    if not await conversation_service.claim_reply(session, message, stale_before=stale_before):
        raise ConflictError(
            "A reply to this message is still being generated.",
            code="reply_in_progress",
            headers={"Retry-After": str(REPLY_POLL_SECONDS)},
        )

    context = AgentContext(
        principal=principal,
        session=session,
        btp_provider=request.app.state.btp_provider,
        session_factory=request.app.state.session_factory,
        settings=request.app.state.settings,
        embedding_client=request.app.state.openai_embeddings,
        redactor=request.app.state.redactor,
        btp_account_id=conversation.btp_account_id,
        btp_environment_id=conversation.btp_environment_id,
    )
    try:
        # Bounds the claim: a "generating" state older than this is abandoned, not running.
        async with asyncio.timeout(run_limit):
            result = await request.app.state.agent.handle(
                AgentRequest(message=message.content, conversation_id=conversation.id), context
            )
        metadata = AgentReplyMetadata.from_response(result)
        saved = await conversation_service.add_assistant_reply(
            session,
            message,
            content=result.content,
            agent_metadata=metadata.model_dump(mode="json"),
        )
    except TimeoutError:
        await _record_failure(session, message.id)
        raise DeepSeekTimeoutError(reason="reply_time_limit") from None
    except Exception:
        await _record_failure(session, message.id)
        raise
    if not saved.created:
        response.status_code = status.HTTP_200_OK
    visible = await _knowledge_refs_visible(session, principal, saved.message)
    return _reply_read(saved.message, hide_knowledge=not visible)


@router.get(
    "/{conversation_id}/messages/{message_id}/reply",
    response_model=ReplyStatusRead,
    responses={**_AUTH, **_NOT_FOUND, **_UNAVAILABLE},
)
async def get_reply_status(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: ChatUser,
) -> ReplyStatusRead:
    """Whether the reply to a user message is done, still generating, or failed.

    Read-only: it never starts or repeats an AI call. ``completed`` includes the reply.
    """
    conversation = await _authorized_conversation(request, session, principal, conversation_id)
    message = await conversation_service.get_user_message(session, conversation, message_id)
    reply = await conversation_service.find_reply(session, message)
    if reply is not None:
        visible = await _knowledge_refs_visible(session, principal, reply)
        return ReplyStatusRead(status="completed", reply=_reply_read(reply, hide_knowledge=not visible))
    _, stale_before = _reply_limits(request.app.state.settings)
    current = conversation_service.effective_reply_status(message, stale_before=stale_before)
    if current is None:
        return ReplyStatusRead(status="not_requested")
    # "completed" without a stored reply cannot happen (both are written together).
    return ReplyStatusRead(
        status="generating" if current is ReplyStatus.GENERATING else "failed"
    )


def _reply_limits(settings: Settings) -> tuple[float, datetime]:
    """Maximum run time of one reply (two bounded model calls plus tools), and the time
    before which a "generating" claim must have been abandoned."""
    run_limit = 2 * settings.DEEPSEEK_REQUEST_DEADLINE + 60
    return run_limit, datetime.now(UTC) - timedelta(seconds=run_limit + 30)


async def _record_failure(session: SessionDep, message_id: uuid.UUID) -> None:
    try:
        await conversation_service.mark_reply_failed(session, message_id)
    except Exception:
        # The claim still expires after the run limit, so a retry remains possible.
        logger.exception(
            "Could not record a failed reply", extra={"event": "reply_status_write_failed"}
        )


def _message_read(message: Message, stale_before: datetime) -> MessageRead:
    read = MessageRead.model_validate(message)
    current = conversation_service.effective_reply_status(message, stale_before=stale_before)
    return read.model_copy(update={"reply_status": current})


async def _knowledge_refs_visible(session: SessionDep, principal: Principal, message: Message) -> bool:
    """Reauthorize stored document citations when a reply is read after grants change."""
    if message.role != "assistant" or not message.agent_metadata:
        return True
    metadata = AgentReplyMetadata.model_validate(message.agent_metadata)
    refs = metadata.knowledge_citations
    if not refs:
        return True
    coverage = await knowledge_coverage_for(session, principal, Permission.KNOWLEDGE_READ)
    clauses = []
    if coverage.global_scope:
        clauses.append(KnowledgeSource.scope_type == KnowledgeScope.GLOBAL.value)
    if coverage.btp.account_ids:
        clauses.append((KnowledgeSource.scope_type == KnowledgeScope.BTP_ACCOUNT.value)
                       & KnowledgeSource.btp_account_id.in_(coverage.btp.account_ids))
    if coverage.btp.environment_ids:
        clauses.append((KnowledgeSource.scope_type == KnowledgeScope.BTP_ENVIRONMENT.value)
                       & KnowledgeSource.btp_environment_id.in_(coverage.btp.environment_ids))
    if not clauses:
        return False
    pairs = [((KnowledgeSource.id == ref.source_id) &
              (KnowledgeSource.active_version_id == ref.source_version_id)) for ref in refs]
    count = await session.scalar(
        select(func.count(func.distinct(KnowledgeSource.id)))
        .join(KnowledgeSourceVersion, KnowledgeSourceVersion.id == KnowledgeSource.active_version_id)
        .where(or_(*clauses), or_(*pairs), KnowledgeSource.status == SourceStatus.ACTIVE.value,
               KnowledgeSource.active_version_status == ValidationStatus.VALIDATED.value,
               KnowledgeSourceVersion.validation_status == ValidationStatus.VALIDATED.value)
    )
    return count == len({ref.source_id for ref in refs})


async def _authorized_message_read(
    session: SessionDep, principal: Principal, message: Message, stale_before: datetime
) -> MessageRead:
    visible = await _knowledge_refs_visible(session, principal, message)
    read = _message_read(message, stale_before)
    if not visible and message.role == "assistant":
        return read.model_copy(update={
            "content": "This document-based reply is no longer available because your current knowledge access changed."
        })
    return read


def _reply_read(message: Message, *, hide_knowledge: bool = False) -> AgentReplyRead:
    metadata = AgentReplyMetadata.model_validate(message.agent_metadata)
    read = MessageRead.model_validate(message)
    if hide_knowledge:
        metadata = metadata.model_copy(update={"knowledge_citations": [], "sources": []})
        read = read.model_copy(update={
            "content": "This document-based reply is no longer available because your current knowledge access changed."
        })
    return AgentReplyRead(message=read, **metadata.model_dump())
