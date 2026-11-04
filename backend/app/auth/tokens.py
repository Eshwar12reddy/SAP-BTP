"""Access-token JWTs and opaque refresh/CSRF tokens.

Access tokens: HS256, 15 minutes, bound to a server-side session (``sid``) and checked
for issuer, audience, signature, expiry, not-before, token type and id.
Refresh and CSRF tokens: 256-bit random strings; only their SHA-256 is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"
_REQUIRED_CLAIMS = ["iss", "aud", "sub", "sid", "jti", "iat", "nbf", "exp", "typ"]


class InvalidTokenError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code  # "token_expired" or "token_invalid"


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    token_id: uuid.UUID
    expires_at: datetime


def _key(settings: Settings) -> str:
    assert settings.JWT_SECRET_KEY is not None  # enforced by settings validation
    return settings.JWT_SECRET_KEY.get_secret_value()


def create_access_token(
    settings: Settings, *, user_id: uuid.UUID, session_id: uuid.UUID, now: datetime | None = None
) -> tuple[str, datetime]:
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS)
    claims = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(uuid.uuid4()),
        "typ": ACCESS_TOKEN_TYPE,
        "iat": now,
        "nbf": now,
        "exp": expires_at,
    }
    return jwt.encode(claims, _key(settings), algorithm=ALGORITHM), expires_at


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    try:
        claims = jwt.decode(
            token,
            _key(settings),
            algorithms=[ALGORITHM],  # never trust the header's "alg"
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError:
        raise InvalidTokenError("token_expired") from None
    except jwt.PyJWTError:
        raise InvalidTokenError("token_invalid") from None
    if claims.get("typ") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("token_invalid")
    try:
        return AccessClaims(
            user_id=uuid.UUID(claims["sub"]),
            session_id=uuid.UUID(claims["sid"]),
            token_id=uuid.UUID(claims["jti"]),
            expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        )
    except (ValueError, TypeError, AttributeError):
        raise InvalidTokenError("token_invalid") from None


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())  # type: ignore[union-attr]


def email_fingerprint(normalized_email: str) -> str:
    """Stable, non-reversible key for rate limiting and audit of login attempts."""
    return hashlib.sha256(b"login:" + normalized_email.encode()).hexdigest()
