"""Knowledge source management: registration, metadata, versions, downloads, removal.

Authorization happens here, before any data is read or any upload is accepted:
- reading needs ``knowledge:read`` coverage of the source's scope; anything else (missing,
  removed, or out of scope) is the same 404, and out-of-scope attempts are audited;
- changes additionally need ``knowledge:create`` / ``update`` / ``remove`` coverage of that
  scope (403 when the source is readable but not changeable).

Identities (owner, creator, uploader, remover) come from the authenticated principal only.

Writes that store a file follow one order: database rows (flushed, uncommitted), then the
file is published, then the commit. If the commit fails the published file is deleted, so a
committed version always has its bytes. A crash between publish and commit leaves only an
unreferenced file, which ``python -m app.cli knowledge reconcile`` cleans up.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.authorization import KnowledgeCoverage, Principal, knowledge_coverage_for
from app.auth.permissions import Permission
from app.core.config import Settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    ServiceUnavailableError,
)
from app.knowledge.storage import (
    AsyncReader,
    EmptyUpload,
    KnowledgeStorage,
    StagedFile,
    StoredObjectError,
    UploadTooLarge,
)
from app.knowledge.validation import (
    REJECTION_MESSAGES,
    FileRejected,
    clean_filename,
    validate_document,
)
from app.integrations.openai_embeddings import OpenAIEmbeddingsClient
from app.models import (
    BTPAccount,
    BTPEnvironment,
    KnowledgeScope,
    KnowledgeSource,
    KnowledgeSourceVersion,
    SourceStatus,
    ValidationStatus,
)
from app.schemas.knowledge import SourceRegistration, SourceRemoval, SourceUpdate
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome
from app.safety.redaction import Redactor

logger = logging.getLogger(__name__)

SOURCE_NOT_FOUND = "Knowledge source not found."
VERSION_NOT_FOUND = "Knowledge version not found."
SCOPE_NOT_FOUND = "Knowledge scope not found."
VALIDATION_TIMEOUT_SECONDS = 20.0


@dataclass
class KnowledgeContext:
    session: AsyncSession
    session_factory: async_sessionmaker[AsyncSession]
    principal: Principal
    storage: KnowledgeStorage
    settings: Settings
    embedding_client: OpenAIEmbeddingsClient | None = None
    redactor: Redactor | None = None

    @property
    def actor_id(self) -> uuid.UUID:
        return self.principal.user_id

    async def coverage(self, permission: Permission) -> KnowledgeCoverage:
        return await knowledge_coverage_for(self.session, self.principal, permission)

    async def deny(
        self, reason: str, error: Exception, *, source_id: uuid.UUID | None = None, **scope
    ) -> NoReturn:
        details: dict[str, object] = {"reason": reason}
        if source_id is not None:
            details["source_id"] = str(source_id)
        await audit_service.record_standalone(
            self.session_factory,
            AuditEventType.KNOWLEDGE_ACCESS_DENIED,
            Outcome.DENIED,
            actor_user_id=self.actor_id,
            details=details,
            **scope,
        )
        raise error


def _scope_fields(source: KnowledgeSource) -> dict[str, object]:
    return {
        "scope_type": source.scope_type,
        "btp_account_id": source.btp_account_id,
        "btp_environment_id": source.btp_environment_id,
    }


# ---------- reading ----------


def _readable(coverage: KnowledgeCoverage):
    """SQL condition for the sources a coverage reaches (None when it reaches nothing)."""
    parts = []
    if coverage.global_scope:
        parts.append(KnowledgeSource.scope_type == KnowledgeScope.GLOBAL.value)
    if coverage.btp.account_ids:
        parts.append(
            and_(
                KnowledgeSource.scope_type == KnowledgeScope.BTP_ACCOUNT.value,
                KnowledgeSource.btp_account_id.in_(coverage.btp.account_ids),
            )
        )
    if coverage.btp.environment_ids:
        parts.append(
            and_(
                KnowledgeSource.scope_type == KnowledgeScope.BTP_ENVIRONMENT.value,
                KnowledgeSource.btp_environment_id.in_(coverage.btp.environment_ids),
            )
        )
    return or_(*parts) if parts else None


async def list_sources(
    ctx: KnowledgeContext,
    *,
    limit: int,
    offset: int,
    query: str | None = None,
    tag: str | None = None,
    scope_type: KnowledgeScope | None = None,
) -> tuple[list[tuple[KnowledgeSource, KnowledgeSourceVersion]], bool]:
    """Active sources the caller can read. Filters only ever narrow the authorized set."""
    readable = _readable(await ctx.coverage(Permission.KNOWLEDGE_READ))
    if readable is None:
        return [], False
    statement = (
        select(KnowledgeSource, KnowledgeSourceVersion)
        .join(KnowledgeSourceVersion, KnowledgeSourceVersion.id == KnowledgeSource.active_version_id)
        .where(KnowledgeSource.status == SourceStatus.ACTIVE.value, readable)
    )
    if query:
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        statement = statement.where(
            or_(
                KnowledgeSource.title.ilike(pattern, escape="\\"),
                KnowledgeSource.description.ilike(pattern, escape="\\"),
            )
        )
    if tag:
        statement = statement.where(KnowledgeSource.tags.contains([tag]))
    if scope_type is not None:
        statement = statement.where(KnowledgeSource.scope_type == scope_type.value)
    rows = list(
        await ctx.session.execute(
            statement.order_by(KnowledgeSource.updated_at.desc(), KnowledgeSource.id)
            .limit(limit + 1)
            .offset(offset)
        )
    )
    return [(source, version) for source, version in rows[:limit]], len(rows) > limit


async def _load_source(
    ctx: KnowledgeContext, source_id: uuid.UUID, permission: Permission, *, lock: bool = False
) -> KnowledgeSource:
    statement = select(KnowledgeSource).where(
        KnowledgeSource.id == source_id, KnowledgeSource.status == SourceStatus.ACTIVE.value
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    source = await ctx.session.scalar(statement)
    if source is None:
        raise NotFoundError(SOURCE_NOT_FOUND)
    scope = _scope_fields(source)
    if not (await ctx.coverage(Permission.KNOWLEDGE_READ)).covers(**scope):
        # Indistinguishable from a missing source.
        await ctx.deny(
            "knowledge_scope_not_covered", NotFoundError(SOURCE_NOT_FOUND), source_id=source.id, **scope
        )
    if permission is not Permission.KNOWLEDGE_READ and not (
        await ctx.coverage(permission)
    ).covers(**scope):
        await ctx.deny(
            f"missing_{permission.value.replace(':', '_')}",
            PermissionDeniedError(
                "You do not have permission to change this knowledge source.",
                code="knowledge_forbidden",
            ),
            source_id=source.id,
            **scope,
        )
    return source


async def get_source(
    ctx: KnowledgeContext, source_id: uuid.UUID
) -> tuple[KnowledgeSource, KnowledgeSourceVersion]:
    source = await _load_source(ctx, source_id, Permission.KNOWLEDGE_READ)
    version = await ctx.session.get(KnowledgeSourceVersion, source.active_version_id)
    assert version is not None  # guaranteed by the composite foreign key
    return source, version


async def list_versions(
    ctx: KnowledgeContext, source_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[KnowledgeSource, list[KnowledgeSourceVersion], bool]:
    source = await _load_source(ctx, source_id, Permission.KNOWLEDGE_READ)
    rows = list(
        await ctx.session.scalars(
            select(KnowledgeSourceVersion)
            .where(KnowledgeSourceVersion.source_id == source.id)
            .order_by(KnowledgeSourceVersion.version_number.desc())
            .limit(limit + 1)
            .offset(offset)
        )
    )
    return source, rows[:limit], len(rows) > limit


async def get_version(
    ctx: KnowledgeContext, source_id: uuid.UUID, version_id: uuid.UUID
) -> tuple[KnowledgeSource, KnowledgeSourceVersion]:
    source = await _load_source(ctx, source_id, Permission.KNOWLEDGE_READ)
    version = await ctx.session.scalar(
        select(KnowledgeSourceVersion).where(
            KnowledgeSourceVersion.id == version_id, KnowledgeSourceVersion.source_id == source.id
        )
    )
    if version is None:
        raise NotFoundError(VERSION_NOT_FOUND)
    return source, version


async def download_path(
    ctx: KnowledgeContext, source_id: uuid.UUID, version_id: uuid.UUID
) -> tuple[KnowledgeSourceVersion, Path]:
    """Authorize, then return the verified private file. Unvalidated versions are not served."""
    _, version = await get_version(ctx, source_id, version_id)
    if version.validation_status != ValidationStatus.VALIDATED.value:
        raise NotFoundError(VERSION_NOT_FOUND)
    try:
        path = await asyncio.to_thread(
            ctx.storage.verified_path,
            version.storage_key,
            size=version.size_bytes,
            sha256=version.sha256,
        )
    except StoredObjectError as exc:
        logger.error(
            "Stored knowledge document unavailable",
            extra={"event": "knowledge_object_unavailable", "reason": str(exc)},
        )
        raise ServiceUnavailableError(
            "The stored document is unavailable.", code="knowledge_file_unavailable"
        ) from None
    return version, path


# ---------- uploads ----------


async def _receive(
    ctx: KnowledgeContext, upload: AsyncReader, filename: str, *, audit_scope: dict, source_id=None
) -> tuple[StagedFile, str]:
    """Stage and validate an upload. Rejections are audited and leave no stored bytes."""

    async def reject(reason: str, error: Exception) -> NoReturn:
        details: dict[str, object] = {"reason": reason, "filename": filename}
        if source_id is not None:
            details["source_id"] = str(source_id)
        await audit_service.record_standalone(
            ctx.session_factory,
            AuditEventType.KNOWLEDGE_VERSION_REJECTED,
            Outcome.FAILURE,
            actor_user_id=ctx.actor_id,
            details=details,
            **audit_scope,
        )
        raise error

    try:
        staged = await ctx.storage.stage(upload, ctx.settings.KNOWLEDGE_MAX_UPLOAD_BYTES)
    except UploadTooLarge:
        limit_mb = ctx.settings.KNOWLEDGE_MAX_UPLOAD_BYTES / (1024 * 1024)
        await reject(
            "file_too_large",
            PayloadTooLargeError(f"The file is larger than {limit_mb:g} MB.", code="file_too_large"),
        )
    except EmptyUpload:
        await reject("file_empty", BadRequestError("The file is empty.", code="file_empty"))
    try:
        media_type = await asyncio.wait_for(
            asyncio.to_thread(
                validate_document,
                filename,
                staged.path,
                staged.head,
                pdf_max_pages=ctx.settings.KNOWLEDGE_PDF_MAX_PAGES,
            ),
            timeout=VALIDATION_TIMEOUT_SECONDS,
        )
    except (FileRejected, TimeoutError) as exc:
        reason = exc.reason if isinstance(exc, FileRejected) else "validation_timeout"
        ctx.storage.discard(staged)
        await reject(reason, BadRequestError(REJECTION_MESSAGES[reason], code=f"file_{reason}"))
    return staged, media_type


async def _commit_with_object(
    ctx: KnowledgeContext, staged: StagedFile, key: str, write: Callable[[], Awaitable[None]]
) -> None:
    """Flush the rows, publish the file, then commit; undo the file if the commit fails."""
    try:
        await write()
        ctx.storage.publish(staged, key)
    except BaseException:
        ctx.storage.discard(staged)
        ctx.storage.delete_object(key)  # in case publishing got as far as creating it
        await ctx.session.rollback()
        raise
    try:
        await ctx.session.commit()
    except BaseException:
        ctx.storage.delete_object(key)
        await ctx.session.rollback()
        raise


def _new_version(
    ctx: KnowledgeContext,
    *,
    source_id: uuid.UUID,
    number: int,
    filename: str,
    media_type: str,
    staged: StagedFile,
    key: str,
    change_note: str | None,
) -> KnowledgeSourceVersion:
    # Validation ran synchronously before this point, so the version is born validated.
    return KnowledgeSourceVersion(
        id=uuid.uuid4(),
        source_id=source_id,
        version_number=number,
        original_filename=filename,
        media_type=media_type,
        size_bytes=staged.size,
        sha256=staged.sha256,
        storage_key=key,
        uploaded_by=ctx.actor_id,
        validation_status=ValidationStatus.VALIDATED.value,
        validated_at=datetime.now(UTC),
        change_note=change_note,
    )


def _record_version_events(
    ctx: KnowledgeContext, source: KnowledgeSource, version: KnowledgeSourceVersion, **extra
) -> None:
    details = {
        "source_id": str(source.id),
        "version_id": str(version.id),
        "version_number": version.version_number,
        "media_type": version.media_type,
        "size_bytes": version.size_bytes,
        "sha256": version.sha256,
        **extra,
    }
    for event in (
        AuditEventType.KNOWLEDGE_VERSION_UPLOADED,
        AuditEventType.KNOWLEDGE_VERSION_VALIDATED,
    ):
        audit_service.record(
            ctx.session,
            event,
            Outcome.SUCCESS,
            actor_user_id=ctx.actor_id,
            details=details,
            **_scope_fields(source),
        )


# ---------- registration ----------


async def _authorized_new_scope(
    ctx: KnowledgeContext, data: SourceRegistration
) -> dict[str, object]:
    """The validated scope columns for a new source, or a denial. Never substitutes a scope."""
    if data.scope_type is KnowledgeScope.GLOBAL:
        fields: dict[str, object] = {
            "scope_type": KnowledgeScope.GLOBAL.value,
            "btp_account_id": None,
            "btp_environment_id": None,
        }
        exists = True
    elif data.scope_type is KnowledgeScope.BTP_ACCOUNT:
        fields = {
            "scope_type": data.scope_type.value,
            "btp_account_id": data.scope_id,
            "btp_environment_id": None,
        }
        exists = await ctx.session.get(BTPAccount, data.scope_id) is not None
    else:
        fields = {
            "scope_type": data.scope_type.value,
            "btp_account_id": None,
            "btp_environment_id": data.scope_id,
        }
        exists = await ctx.session.get(BTPEnvironment, data.scope_id) is not None
    coverage = await ctx.coverage(Permission.KNOWLEDGE_CREATE)
    if not exists or not coverage.covers(**fields):
        if data.scope_type is KnowledgeScope.GLOBAL:
            await ctx.deny(
                "global_knowledge_not_permitted",
                PermissionDeniedError(
                    "You may not register global knowledge.", code="knowledge_forbidden"
                ),
                scope_type=KnowledgeScope.GLOBAL.value,
            )
        # Unknown and out-of-scope look the same.
        await ctx.deny(
            "knowledge_scope_not_covered" if exists else "knowledge_scope_not_found",
            NotFoundError(SCOPE_NOT_FOUND),
            **(fields if exists else {}),
        )
    return fields


async def register_source(
    ctx: KnowledgeContext, data: SourceRegistration, upload: AsyncReader, filename: str | None
) -> tuple[KnowledgeSource, KnowledgeSourceVersion]:
    scope = await _authorized_new_scope(ctx, data)
    await ctx.session.commit()  # end the read transaction before receiving the file
    display_name = clean_filename(filename)
    staged, media_type = await _receive(ctx, upload, display_name, audit_scope=scope)

    source_id, key = uuid.uuid4(), ctx.storage.new_key()
    version = _new_version(
        ctx,
        source_id=source_id,
        number=1,
        filename=display_name,
        media_type=media_type,
        staged=staged,
        key=key,
        change_note=data.change_note,
    )
    source = KnowledgeSource(
        id=source_id,
        title=data.title,
        description=data.description,
        tags=data.tags,
        owner_id=ctx.actor_id,
        created_by=ctx.actor_id,
        status=SourceStatus.ACTIVE.value,
        active_version_id=version.id,
        active_version_status=ValidationStatus.VALIDATED.value,
        **scope,
    )

    async def write() -> None:
        # Version first: its source_id foreign key is checked at commit (deferred), while
        # the source's active-version key is checked immediately.
        ctx.session.add(version)
        await ctx.session.flush()
        ctx.session.add(source)
        await ctx.session.flush()
        audit_service.record(
            ctx.session,
            AuditEventType.KNOWLEDGE_SOURCE_CREATED,
            Outcome.SUCCESS,
            actor_user_id=ctx.actor_id,
            details={"source_id": str(source.id), "title": source.title, "tags": source.tags},
            **scope,
        )
        _record_version_events(ctx, source, version)

    await _commit_with_object(ctx, staged, key, write)
    await _index_active_source(ctx, source.id)
    return source, version


# ---------- new versions ----------


async def upload_version(
    ctx: KnowledgeContext,
    source_id: uuid.UUID,
    upload: AsyncReader,
    filename: str | None,
    change_note: str | None,
) -> tuple[KnowledgeSource, KnowledgeSourceVersion]:
    source = await _load_source(ctx, source_id, Permission.KNOWLEDGE_UPDATE)
    scope = _scope_fields(source)
    await ctx.session.commit()  # end the read transaction before receiving the file
    display_name = clean_filename(filename)
    staged, media_type = await _receive(
        ctx, upload, display_name, audit_scope=scope, source_id=source.id
    )
    key = ctx.storage.new_key()
    created: list[KnowledgeSourceVersion] = []

    async def write() -> None:
        # The row lock serializes concurrent uploads and removal; the unique constraint on
        # (source_id, version_number) is the final guard.
        locked = await ctx.session.scalar(
            select(KnowledgeSource)
            .where(KnowledgeSource.id == source.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None or locked.status != SourceStatus.ACTIVE.value:
            raise NotFoundError(SOURCE_NOT_FOUND)
        previous = await ctx.session.get(KnowledgeSourceVersion, locked.active_version_id)
        if previous is not None and previous.sha256 == staged.sha256:
            raise ConflictError(
                "This file is identical to the active version.", code="content_unchanged"
            )
        latest = await ctx.session.scalar(
            select(func.max(KnowledgeSourceVersion.version_number)).where(
                KnowledgeSourceVersion.source_id == locked.id
            )
        )
        version = _new_version(
            ctx,
            source_id=locked.id,
            number=(latest or 0) + 1,
            filename=display_name,
            media_type=media_type,
            staged=staged,
            key=key,
            change_note=change_note,
        )
        ctx.session.add(version)
        await ctx.session.flush()
        locked.active_version_id = version.id
        locked.active_version_status = ValidationStatus.VALIDATED.value
        locked.revision += 1
        await ctx.session.flush()
        _record_version_events(
            ctx, locked, version, previous_version_id=str(previous.id) if previous else None
        )
        created.append(version)

    await _commit_with_object(ctx, staged, key, write)
    await ctx.session.refresh(source)
    await _index_active_source(ctx, source.id)
    return source, created[0]


async def _index_active_source(ctx: KnowledgeContext, source_id: uuid.UUID) -> None:
    """Index after the source/version commit; failed indexing never hides the upload."""
    from app.rag.indexing import IndexContext, index_active_source

    await index_active_source(
        IndexContext(
            session_factory=ctx.session_factory,
            principal=ctx.principal,
            storage=ctx.storage,
            settings=ctx.settings,
            embedding_client=ctx.embedding_client,
            redactor=ctx.redactor,
        ),
        source_id,
    )


# ---------- metadata and removal ----------


async def update_metadata(
    ctx: KnowledgeContext, source_id: uuid.UUID, data: SourceUpdate
) -> tuple[KnowledgeSource, KnowledgeSourceVersion]:
    source = await _load_source(ctx, source_id, Permission.KNOWLEDGE_UPDATE)
    changes = data.changes()
    updated = await ctx.session.scalar(
        update(KnowledgeSource)
        .where(
            KnowledgeSource.id == source.id,
            KnowledgeSource.revision == data.revision,
            KnowledgeSource.status == SourceStatus.ACTIVE.value,
        )
        .values(**changes, revision=KnowledgeSource.revision + 1, updated_at=func.now())
        .returning(KnowledgeSource.revision)
    )
    if updated is None:
        await ctx.session.rollback()
        raise ConflictError(
            "This knowledge source was changed by someone else. Reload it and try again.",
            code="revision_conflict",
        )
    audit_service.record(
        ctx.session,
        AuditEventType.KNOWLEDGE_SOURCE_UPDATED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        details={"source_id": str(source.id), "fields": sorted(changes), "revision": updated},
        **_scope_fields(source),
    )
    await ctx.session.commit()
    await ctx.session.refresh(source)
    version = await ctx.session.get(KnowledgeSourceVersion, source.active_version_id)
    assert version is not None
    return source, version


async def remove_source(
    ctx: KnowledgeContext, source_id: uuid.UUID, data: SourceRemoval
) -> KnowledgeSource:
    """Soft removal: hidden from listing, reads, downloads and uploads; files are kept."""
    source = await _load_source(ctx, source_id, Permission.KNOWLEDGE_REMOVE, lock=True)
    source.status = SourceStatus.REMOVED.value
    source.removed_at = datetime.now(UTC)
    source.removed_by = ctx.actor_id
    source.removal_reason = data.reason
    source.revision += 1
    await ctx.session.flush()
    audit_service.record(
        ctx.session,
        AuditEventType.KNOWLEDGE_SOURCE_REMOVED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        details={"source_id": str(source.id), "reason": data.reason},
        **_scope_fields(source),
    )
    await ctx.session.commit()
    return source


# ---------- operations ----------


async def reconcile_storage(
    session: AsyncSession, storage: KnowledgeStorage, *, delete: bool, min_age_seconds: float
) -> dict[str, list[str]]:
    """Compare stored files with version rows (CLI only). Never touches referenced files."""
    referenced = {
        key: (size, sha)
        for key, size, sha in await session.execute(
            select(
                KnowledgeSourceVersion.storage_key,
                KnowledgeSourceVersion.size_bytes,
                KnowledgeSourceVersion.sha256,
            )
        )
    }
    on_disk = set(storage.object_keys())
    orphans = sorted(
        key for key in on_disk - referenced.keys() if storage.object_age_seconds(key) >= min_age_seconds
    )
    damaged = []
    for key, (size, sha) in referenced.items():
        try:
            storage.verified_path(key, size=size, sha256=sha)
        except StoredObjectError:
            damaged.append(key)
    stale = storage.stale_staging(min_age_seconds)
    if delete:
        for key in orphans:
            storage.delete_object(key)
        for path in stale:
            path.unlink(missing_ok=True)
    return {
        "orphaned_objects": orphans,
        "missing_or_damaged_versions": sorted(damaged),
        "stale_staging_files": sorted(p.name for p in stale),
    }

