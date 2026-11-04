"""Application errors and the handlers that turn them into a consistent JSON shape.

Every error response looks like::

    {"error": {"code": "not_found", "message": "...", "request_id": "..."}}

Raw database errors, SQL and stack traces are never returned to clients.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import exc as sa_exc
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "internal_error"
    message = "An unexpected error occurred."
    headers: dict[str, str] | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        if code:
            self.code = code
        if headers:
            self.headers = headers


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    message = "The request could not be processed."


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "not_authenticated"
    message = "Authentication is required."
    headers = {"WWW-Authenticate": "Bearer"}  # noqa: RUF012


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have permission to perform this action."


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "too_many_requests"
    message = "Too many attempts. Please wait and try again."


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "payload_too_large"
    message = "The upload is larger than the allowed maximum."


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The request conflicts with existing data."


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "The database is temporarily unavailable. Please try again shortly."


def error_body(code: str, message: str, details: list[dict] | None = None) -> dict:
    error: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": request_id_var.get(),
    }
    if details:
        error["details"] = details
    return {"error": error}


def is_database_unavailable(exc: sa_exc.SQLAlchemyError) -> bool:
    """True for connection loss, refused connections, pool exhaustion and timeouts."""
    if isinstance(exc, (sa_exc.TimeoutError, sa_exc.OperationalError, sa_exc.InterfaceError)):
        return True
    return isinstance(exc, sa_exc.DBAPIError) and exc.connection_invalidated


async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        error_body(exc.code, exc.message), status_code=exc.status_code, headers=exc.headers
    )


async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {
        404: "not_found",
        405: "method_not_allowed",
    }.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(
        error_body(code, message), status_code=exc.status_code, headers=exc.headers
    )


async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Only location, message and type: echoing "input" could leak message contents.
    details = [
        {
            "loc": [str(part) for part in error.get("loc", ())],
            "message": error.get("msg", "Invalid value."),
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        error_body("validation_error", "The request is invalid.", details),
        status_code=422,
    )


async def _database_error_handler(_: Request, exc: sa_exc.SQLAlchemyError) -> JSONResponse:
    # Log only the exception type: SQLAlchemy messages can include SQL text.
    if is_database_unavailable(exc):
        logger.warning(
            "Database unavailable while handling request",
            extra={"event": "database_unavailable", "error_type": type(exc).__name__},
        )
        error = ServiceUnavailableError()
    else:
        logger.error(
            "Unhandled database error",
            extra={"event": "database_error", "error_type": type(exc).__name__},
        )
        error = AppError()
    return JSONResponse(error_body(error.code, error.message), status_code=error.status_code)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(sa_exc.SQLAlchemyError, _database_error_handler)  # type: ignore[arg-type]
