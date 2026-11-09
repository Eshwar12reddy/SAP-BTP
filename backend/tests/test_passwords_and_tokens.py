from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth import passwords
from app.auth.permissions import ROLE_PERMISSIONS, Permission, RoleName
from app.auth.tokens import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_token,
    new_opaque_token,
)
from app.core.config import load_settings
from tests.conftest import PRODUCTION_HASHER, TEST_JWT_SECRET

SETTINGS = load_settings(
    _env_file=None, DATABASE_URL="postgresql://u:p@h/db", JWT_SECRET_KEY=TEST_JWT_SECRET
)


# ---------- passwords ----------


def test_production_hasher_is_argon2id_with_strong_parameters() -> None:
    encoded = PRODUCTION_HASHER.hash("a sufficiently long password")
    assert encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_hash_and_verify() -> None:
    encoded = passwords.hash_password("correct horse battery staple")
    assert encoded.startswith("$argon2id$")
    assert "correct horse" not in encoded
    assert passwords.verify_password(encoded, "correct horse battery staple")
    assert not passwords.verify_password(encoded, "correct horse battery stapl")
    assert not passwords.verify_password("not-a-hash", "anything")


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("short", "at least 12"),
        ("x" * 129, "at most 128"),
        (" " * 14, "blank"),
        ("Person@Example.com", "email"),
    ],
)
def test_password_policy(password: str, problem: str) -> None:
    problems = passwords.password_policy_problems(password, email="person@example.com")
    assert any(problem in p for p in problems)


def test_password_policy_accepts_twelve_characters() -> None:
    assert passwords.password_policy_problems("twelve chars", email="a@b.co") == []


# ---------- tokens ----------


def _claims(**overrides) -> dict:
    now = datetime.now(UTC)
    claims = {
        "iss": SETTINGS.JWT_ISSUER,
        "aud": SETTINGS.JWT_AUDIENCE,
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "typ": "access",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=15),
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _encode(claims: dict, key: str = TEST_JWT_SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, key, algorithm=algorithm)


def test_access_token_round_trip_and_fifteen_minute_lifetime() -> None:
    user_id, session_id = uuid.uuid4(), uuid.uuid4()
    token, expires_at = create_access_token(SETTINGS, user_id=user_id, session_id=session_id)
    claims = decode_access_token(SETTINGS, token)
    assert (claims.user_id, claims.session_id) == (user_id, session_id)
    lifetime = expires_at - datetime.now(UTC)
    assert timedelta(minutes=14) < lifetime <= timedelta(minutes=15)


@pytest.mark.parametrize(
    ("token_factory", "code"),
    [
        (lambda: _encode(_claims(exp=datetime.now(UTC) - timedelta(seconds=1))), "token_expired"),
        (lambda: _encode(_claims(), key="another-secret-of-sufficient-length-123456"), "token_invalid"),
        (lambda: _encode(_claims(aud="someone-else")), "token_invalid"),
        (lambda: _encode(_claims(iss="someone-else")), "token_invalid"),
        (lambda: _encode(_claims(nbf=datetime.now(UTC) + timedelta(minutes=5))), "token_invalid"),
        (lambda: _encode(_claims(sid=None)), "token_invalid"),
        (lambda: _encode(_claims(jti=None)), "token_invalid"),
        (lambda: _encode(_claims(typ="refresh")), "token_invalid"),
        (lambda: _encode(_claims(sub="not-a-uuid")), "token_invalid"),
        (lambda: _encode(_claims(), algorithm="HS512"), "token_invalid"),
        (lambda: jwt.encode(_claims(), None, algorithm="none"), "token_invalid"),
        (lambda: "not.a.jwt", "token_invalid"),
    ],
)
# The HS512 case signs with a short key on purpose; PyJWT warns about that.
@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
def test_invalid_access_tokens_are_rejected(token_factory, code: str) -> None:
    with pytest.raises(InvalidTokenError) as info:
        decode_access_token(SETTINGS, token_factory())
    assert info.value.code == code


def test_tampered_token_is_rejected() -> None:
    token, _ = create_access_token(SETTINGS, user_id=uuid.uuid4(), session_id=uuid.uuid4())
    header, _payload, signature = token.split(".")
    forged = jwt.encode(_claims(sub=str(uuid.uuid4())), "x" * 40, algorithm="HS256").split(".")[1]
    with pytest.raises(InvalidTokenError):
        decode_access_token(SETTINGS, f"{header}.{forged}.{signature}")


def test_opaque_tokens_are_random_and_only_hashed() -> None:
    a, b = new_opaque_token(), new_opaque_token()
    assert a != b and len(a) >= 43
    assert hash_token(a) != a and len(hash_token(a)) == 64


# ---------- role definitions ----------


def test_role_permission_definitions() -> None:
    viewer = ROLE_PERMISSIONS[RoleName.VIEWER]
    # Phase 11 deliberately lets viewers inspect/select connection metadata, but
    # never create, update, validate, resolve credentials, or modify BTP state.
    assert viewer == {
        Permission.CHAT_USE,
        Permission.BTP_READ,
        Permission.BTP_CONNECTIONS_READ,
        Permission.BTP_ENVIRONMENT_SELECT,
        Permission.KNOWLEDGE_READ,
    }
    assert Permission.BTP_CONNECTIONS_CREATE not in viewer
    assert Permission.BTP_CONNECTIONS_UPDATE not in viewer
    assert Permission.BTP_CONNECTIONS_VALIDATE not in viewer
    assert viewer < ROLE_PERMISSIONS[RoleName.OPERATOR]
    assert viewer < ROLE_PERMISSIONS[RoleName.APPROVER]
    assert Permission.APPROVALS_DECIDE not in ROLE_PERMISSIONS[RoleName.OPERATOR]
    assert Permission.OPERATIONS_REQUEST not in ROLE_PERMISSIONS[RoleName.APPROVER]
    admin = ROLE_PERMISSIONS[RoleName.ADMINISTRATOR]
    # Administrators manage access but get no BTP data or chat by default.
    assert Permission.BTP_READ not in admin and Permission.CHAT_USE not in admin
    assert Permission.GRANTS_MANAGE in admin
