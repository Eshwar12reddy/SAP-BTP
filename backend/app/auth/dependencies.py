"""FastAPI dependencies that authenticate the caller and enforce permissions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Principal, load_active_grants
from app.auth.permissions import Permission
from app.auth.tokens import InvalidTokenError, decode_access_token
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.db.session import get_session
from app.models import Session, User
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome

_bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_principal(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError()
    try:
        claims = decode_access_token(request.app.state.settings, credentials.credentials)
    except InvalidTokenError as exc:
        message = "The access token has expired." if exc.code == "token_expired" else None
        raise AuthenticationError(message, code=exc.code) from None

    # The JWT alone is not enough: the server-side session must still be valid, so
    # logout, revocation and account disabling take effect immediately.
    db_session = await session.get(Session, claims.session_id)
    now = datetime.now(UTC)
    if (
        db_session is None
        or db_session.user_id != claims.user_id
        or db_session.revoked_at is not None
        or db_session.absolute_expires_at <= now
        or db_session.idle_expires_at <= now
    ):
        raise AuthenticationError("Your session has ended. Please sign in again.", code="session_expired")
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Your session has ended. Please sign in again.", code="session_expired")

    principal = Principal(
        user=user,
        session_id=db_session.id,
        grants=await load_active_grants(session, user.id),
    )
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def deny(
    request: Request,
    principal: Principal,
    *,
    reason: str,
    permission: Permission | None = None,
    error: Exception | None = None,
    **scope,
) -> None:
    """Audit an access denial in its own transaction, then raise (403 unless given)."""
    details = {"reason": reason, "route": request.scope.get("path")}
    if permission:
        details["permission"] = permission.value
    await audit_service.record_standalone(
        request.app.state.session_factory,
        AuditEventType.ACCESS_DENIED,
        Outcome.DENIED,
        actor_user_id=principal.user_id,
        details=details,
        **scope,
    )
    raise error or PermissionDeniedError()


def require_permission(permission: Permission):
    """Dependency: the caller holds ``permission`` through at least one active grant.

    Scope checks for specific resources happen in the endpoint/service on top of this.
    """

    async def dependency(request: Request, principal: CurrentPrincipal) -> Principal:
        if not principal.has_permission(permission):
            await deny(request, principal, reason="missing_permission", permission=permission)
        return principal

    return dependency
