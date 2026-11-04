"""Private, write-once file storage for knowledge document versions.

Layout under ``KNOWLEDGE_STORAGE_DIR`` (never served by the web server):

    staging/<random>.part   uploads being received and validated
    objects/<k[:2]>/<key>   published versions, named only by their opaque key

- Keys are 32 lowercase hex characters generated here. Client filenames never touch the
  filesystem, and every path is built from a validated key and checked to stay inside root.
- Publishing is exclusive (a hard link to a new name fails if the name exists), and the
  object is then made read-only, so a version's bytes are never overwritten.
- Uploads are copied in bounded chunks with a hard size limit while the SHA-256 is computed.

PostgreSQL and the filesystem do not commit together. The knowledge service publishes the
object before the database commit and deletes it if the commit fails, so the database never
references a missing file. A crash between the two can leave an unreferenced object or a
stale staging file; ``python -m app.cli knowledge reconcile`` reports and removes those.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import stat
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CHUNK_BYTES = 64 * 1024
HEAD_BYTES = 8 * 1024
_KEY = re.compile(r"^[0-9a-f]{32}$")
_READ_ONLY = stat.S_IRUSR | stat.S_IRGRP


class UploadTooLarge(Exception):
    pass


class EmptyUpload(Exception):
    pass


class StoredObjectError(Exception):
    """A published object is missing or its bytes no longer match the recorded checksum."""


class AsyncReader(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class StagedFile:
    path: Path
    size: int
    sha256: str
    head: bytes  # the first bytes, for format detection


def _sync_and_close(handle) -> None:
    handle.flush()
    os.fsync(handle.fileno())
    handle.close()


class KnowledgeStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.staging = self.root / "staging"
        self.objects = self.root / "objects"

    @staticmethod
    def new_key() -> str:
        return secrets.token_hex(16)

    async def stage(self, source: AsyncReader, max_bytes: int) -> StagedFile:
        """Copy an upload into staging, hashing it, and stop as soon as it exceeds max_bytes."""
        self._ensure_dirs()
        path = self.staging / f"{secrets.token_hex(16)}.part"
        digest = hashlib.sha256()
        size, head = 0, b""
        try:
            # File I/O runs in a worker thread so a large upload never blocks the event loop.
            out = await asyncio.to_thread(open, path, "xb")
            try:
                while chunk := await source.read(CHUNK_BYTES):
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLarge()
                    if len(head) < HEAD_BYTES:
                        head += chunk[: HEAD_BYTES - len(head)]
                    digest.update(chunk)
                    await asyncio.to_thread(out.write, chunk)
                await asyncio.to_thread(_sync_and_close, out)
            finally:
                if not out.closed:
                    out.close()
            if size == 0:
                raise EmptyUpload()
        except BaseException:
            self._unlink(path)
            raise
        return StagedFile(path=path, size=size, sha256=digest.hexdigest(), head=head)

    def publish(self, staged: StagedFile, key: str) -> None:
        """Move a staged file to its permanent, read-only object name. Never overwrites."""
        target = self._object_path(key)
        target.parent.mkdir(mode=0o700, exist_ok=True)
        try:
            os.link(staged.path, target)  # FileExistsError if the key is taken
        except FileExistsError:
            raise
        except OSError:
            # No hard links on this filesystem: exclusive create plus copy instead.
            with open(staged.path, "rb") as src, open(target, "xb") as dst:
                while chunk := src.read(CHUNK_BYTES):
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
        os.chmod(target, _READ_ONLY)
        self._unlink(staged.path)

    def discard(self, staged: StagedFile) -> None:
        self._unlink(staged.path)

    def delete_object(self, key: str) -> None:
        """Only for cleanup: an object whose database row was never committed, or an orphan."""
        self._unlink(self._object_path(key))

    def verified_path(self, key: str, *, size: int, sha256: str) -> Path:
        """The object's path, after checking that its bytes match what was recorded."""
        path = self._object_path(key)
        try:
            if path.stat().st_size != size:
                raise StoredObjectError("size mismatch")
            digest = hashlib.sha256()
            with open(path, "rb") as stored:
                while chunk := stored.read(CHUNK_BYTES):
                    digest.update(chunk)
        except FileNotFoundError:
            raise StoredObjectError("missing") from None
        if digest.hexdigest() != sha256:
            raise StoredObjectError("checksum mismatch")
        return path

    def object_keys(self) -> Iterator[str]:
        if not self.objects.is_dir():
            return
        for path in self.objects.glob("??/*"):
            if _KEY.match(path.name) and path.parent.name == path.name[:2]:
                yield path.name

    def stale_staging(self, older_than_seconds: float) -> list[Path]:
        if not self.staging.is_dir():
            return []
        cutoff = time.time() - older_than_seconds
        return [p for p in self.staging.glob("*.part") if p.stat().st_mtime < cutoff]

    def object_age_seconds(self, key: str) -> float:
        return time.time() - self._object_path(key).stat().st_mtime

    def _object_path(self, key: str) -> Path:
        if not _KEY.match(key):
            raise ValueError("invalid storage key")
        path = (self.objects / key[:2] / key).resolve()
        if not path.is_relative_to(self.objects):
            raise ValueError("storage path escapes the storage root")
        return path

    def _ensure_dirs(self) -> None:
        for directory in (self.staging, self.objects):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # read-only files cannot be removed on Windows
            path.unlink()
        except FileNotFoundError:
            pass
