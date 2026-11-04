"""Refresh and CSRF cookies.

- ``btp_refresh``: HttpOnly, SameSite=Strict, Secure, scoped to the auth endpoints only.
- ``btp_csrf``: readable by the SPA (double-submit token), SameSite=Strict, Secure. It is
  also bound to the server-side session, so a cookie planted by another site is useless.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request, Response

from app.core.config import Settings
from app.core.exceptions import PermissionDeniedError

REFRESH_COOKIE = "btp_refresh"
CSRF_COOKIE = "btp_csrf"
CSRF_HEADER = "X-CSRF-Token"


def _refresh_path(settings: Settings) -> str:
    return f"{settings.API_V1_PREFIX}/auth"


def set_auth_cookies(
    response: Response,
    settings: Settings,
    *,
    refresh_token: str,
    expires_at: datetime,
    csrf_token: str | None = None,
) -> None:
    max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=max_age,
        path=_refresh_path(settings),
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )
    if csrf_token is not None:
        response.set_cookie(
            CSRF_COOKIE,
            csrf_token,
            max_age=settings.SESSION_ABSOLUTE_TTL_SECONDS,
            path="/",
            secure=settings.AUTH_COOKIE_SECURE,
            httponly=False,
            samesite="strict",
        )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=_refresh_path(settings),
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        CSRF_COOKIE, path="/", secure=settings.AUTH_COOKIE_SECURE, samesite="strict"
    )


def enforce_trusted_origin(request: Request, settings: Settings) -> None:
    """Cookie-authenticated endpoints accept only same-origin or configured origins."""
    origin = request.headers.get("origin")
    if origin is None:
        return  # non-browser clients; SameSite + CSRF token still apply
    origin = origin.rstrip("/")
    same_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin != same_origin and origin not in settings.CORS_ORIGINS:
        raise PermissionDeniedError("Request origin is not allowed.", code="origin_not_allowed")
