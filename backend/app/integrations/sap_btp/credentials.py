"""Server-side resolution of opaque BTP credential references."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.core.config import Settings


class CredentialReferenceError(ValueError):
    """A reference is not registered or its secret is unavailable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ResolvedCredential:
    """Trusted backend-only credential material; never serialize this object."""

    reference: str
    value: str


class CredentialResolver:
    """Resolve only references in the server-controlled settings mapping."""

    def __init__(self, settings: Settings) -> None:
        self._mapping = dict(settings.BTP_CREDENTIAL_ENV_MAP)

    def is_registered(self, reference: str) -> bool:
        return self._environment_name(reference) is not None

    def resolve(self, reference: str) -> ResolvedCredential:
        environment_name = self._environment_name(reference)
        if environment_name is None:
            raise CredentialReferenceError("credential_reference_unrecognized")
        value = os.environ.get(environment_name)
        if not value:
            raise CredentialReferenceError("credentials_unavailable")
        return ResolvedCredential(reference=reference, value=value)

    def _environment_name(self, reference: str) -> str | None:
        if not reference.startswith("env://"):
            return None
        key = reference.removeprefix("env://")
        if not key or key not in self._mapping:
            return None
        environment_name = self._mapping[key]
        if not environment_name or not environment_name.replace("_", "").isalnum():
            return None
        return environment_name
