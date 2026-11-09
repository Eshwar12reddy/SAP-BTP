"""Synchronous, transactionally published lexical indexes for active versions."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.authorization import Principal, knowledge_coverage_for
from app.auth.permissions import Permission
from app.core.config import Settings
from app.integrations.openai_embeddings import EmbeddingsError, OpenAIEmbeddingsClient
from app.knowledge.storage import KnowledgeStorage, StoredObjectError
from app.models import KnowledgeSource, KnowledgeSourceVersion, SourceStatus, ValidationStatus
from app.models.knowledge_index import (
    IndexStatus,
    KnowledgeChildChunk,
    KnowledgeIndexGeneration,
    KnowledgeParentChunk,
)
from app.rag.chunking import ChunkConfig, ChunkedParent, _encoding, chunk_sections
from app.rag.extraction import ExtractionError, extract_document
from app.safety.redaction import Redactor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexResult:
    status: str
    generation_id: uuid.UUID | None
    parent_count: int = 0
    child_count: int = 0
    failure_code: str | None = None


@dataclass(frozen=True)
class IndexArtifacts:
    config_version: str
    parents: list[ChunkedParent]
    embeddings: list[list[float]] | None
    embedding_status: str
    embedding_model_id: str | None
    embedding_dimensions: int | None


class IndexPreparationError(ValueError):
    """Safe, stable failure raised before a new version or index is committed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code if code.isascii() and len(code) <= 64 else "indexing_failed"


@dataclass(frozen=True)
class IndexContext:
    session_factory: async_sessionmaker[AsyncSession]
    principal: Principal
    storage: KnowledgeStorage
    settings: Settings
    embedding_client: OpenAIEmbeddingsClient | None = None
    redactor: Redactor | None = None


def _chunk_config(settings: Settings) -> ChunkConfig:
    config = ChunkConfig(
        parent_tokens=settings.KNOWLEDGE_PARENT_TOKENS,
        child_tokens=settings.KNOWLEDGE_CHILD_TOKENS,
        child_overlap=settings.KNOWLEDGE_CHILD_OVERLAP_TOKENS,
        tokenizer_name=settings.KNOWLEDGE_TOKENIZER_ENCODING,
        version=settings.KNOWLEDGE_INDEX_CONFIG_VERSION,
    )
    if getattr(_encoding(settings.KNOWLEDGE_TOKENIZER_ENCODING), "approximate", False):
        return replace(config, version=f"{config.version}-character-fallback")
    return config


