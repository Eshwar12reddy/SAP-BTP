"""Authentication endpoints.

Access tokens travel in the ``Authorization`` header and live only in browser memory.
The refresh token is an HttpOnly cookie; endpoints that rely on it (refresh, logout)
also require the double-submit CSRF header and a trusted Origin.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import cookies
from app.auth.authorization import Principal
from app.auth.dependencies import CurrentPrincipal, SessionDep, get_principal
from app.auth.permissions import RoleName
from app.core.exceptions import AppError, AuthenticationError, error_body
from app.schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    MeResponse,
    TokenResponse,
    UserRead,
)
from app.services import auth_service
from app.services.grant_view import describe_grants

router = APIRouter(prefix="/auth", tags=["auth"])

RefreshCookie = Annotated[str | None, Cookie(alias=cookies.REFRESH_COOKIE)]
CsrfCookie = Annotated[str | None, Cookie(alias=cookies.CSRF_COOKIE)]
CsrfHeader = Annotated[str | None, Header(alias=cookies.CSRF_HEADER)]


def _token_response(issued: auth_service.IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token,
        expires_at=issued.access_expires_at,
        user=UserRead.model_validate(issued.user),
    )


def _error_clearing_cookies(request: Request, exc: AppError) -> JSONResponse:
    response = JSONResponse(
        error_body(exc.code, exc.message), status_code=exc.status_code, headers=exc.headers
    )
    cookies.clear_auth_cookies(response, request.app.state.settings)
    return response


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    """Email/password login. Failures always answer the same generic 401."""
    settings = request.app.state.settings
    issued = await auth_service.login(
        session,
        request.app.state.session_factory,
        settings,
        email=payload.email,
        password=payload.password,
        ip=request.client.host[:45] if request.client else None,
        user_agent=user_agent,
    )
    cookies.set_auth_cookies(
        response,
        settings,
        refresh_token=issued.refresh_token,
        expires_at=issued.refresh_expires_at,
        csrf_token=issued.csrf_token,
    )
    response.headers["Cache-Control"] = "no-store"
    return _token_response(issued)


@router.post("/refresh", response_model=TokenResponse, responses={409: {"description": "Concurrent refresh"}})
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    refresh_cookie: RefreshCookie = None,
    csrf_cookie: CsrfCookie = None,
    csrf_header: CsrfHeader = None,
):
    """Rotate the refresh token and issue a new access token."""
    settings = request.app.state.settings
    try:
        cookies.enforce_trusted_origin(request, settings)
        issued = await auth_service.refresh(
            session,
            settings,
            refresh_token=refresh_cookie,
            csrf_header=csrf_header,
            csrf_cookie=csrf_cookie,
        )
    except AuthenticationError as exc:
        return _error_clearing_cookies(request, exc)
    cookies.set_auth_cookies(
        response, settings, refresh_token=issued.refresh_token, expires_at=issued.refresh_expires_at
    )
    response.headers["Cache-Control"] = "no-store"
    return _token_response(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    session: SessionDep,
    refresh_cookie: RefreshCookie = None,
    csrf_cookie: CsrfCookie = None,
    csrf_header: CsrfHeader = None,
) -> Response:
    """End the current session. Works with a valid access token, or with the refresh
    cookie + CSRF header when the access token has already expired."""
    settings = request.app.state.settings
    principal: Principal | None = None
    if request.headers.get("authorization"):
        try:
            principal = await get_principal(request, session, await _bearer_credentials(request))
        except AuthenticationError:
            principal = None
    if principal is not None:
        await auth_service.logout(session, user_id=principal.user_id, session_id=principal.session_id)
    else:
        cookies.enforce_trusted_origin(request, settings)
        db_session = await auth_service.session_from_refresh_cookie(
            session, refresh_token=refresh_cookie, csrf_header=csrf_header, csrf_cookie=csrf_cookie
        )
        if db_session is not None:
            await auth_service.logout(session, user_id=db_session.user_id, session_id=db_session.id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    cookies.clear_auth_cookies(response, settings)
    return response


async def _bearer_credentials(request: Request) -> HTTPAuthorizationCredentials | None:
    return await HTTPBearer(auto_error=False)(request)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(request: Request, session: SessionDep, principal: CurrentPrincipal) -> Response:
    """End every session of the current user, on all devices."""
    await auth_service.logout_all(session, principal)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    cookies.clear_auth_cookies(response, request.app.state.settings)
    return response


@router.get("/me", response_model=MeResponse)
async def me(session: SessionDep, principal: CurrentPrincipal) -> MeResponse:
    return MeResponse(
        user=UserRead.model_validate(principal.user),
        roles=_ordered_roles(principal),
        permissions=sorted(p.value for p in principal.permissions),
        grants=await describe_grants(session, principal.grants),
    )


def _ordered_roles(principal: Principal) -> list[RoleName]:
    held = {g.role for g in principal.grants}
    return [role for role in RoleName if role in held]


@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> ChangePasswordResponse:
    """Change the current user's password. All *other* sessions are signed out."""
    revoked = await auth_service.change_password(
        session,
        request.app.state.session_factory,
        request.app.state.settings,
        principal,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return ChangePasswordResponse(other_sessions_revoked=revoked)
