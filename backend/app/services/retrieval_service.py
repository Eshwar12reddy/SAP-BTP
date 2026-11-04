"""Authorized hybrid knowledge retrieval with lexical fallback and verified citations."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Principal, knowledge_coverage_for
from app.auth.permissions import Permission
from app.core.config import Settings
from app.integrations.openai_embeddings import (
    EmbeddingsError,
    EmbeddingsNotConfigured,
    OpenAIEmbeddingsClient,
)
from app.models import (
    KnowledgeScope,
    KnowledgeSource,
    KnowledgeSourceVersion,
    SourceStatus,
    ValidationStatus,
)
from app.models.knowledge_index import (
    IndexStatus,
    KnowledgeChildChunk,
    KnowledgeIndexGeneration,
    KnowledgeParentChunk,
)
from app.rag.chunking import _encoding
from app.safety.redaction import Redactor
from app.schemas.retrieval import KnowledgeCitation, KnowledgeEvidence, KnowledgeSearchResponse


@dataclass
class _Candidate:
    child: KnowledgeChildChunk
    parent: KnowledgeParentChunk
    source: KnowledgeSource
    version: KnowledgeSourceVersion
    lexical_rank: int | None = None
    vector_rank: int | None = None
    score: float = 0.0


def _authorized_source_condition(coverage):
    conditions = []
    if coverage.global_scope:
        conditions.append(KnowledgeSource.scope_type == KnowledgeScope.GLOBAL.value)
    if coverage.btp.account_ids:
        conditions.append(and_(KnowledgeSource.scope_type == KnowledgeScope.BTP_ACCOUNT.value,
                               KnowledgeSource.btp_account_id.in_(coverage.btp.account_ids)))
    if coverage.btp.environment_ids:
        conditions.append(and_(KnowledgeSource.scope_type == KnowledgeScope.BTP_ENVIRONMENT.value,
                               KnowledgeSource.btp_environment_id.in_(coverage.btp.environment_ids)))
    return or_(*conditions) if conditions else None


def _base_statement(authorized, tsquery, *, semantic: bool = False, vector: list[float] | None = None,
                   embedding_model: str = "text-embedding-3-small", embedding_dimensions: int = 1536):
    cols = [KnowledgeChildChunk, KnowledgeParentChunk, KnowledgeSource, KnowledgeSourceVersion]
    if semantic and vector is not None:
        distance = KnowledgeChildChunk.embedding.cosine_distance(vector)
        cols.append(distance.label("distance"))
    else:
        rank = func.ts_rank_cd(KnowledgeChildChunk.search_vector, tsquery)
        cols.append(rank.label("lexical_score"))
    statement = (
        select(*cols)
        .join(KnowledgeIndexGeneration, and_(KnowledgeIndexGeneration.id == KnowledgeChildChunk.generation_id,
                                             KnowledgeIndexGeneration.status == IndexStatus.PUBLISHED.value))
        .join(KnowledgeParentChunk, and_(KnowledgeParentChunk.id == KnowledgeChildChunk.parent_id,
                                         KnowledgeParentChunk.generation_id == KnowledgeChildChunk.generation_id))
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChildChunk.source_id)
        .join(KnowledgeSourceVersion, and_(KnowledgeSourceVersion.id == KnowledgeChildChunk.source_version_id,
                                           KnowledgeSourceVersion.source_id == KnowledgeSource.id))
        .where(authorized, KnowledgeSource.status == SourceStatus.ACTIVE.value,
               KnowledgeSource.active_version_id == KnowledgeChildChunk.source_version_id,
               KnowledgeSource.active_version_status == ValidationStatus.VALIDATED.value,
               KnowledgeSourceVersion.validation_status == ValidationStatus.VALIDATED.value,
               KnowledgeIndexGeneration.source_id == KnowledgeSource.id,
               KnowledgeIndexGeneration.source_version_id == KnowledgeSource.active_version_id)
    )
    if semantic and vector is not None:
        statement = statement.where(KnowledgeIndexGeneration.embedding_status == "ready",
                                    KnowledgeIndexGeneration.embedding_model_id == embedding_model,
                                    KnowledgeIndexGeneration.embedding_dimensions == embedding_dimensions,
                                    KnowledgeChildChunk.embedding.is_not(None))
    else:
        statement = statement.where(KnowledgeChildChunk.search_vector.op("@@")(tsquery))
    return statement


async def search_knowledge(
    session: AsyncSession, principal: Principal, settings: Settings, query: str, *,
    max_results: int = 6, embedding_client: OpenAIEmbeddingsClient | None = None,
    redactor: Redactor | None = None,
) -> KnowledgeSearchResponse:
    """Search only currently authorized source versions; remote embedding is opt-in."""
    coverage = await knowledge_coverage_for(session, principal, Permission.KNOWLEDGE_READ)
    authorized = _authorized_source_condition(coverage)
    requested = min(max_results, settings.KNOWLEDGE_FINAL_CHILD_TOP_K, 8)
    if authorized is None:
        return _empty(query, "no_accessible_sources", "no_accessible_sources")

    semantic_status = "disabled_by_configuration"
    query_vector = None
    if settings.OPENAI_EMBEDDINGS_ENABLED:
        if embedding_client is None or not embedding_client.configured:
            semantic_status = "openai_not_configured"
        else:
            safe_query = (redactor or Redactor.from_settings(settings)).redact_text(query)
            try:
                vectors = await embedding_client.embed_texts([safe_query])
                query_vector = vectors[0]
                semantic_status = "available"
            except EmbeddingsNotConfigured:
                semantic_status = "openai_not_configured"
            except EmbeddingsError:
                semantic_status = "openai_unavailable"

    timeout_ms = int(settings.KNOWLEDGE_SEARCH_TIMEOUT_SECONDS * 1000)
    await session.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
    tsquery = func.websearch_to_tsquery("english", query)
    lexical_stmt = _base_statement(authorized, tsquery).order_by(
        func.ts_rank_cd(KnowledgeChildChunk.search_vector, tsquery).desc(), KnowledgeChildChunk.id
    ).limit(settings.KNOWLEDGE_LEXICAL_TOP_K)
    lexical_rows = list(await asyncio.wait_for(session.execute(lexical_stmt), timeout=settings.KNOWLEDGE_SEARCH_TIMEOUT_SECONDS))
    candidates: dict[Any, _Candidate] = {}
    for rank, row in enumerate(lexical_rows, 1):
        child, parent, source, version, _ = row
        item = _Candidate(child, parent, source, version, lexical_rank=rank)
        candidates[child.id] = item

    if query_vector is not None:
        # Check extension availability without attempting any schema change.
        has_vector = bool(await session.scalar(select(func.count()).select_from(text("pg_extension")).where(text("extname = 'vector'"))))
        if not has_vector:
            semantic_status = "pgvector_unavailable"
        else:
            try:
                vector_stmt = _base_statement(authorized, tsquery, semantic=True, vector=query_vector,
                                              embedding_model=settings.OPENAI_EMBEDDING_MODEL,
                                              embedding_dimensions=settings.OPENAI_EMBEDDING_DIMENSIONS)
                vector_stmt = vector_stmt.order_by(KnowledgeChildChunk.embedding.cosine_distance(query_vector),
                                                   KnowledgeChildChunk.id).limit(settings.KNOWLEDGE_VECTOR_TOP_K)
                vector_rows = list(await asyncio.wait_for(session.execute(vector_stmt), timeout=settings.KNOWLEDGE_SEARCH_TIMEOUT_SECONDS))
                for rank, row in enumerate(vector_rows, 1):
                    child, parent, source, version, _distance = row
                    item = candidates.get(child.id)
                    if item is None:
                        item = _Candidate(child, parent, source, version)
                        candidates[child.id] = item
                    item.vector_rank = rank
                if not vector_rows:
                    mismatched = await session.scalar(
                        select(func.count()).select_from(KnowledgeIndexGeneration)
                        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeIndexGeneration.source_id)
                        .where(authorized, KnowledgeSource.status == SourceStatus.ACTIVE.value,
                               KnowledgeSource.active_version_id == KnowledgeIndexGeneration.source_version_id,
                               KnowledgeIndexGeneration.status == IndexStatus.PUBLISHED.value,
                               KnowledgeIndexGeneration.embedding_status == "ready",
                               or_(KnowledgeIndexGeneration.embedding_model_id != settings.OPENAI_EMBEDDING_MODEL,
                                   KnowledgeIndexGeneration.embedding_dimensions != settings.OPENAI_EMBEDDING_DIMENSIONS))
                    )
                    if mismatched:
                        semantic_status = "model_mismatch"
            except (SQLAlchemyError, TimeoutError):
                # Semantic retrieval is optional; a database error must not suppress lexical search.
                await session.rollback()
                semantic_status = "pgvector_unavailable"
                await session.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
                lexical_rows = list(await asyncio.wait_for(
                    session.execute(lexical_stmt), timeout=settings.KNOWLEDGE_SEARCH_TIMEOUT_SECONDS
                ))
                candidates = {}
                for rank, row in enumerate(lexical_rows, 1):
                    child, parent, source, version, _ = row
                    candidates[child.id] = _Candidate(child, parent, source, version, lexical_rank=rank)

    constant = settings.KNOWLEDGE_FUSION_CONSTANT
    for item in candidates.values():
        if item.lexical_rank is not None:
            item.score += 1.0 / (constant + item.lexical_rank)
        if item.vector_rank is not None:
            item.score += 1.0 / (constant + item.vector_rank)
    ordered = sorted(candidates.values(), key=lambda item: (-item.score, str(item.child.id)))
    selected: list[_Candidate] = []
    per_source: defaultdict[str, int] = defaultdict(int)
    for item in ordered:
        key = str(item.source.id)
        if per_source[key] >= 2:
            continue
        selected.append(item)
        per_source[key] += 1
        if len(selected) >= requested:
            break
    if not selected:
        status = await _index_availability(session, authorized)
        return _empty(query, status, semantic_status)

    encoding = _encoding(settings.KNOWLEDGE_TOKENIZER_ENCODING)
    parent_seen: set[Any] = set()
    evidence: list[KnowledgeEvidence] = []
    budget_left = settings.KNOWLEDGE_EVIDENCE_BUDGET_TOKENS
    for position, item in enumerate(selected, 1):
        parent_context = None
        if item.parent.id not in parent_seen and len(parent_seen) < settings.KNOWLEDGE_EXPANDED_PARENT_TOP_K and budget_left:
            tokens = encoding.encode(item.parent.chunk_text, disallowed_special=())
            used = min(len(tokens), budget_left)
            parent_context = encoding.decode(tokens[:used]).strip()
            budget_left -= used
            parent_seen.add(item.parent.id)
        citation = KnowledgeCitation(
            citation_id=f"c{position}", source_id=item.source.id, source_title=item.source.title,
            source_version_id=item.version.id, version_number=item.version.version_number,
            page_start=item.parent.page_start, page_end=item.parent.page_end,
            section_heading=item.parent.section_heading, parent_id=item.parent.id, child_id=item.child.id,
            download_url=f"/api/v1/knowledge/sources/{item.source.id}/versions/{item.version.id}/download",
        )
        evidence.append(KnowledgeEvidence(citation=citation, excerpt=item.child.chunk_text,
                                          parent_context=parent_context, lexical_rank=item.lexical_rank,
                                          vector_rank=item.vector_rank, fused_score=item.score))
    has_vector_results = any(item.vector_rank is not None for item in selected)
    mode = "hybrid" if has_vector_results else ("lexical_fallback" if settings.OPENAI_EMBEDDINGS_ENABLED else "lexical")
    return KnowledgeSearchResponse(query=query, retrieval_mode=mode, semantic_status=semantic_status,
                                   indexing_status="ready", items=evidence)


async def _index_availability(session: AsyncSession, authorized: Any) -> str:
    current = select(KnowledgeIndexGeneration.id).where(
        KnowledgeIndexGeneration.source_id == KnowledgeSource.id,
        KnowledgeIndexGeneration.source_version_id == KnowledgeSource.active_version_id,
        KnowledgeIndexGeneration.status == IndexStatus.PUBLISHED.value).exists()
    available = await session.scalar(select(func.count()).select_from(KnowledgeSource).where(
        authorized, KnowledgeSource.status == SourceStatus.ACTIVE.value,
        KnowledgeSource.active_version_status == ValidationStatus.VALIDATED.value, current))
    return "no_matching_evidence" if available else "index_missing"


def _empty(query: str, status: str, semantic_status: str = "disabled_by_configuration") -> KnowledgeSearchResponse:
    return KnowledgeSearchResponse(query=query, retrieval_mode="lexical_no_match",
                                   semantic_status=semantic_status, indexing_status=status, items=[])
