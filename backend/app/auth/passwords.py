"""Argon2id password hashing and the password policy for new passwords.

Hashing is CPU- and memory-heavy by design, so async callers run these functions in a
worker thread (``asyncio.to_thread``) to keep the event loop responsive.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12
# Bounds hashing cost for hostile input.
MAX_PASSWORD_LENGTH = 128

# RFC 9106 second recommended profile (argon2id, 64 MiB, t=3, p=4).
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4, type=Type.ID)

# Verifying against this when the account does not exist keeps response timing uniform.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def burn_verification_time(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def password_policy_problems(password: str, *, email: str) -> list[str]:
    """Reasons a *new* password is unacceptable (empty list when it is fine)."""
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"must be at most {MAX_PASSWORD_LENGTH} characters")
    if not password.strip():
        problems.append("must not be blank")
    if password.strip().lower() == email.strip().lower():
        problems.append("must not be the email address")
    return problems