async def index_active_source(ctx: IndexContext, source_id: uuid.UUID) -> IndexResult:
    """Extract and atomically publish one source's current version under a database lock."""
    version_id: uuid.UUID | None = None
    scope: dict[str, object] = {}
    try:
        async with asyncio.timeout(ctx.settings.KNOWLEDGE_INDEX_TIMEOUT_SECONDS):
            async with ctx.session_factory() as session:
                source = await session.scalar(
                    select(KnowledgeSource)
                    .where(KnowledgeSource.id == source_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if source is None or source.status != SourceStatus.ACTIVE.value:
                    return IndexResult("unavailable", None, failure_code="source_unavailable")
                scope = {
                    "scope_type": source.scope_type,
                    "btp_account_id": source.btp_account_id,
                    "btp_environment_id": source.btp_environment_id,
                }
                coverage = await knowledge_coverage_for(session, ctx.principal, Permission.KNOWLEDGE_READ)
                if not coverage.covers(**scope):
                    return IndexResult("unavailable", None, failure_code="source_unavailable")
                version_id = source.active_version_id
                version = await session.get(KnowledgeSourceVersion, version_id)
                if version is None or version.validation_status != ValidationStatus.VALIDATED.value:
                    return IndexResult("unavailable", None, failure_code="active_version_unavailable")
                chunk_config = _chunk_config(ctx.settings)
                current = await session.scalar(
                    select(KnowledgeIndexGeneration).where(
                        KnowledgeIndexGeneration.source_id == source.id,
                        KnowledgeIndexGeneration.source_version_id == version.id,
                        KnowledgeIndexGeneration.config_version == chunk_config.version,
                        KnowledgeIndexGeneration.status == IndexStatus.PUBLISHED.value,
                        KnowledgeIndexGeneration.embedding_status == (
                            "ready" if ctx.settings.OPENAI_EMBEDDINGS_ENABLED else "disabled"
                        ),
                        KnowledgeIndexGeneration.embedding_model_id == (
                            ctx.settings.OPENAI_EMBEDDING_MODEL if ctx.settings.OPENAI_EMBEDDINGS_ENABLED else None
                        ),
                        KnowledgeIndexGeneration.embedding_dimensions == (
                            ctx.settings.OPENAI_EMBEDDING_DIMENSIONS if ctx.settings.OPENAI_EMBEDDINGS_ENABLED else None
                        ),
                    )
                )
                if current is not None:
                    return IndexResult("published", current.id, current.parent_count, current.child_count)
                try:
                    path = await asyncio.to_thread(
                        ctx.storage.verified_path,
                        version.storage_key,
                        size=version.size_bytes,
                        sha256=version.sha256,
                    )
                except StoredObjectError:
                    failure_code = "source_file_unavailable"
                    await session.rollback()
                    return await _record_failure(ctx, source_id, version_id, failure_code)
                try:
                    artifacts = await prepare_index_artifacts(ctx, path, version.media_type)
                except IndexPreparationError as exc:
                    failure_code = exc.code
                    await session.rollback()
                    return await _record_failure(ctx, source_id, version_id, failure_code)
                generation = await publish_index_artifacts(session, source, version, artifacts)
                await session.commit()
                return IndexResult(
                    "published",
                    generation.id,
                    generation.parent_count,
                    generation.child_count,
                )
    except TimeoutError:
        return await _record_failure(ctx, source_id, version_id, "indexing_timeout")
    except IndexPreparationError as exc:
        return await _record_failure(ctx, source_id, version_id, exc.code)
    except Exception:
        logger.exception(
            "Knowledge indexing failed",
            extra={"event": "knowledge_index_failed", "reason": "indexing_failed"},
        )
        return await _record_failure(ctx, source_id, version_id, "indexing_failed")


def _extract_and_chunk(
    path: Path, media_type: str, settings: Settings, config: ChunkConfig
) -> list[ChunkedParent]:
    sections = extract_document(
        path,
        media_type,
        max_bytes=settings.KNOWLEDGE_MAX_UPLOAD_BYTES,
        max_pages=settings.KNOWLEDGE_PDF_MAX_PAGES,
        max_characters=settings.KNOWLEDGE_INDEX_MAX_CHARACTERS,
    )
    parents = chunk_sections(sections, config, max_chunks=settings.KNOWLEDGE_INDEX_MAX_CHUNKS)
    if not parents:
        raise ExtractionError("insufficient_extractable_text")
    return parents


async def prepare_index_artifacts(
    ctx: IndexContext, path: Path, media_type: str
) -> IndexArtifacts:
    """Prepare extraction and embeddings without changing database state."""
    config = _chunk_config(ctx.settings)
    try:
        parents = await asyncio.to_thread(
            _extract_and_chunk, path, media_type, ctx.settings, config
        )
    except ExtractionError as exc:
        raise IndexPreparationError(exc.code) from None
    except ValueError:
        raise IndexPreparationError("indexing_failed") from None

    if not ctx.settings.OPENAI_EMBEDDINGS_ENABLED:
        return IndexArtifacts(config.version, parents, None, "disabled", None, None)

    children = [child for parent in parents for child in parent.children]
    redactor = ctx.redactor or Redactor.from_settings(ctx.settings)
    try:
        if ctx.embedding_client is None:
            raise EmbeddingsError("openai_embeddings_unavailable")
        embeddings = await ctx.embedding_client.embed_texts(
            [redactor.redact_text(child.text) for child in children]
        )
    except EmbeddingsError as exc:
        code = str(exc)[:64]
        raise IndexPreparationError(code) from None
    if len(embeddings) != len(children):
        raise IndexPreparationError("embedding_count_mismatch")
    if any(len(vector) != ctx.settings.OPENAI_EMBEDDING_DIMENSIONS for vector in embeddings):
        raise IndexPreparationError("embedding_dimension_mismatch")
    return IndexArtifacts(
        config.version,
        parents,
        embeddings,
        "ready",
        ctx.settings.OPENAI_EMBEDDING_MODEL,
        ctx.settings.OPENAI_EMBEDDING_DIMENSIONS,
    )


async def publish_index_artifacts(
    session: AsyncSession,
    source: KnowledgeSource,
    version: KnowledgeSourceVersion,
    artifacts: IndexArtifacts,
) -> KnowledgeIndexGeneration:
    """Persist one complete generation in the caller's transaction.

    The new generation is staged as retired while its chunks are inserted. The previous
    published generation is retired only after preparation has succeeded, and the new one
    becomes published before the caller commits. A rollback therefore preserves the old
    active version and generation together.
    """
    parents = artifacts.parents
    generation = KnowledgeIndexGeneration(
        source_id=source.id,
        source_version_id=version.id,
        version_validation_status=ValidationStatus.VALIDATED.value,
        status=IndexStatus.RETIRED.value,
        config_version=artifacts.config_version,
        parent_count=len(parents),
        child_count=sum(len(parent.children) for parent in parents),
        embedding_model_id=artifacts.embedding_model_id,
        embedding_dimensions=artifacts.embedding_dimensions,
        embedding_status=artifacts.embedding_status,
    )
    session.add(generation)
    await session.flush()
    embedding_index = 0
    for parent_chunk in parents:
        parent = KnowledgeParentChunk(
            generation_id=generation.id,
            source_id=source.id,
            source_version_id=version.id,
            parent_order=parent_chunk.order,
            section_heading=parent_chunk.heading,
            page_start=parent_chunk.page_start,
            page_end=parent_chunk.page_end,
            source_offset_start=parent_chunk.source_offset_start,
            source_offset_end=parent_chunk.source_offset_end,
            chunk_text=parent_chunk.text,
            token_count=parent_chunk.token_count,
            fingerprint=parent_chunk.fingerprint,
        )
        session.add(parent)
        await session.flush()
        children_to_add = []
        for child in parent_chunk.children:
            vector = artifacts.embeddings[embedding_index] if artifacts.embeddings else None
            embedding_index += 1
            children_to_add.append(
                KnowledgeChildChunk(
                    generation_id=generation.id,
                    parent_id=parent.id,
                    source_id=source.id,
                    source_version_id=version.id,
                    child_order=child.order,
                    source_offset_start=child.source_offset_start,
                    source_offset_end=child.source_offset_end,
                    chunk_text=child.text,
                    token_count=child.token_count,
                    fingerprint=child.fingerprint,
                    embedding=vector,
                )
            )
        session.add_all(children_to_add)
    await session.flush()
    await session.execute(
        update(KnowledgeIndexGeneration)
        .where(
            KnowledgeIndexGeneration.source_id == source.id,
            KnowledgeIndexGeneration.status == IndexStatus.PUBLISHED.value,
        )
        .values(status=IndexStatus.RETIRED.value)
    )
    generation.status = IndexStatus.PUBLISHED.value
    generation.published_at = datetime.now(UTC)
    await session.flush()
    return generation


async def _record_failure(
    ctx: IndexContext,
    source_id: uuid.UUID,
    version_id: uuid.UUID | None,
    failure_code: str,
) -> IndexResult:
    if version_id is None:
        return IndexResult("failed", None, failure_code=failure_code)
    try:
        async with ctx.session_factory() as session:
            source = await session.scalar(
                select(KnowledgeSource)
                .where(KnowledgeSource.id == source_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if source is None or source.status != SourceStatus.ACTIVE.value or source.active_version_id != version_id:
                await session.rollback()
                return IndexResult("unavailable", None, failure_code="source_unavailable")
            generation = KnowledgeIndexGeneration(
                source_id=source_id,
                source_version_id=version_id,
                version_validation_status=ValidationStatus.VALIDATED.value,
                status=IndexStatus.FAILED.value,
                config_version=_chunk_config(ctx.settings).version,
                embedding_model_id=(
                    ctx.settings.OPENAI_EMBEDDING_MODEL
                    if ctx.settings.OPENAI_EMBEDDINGS_ENABLED
                    else None
                ),
                embedding_dimensions=(
                    ctx.settings.OPENAI_EMBEDDING_DIMENSIONS
                    if ctx.settings.OPENAI_EMBEDDINGS_ENABLED
                    else None
                ),
                embedding_status=("failed" if ctx.settings.OPENAI_EMBEDDINGS_ENABLED else "disabled"),
                failure_code=failure_code,
            )
            session.add(generation)
            await session.commit()
            return IndexResult("failed", generation.id, failure_code=failure_code)
    except Exception:
        logger.exception(
            "Could not persist knowledge indexing failure",
            extra={"event": "knowledge_index_failure_record_failed", "reason": failure_code},
        )
        return IndexResult("failed", None, failure_code="failure_status_unavailable")
