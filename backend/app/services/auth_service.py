"""Login, refresh-token rotation, logout and password changes."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import passwords
from app.auth.authorization import Principal
from app.auth.tokens import (
    create_access_token,
    email_fingerprint,
    hash_token,
    new_opaque_token,
    tokens_match,
)
from app.core.config import Settings
from app.core.exceptions import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    PermissionDeniedError,
    TooManyRequestsError,
)
from app.models import LoginAttempt, Session, SessionRefreshToken, User
from app.models.user import normalize_email
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome

INVALID_CREDENTIALS = "Invalid email or password."


@dataclass(frozen=True)
class IssuedTokens:
    user: User
    session_id: uuid.UUID
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    csrf_token: str | None  # only issued at login


# ---------- rate limiting ----------


async def _failures_since(
    session: AsyncSession, settings: Settings, email_hash: str, ip: str | None
) -> tuple[int, int, datetime | None]:
    """Recent failures for this email (since its last success) and for this IP."""
    window_start = datetime.now(UTC) - timedelta(seconds=settings.LOGIN_WINDOW_SECONDS)
    last_success = await session.scalar(
        select(func.max(LoginAttempt.created_at)).where(
            LoginAttempt.email_hash == email_hash, LoginAttempt.succeeded
        )
    )
    email_start = max(window_start, last_success) if last_success else window_start
    email_failures, oldest = (
        await session.execute(
            select(func.count(), func.min(LoginAttempt.created_at)).where(
                LoginAttempt.email_hash == email_hash,
                ~LoginAttempt.succeeded,
                LoginAttempt.created_at > email_start,
            )
        )
    ).one()
    ip_failures = 0
    if ip:
        ip_failures = await session.scalar(
            select(func.count()).where(
                LoginAttempt.ip_address == ip,
                ~LoginAttempt.succeeded,
                LoginAttempt.created_at > window_start,
            )
        )
    return email_failures, ip_failures or 0, oldest


async def _enforce_rate_limit(
    session: AsyncSession, settings: Settings, email_hash: str, ip: str | None
) -> None:
    email_failures, ip_failures, oldest = await _failures_since(session, settings, email_hash, ip)
    if (
        email_failures >= settings.LOGIN_MAX_FAILURES_PER_EMAIL
        or ip_failures >= settings.LOGIN_MAX_FAILURES_PER_IP
    ):
        retry_after = settings.LOGIN_WINDOW_SECONDS
        if oldest is not None and email_failures >= settings.LOGIN_MAX_FAILURES_PER_EMAIL:
            elapsed = (datetime.now(UTC) - oldest).total_seconds()
            retry_after = max(1, int(settings.LOGIN_WINDOW_SECONDS - elapsed))
        raise TooManyRequestsError(headers={"Retry-After": str(retry_after)})


# ---------- sessions ----------


def _new_refresh_row(session_id: uuid.UUID) -> tuple[str, SessionRefreshToken]:
    raw = new_opaque_token()
    return raw, SessionRefreshToken(id=uuid.uuid4(), session_id=session_id, token_hash=hash_token(raw))


async def revoke_sessions(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    reason: str,
    only_session_id: uuid.UUID | None = None,
    except_session_id: uuid.UUID | None = None,
) -> int:
    """Revoke sessions (and their refresh tokens) in the caller's transaction."""
    now = datetime.now(UTC)
    conditions = [Session.user_id == user_id, Session.revoked_at.is_(None)]
    if only_session_id is not None:
        conditions.append(Session.id == only_session_id)
    if except_session_id is not None:
        conditions.append(Session.id != except_session_id)
    revoked = list(
        await session.scalars(
            update(Session)
            .where(*conditions)
            .values(revoked_at=now, revoked_reason=reason)
            .returning(Session.id)
        )
    )
    if revoked:
        await session.execute(
            update(SessionRefreshToken)
            .where(
                SessionRefreshToken.session_id.in_(revoked),
                SessionRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    return len(revoked)


# ---------- login ----------


async def login(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> IssuedTokens:
    normalized = normalize_email(email)
    email_hash = email_fingerprint(normalized)

    try:
        await _enforce_rate_limit(session, settings, email_hash, ip)
    except TooManyRequestsError:
        await audit_service.record_standalone(
            session_factory,
            AuditEventType.LOGIN_RATE_LIMITED,
            Outcome.DENIED,
            details={"email_hash": email_hash},
        )
        raise

    user = await session.scalar(select(User).where(User.email == normalized))
    if user is None:
        await asyncio.to_thread(passwords.burn_verification_time, password)
        valid, reason = False, "unknown_user"
    else:
        valid = await asyncio.to_thread(passwords.verify_password, user.password_hash, password)
        reason = "bad_password" if not valid else ("disabled" if not user.is_active else "")
        valid = valid and user.is_active

    if not valid:
        target_user_id = user.id if user else None  # read before rollback expires it
        await session.rollback()
        async with session_factory() as audit_session:
            audit_session.add(LoginAttempt(email_hash=email_hash, ip_address=ip, succeeded=False))
            audit_service.record(
                audit_session,
                AuditEventType.LOGIN_FAILURE,
                Outcome.FAILURE,
                target_user_id=target_user_id,
                details={"reason": reason, "email_hash": email_hash},
            )
            await audit_session.commit()
        # Identical response for unknown, wrong-password and disabled accounts.
        raise AuthenticationError(INVALID_CREDENTIALS, code="invalid_credentials")

    assert user is not None
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = await asyncio.to_thread(passwords.hash_password, password)

    now = datetime.now(UTC)
    absolute = now + timedelta(seconds=settings.SESSION_ABSOLUTE_TTL_SECONDS)
    idle = min(now + timedelta(seconds=settings.REFRESH_IDLE_TTL_SECONDS), absolute)
    csrf_token = new_opaque_token()
    db_session = Session(
        id=uuid.uuid4(),
        user_id=user.id,
        csrf_token_hash=hash_token(csrf_token),
        user_agent=(user_agent or "")[:255] or None,
        ip_address=ip,
        idle_expires_at=idle,
        absolute_expires_at=absolute,
    )
    raw_refresh, refresh_row = _new_refresh_row(db_session.id)
    user.last_login_at = now
    session.add(db_session)
    await session.flush()  # the refresh token row references the session
    session.add(refresh_row)
    session.add(LoginAttempt(email_hash=email_hash, ip_address=ip, succeeded=True))
    audit_service.record(
        session,
        AuditEventType.LOGIN_SUCCESS,
        Outcome.SUCCESS,
        actor_user_id=user.id,
        target_user_id=user.id,
        details={"session_id": str(db_session.id)},
    )
    await session.commit()

    access, access_expires = create_access_token(settings, user_id=user.id, session_id=db_session.id)
    return IssuedTokens(user, db_session.id, access, access_expires, raw_refresh, idle, csrf_token)


# ---------- refresh ----------


async def refresh(
    session: AsyncSession,
    settings: Settings,
    *,
    refresh_token: str | None,
    csrf_header: str | None,
    csrf_cookie: str | None,
) -> IssuedTokens:
    if not refresh_token:
        raise AuthenticationError("Your session has ended. Please sign in again.", code="session_expired")
    # Double-submit check: the header must echo the CSRF cookie.
    if not tokens_match(csrf_header, csrf_cookie):
        raise PermissionDeniedError("CSRF validation failed.", code="csrf_failed")

    # Row locks serialize concurrent refreshes of the same token and session.
    token = await session.scalar(
        select(SessionRefreshToken)
        .where(SessionRefreshToken.token_hash == hash_token(refresh_token))
        .with_for_update()
    )
    if token is None:
        raise AuthenticationError("Your session has ended. Please sign in again.", code="session_expired")
    db_session = await session.scalar(
        select(Session).where(Session.id == token.session_id).with_for_update()
    )
    assert db_session is not None
    # ...and the CSRF token must be the one bound to this session.
    if not tokens_match(hash_token(csrf_header or ""), db_session.csrf_token_hash):
        raise PermissionDeniedError("CSRF validation failed.", code="csrf_failed")

    now = datetime.now(UTC)
    if db_session.revoked_at is not None:
        raise AuthenticationError("Your session has ended. Please sign in again.", code="session_expired")

    if token.used_at is not None or token.revoked_at is not None:
        within_grace = (
            token.revoked_at is None
            and token.used_at is not None
            and (now - token.used_at).total_seconds() <= settings.REFRESH_REUSE_GRACE_SECONDS
        )
        if within_grace:
            # A parallel request already rotated this token; nothing is issued.
            raise ConflictError("The session was refreshed by another request.", code="refresh_in_progress")
        # Reuse of a rotated token: assume theft and kill the whole session.
        await revoke_sessions(
            session, db_session.user_id, reason="refresh_token_reuse", only_session_id=db_session.id
        )
        audit_service.record(
            session,
            AuditEventType.REFRESH_REUSE_DETECTED,
            Outcome.DENIED,
            target_user_id=db_session.user_id,
            details={"session_id": str(db_session.id)},
        )
        await session.commit()
        raise AuthenticationError("Your session has ended. Please sign in again.", code="session_revoked")

    if db_session.absolute_expires_at <= now or db_session.idle_expires_at <= now:
        raise AuthenticationError("Your session has expired. Please sign in again.", code="session_expired")
    user = await session.get(User, db_session.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Your session has ended. Please sign in again.", code="session_expired")

    raw_refresh, new_row = _new_refresh_row(db_session.id)
    session.add(new_row)
    await session.flush()
    token.used_at = now
    token.replaced_by_token_id = new_row.id
    db_session.last_used_at = now
    db_session.idle_expires_at = min(
        now + timedelta(seconds=settings.REFRESH_IDLE_TTL_SECONDS), db_session.absolute_expires_at
    )
    idle_expires_at = db_session.idle_expires_at
    await session.commit()

    access, access_expires = create_access_token(settings, user_id=user.id, session_id=db_session.id)
    return IssuedTokens(user, db_session.id, access, access_expires, raw_refresh, idle_expires_at, None)


async def session_from_refresh_cookie(
    session: AsyncSession, *, refresh_token: str | None, csrf_header: str | None, csrf_cookie: str | None
) -> Session | None:
    """The session a refresh cookie belongs to, if the CSRF pair is valid (for logout)."""
    if not refresh_token or not tokens_match(csrf_header, csrf_cookie):
        return None
    token = await session.scalar(
        select(SessionRefreshToken).where(SessionRefreshToken.token_hash == hash_token(refresh_token))
    )
    if token is None:
        return None
    db_session = await session.get(Session, token.session_id)
    if db_session is None or not tokens_match(hash_token(csrf_header or ""), db_session.csrf_token_hash):
        return None
    return db_session


# ---------- logout / password ----------


async def logout(session: AsyncSession, *, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
    revoked = await revoke_sessions(session, user_id, reason="logout", only_session_id=session_id)
    if revoked:
        audit_service.record(
            session,
            AuditEventType.LOGOUT,
            Outcome.SUCCESS,
            actor_user_id=user_id,
            target_user_id=user_id,
            details={"session_id": str(session_id)},
        )
    await session.commit()


async def logout_all(session: AsyncSession, principal: Principal) -> int:
    count = await revoke_sessions(session, principal.user_id, reason="logout_all")
    audit_service.record(
        session,
        AuditEventType.LOGOUT_ALL,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        target_user_id=principal.user_id,
        details={"sessions_revoked": count},
    )
    await session.commit()
    return count


async def change_password(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    principal: Principal,
    *,
    current_password: str,
    new_password: str,
) -> int:
    user = principal.user
    user_id = user.id
    email_hash = email_fingerprint(user.email)
    # Wrong current passwords count toward the login limit (stolen access token defence).
    await _enforce_rate_limit(session, settings, email_hash, None)
    if not await asyncio.to_thread(passwords.verify_password, user.password_hash, current_password):
        await session.rollback()
        async with session_factory() as audit_session:
            audit_session.add(LoginAttempt(email_hash=email_hash, ip_address=None, succeeded=False))
            audit_service.record(
                audit_session,
                AuditEventType.PASSWORD_CHANGE_FAILED,
                Outcome.FAILURE,
                actor_user_id=user_id,
                target_user_id=user_id,
                details={"reason": "bad_current_password"},
            )
            await audit_session.commit()
        raise BadRequestError("The current password is incorrect.", code="invalid_current_password")

    problems = passwords.password_policy_problems(new_password, email=user.email)
    if new_password == current_password:
        problems.append("must differ from the current password")
    if problems:
        raise BadRequestError("New password " + "; ".join(problems) + ".", code="weak_password")

    user.password_hash = await asyncio.to_thread(passwords.hash_password, new_password)
    user.password_changed_at = datetime.now(UTC)
    # Other devices must sign in again; the current session continues.
    revoked = await revoke_sessions(
        session, user.id, reason="password_changed", except_session_id=principal.session_id
    )
    audit_service.record(
        session,
        AuditEventType.PASSWORD_CHANGED,
        Outcome.SUCCESS,
        actor_user_id=user.id,
        target_user_id=user.id,
        details={"other_sessions_revoked": revoked},
    )
    await session.commit()
    return revoked

