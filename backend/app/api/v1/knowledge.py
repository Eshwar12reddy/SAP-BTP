"""Knowledge source management endpoints.

Every route needs the coarse knowledge permission (any active grant) and the service then
checks the source's own scope. Sources outside the caller's scope, removed sources and
unknown ids are all 404. Uploads are multipart with exactly one ``file`` part; any field not
listed for the endpoint is rejected, so identity fields cannot be smuggled in. Responses
never contain storage keys or filesystem paths.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile

from app.auth.authorization import Principal
from app.auth.dependencies import SessionDep, require_permission
from app.auth.permissions import Permission
from app.core.exceptions import error_body
from app.models import KnowledgeScope, KnowledgeSource, KnowledgeSourceVersion
from app.models.knowledge import MAX_TAGS
from app.rag.indexing import IndexContext, index_active_source
from app.schemas.knowledge import (
    KnowledgeRemovalRead,
    KnowledgeSourcePage,
    KnowledgeSourceRead,
    KnowledgeVersionPage,
    KnowledgeVersionRead,
    SourceRegistration,
    SourceRemoval,
    SourceUpdate,
    VersionUpload,
)
from app.schemas.retrieval import (
    KnowledgeIndexRead,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.services import knowledge_service
from app.services.knowledge_service import KnowledgeContext
from app.services.retrieval_service import search_knowledge

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

Reader = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_READ))]
Creator = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_CREATE))]
Updater = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_UPDATE))]
Remover = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_REMOVE))]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=10_000)]

_REGISTRATION_FIELDS = frozenset(SourceRegistration.model_fields)
_VERSION_FIELDS = frozenset(VersionUpload.model_fields)
_LIST_FIELDS = frozenset({"tags"})

_ERROR = {"content": {"application/json": {"example": error_body("not_found", "Not found.")}}}
_RESPONSES = {
    401: {"description": "Not authenticated", **_ERROR},
    403: {"description": "Missing knowledge permission for this scope", **_ERROR},
    404: {"description": "Unknown, removed or out-of-scope source", **_ERROR},
}
_UPLOAD_RESPONSES = {
    **_RESPONSES,
    400: {"description": "Rejected file (file_* codes)", **_ERROR},
    413: {"description": "File larger than KNOWLEDGE_MAX_UPLOAD_BYTES", **_ERROR},
}


def _multipart_doc(fields: dict[str, dict]) -> dict:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}, **fields},
                    }
                }
            },
        }
    }


def _context(request: Request, session: SessionDep, principal: Principal) -> KnowledgeContext:
    return KnowledgeContext(
        session=session,
        session_factory=request.app.state.session_factory,
        principal=principal,
        storage=request.app.state.knowledge_storage,
        settings=request.app.state.settings,
        embedding_client=request.app.state.openai_embeddings,
        redactor=request.app.state.redactor,
    )


def _invalid(errors: list[dict]) -> RequestValidationError:
    return RequestValidationError(errors)


async def _upload_form(request: Request, allowed: frozenset[str]) -> tuple[dict, UploadFile]:
    """Strictly parse a multipart upload: one ``file`` plus only the allowed text fields."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise _invalid([_error("body", "Send multipart/form-data with a 'file' field.", "type_error")])
    form = await request.form(
        max_files=1, max_fields=len(allowed) + MAX_TAGS + 1, max_part_size=16 * 1024
    )
    errors = [
        _error(key, "Unexpected field.", "extra_forbidden")
        for key in sorted(set(form.keys()) - allowed - {"file"})[:10]
    ]
    files = form.getlist("file")
    if len(files) != 1 or not isinstance(files[0], UploadFile):
        errors.append(_error("file", "Exactly one file is required.", "missing"))
    values: dict[str, object] = {}
    for key in allowed:
        items = form.getlist(key)
        if any(not isinstance(item, str) for item in items):
            errors.append(_error(key, "Must be text.", "string_type"))
        elif key in _LIST_FIELDS:
            values[key] = items
        elif len(items) > 1:
            errors.append(_error(key, "Provide this field once.", "duplicate"))
        elif items and items[0].strip():
            values[key] = items[0]
    if errors:
        raise _invalid(errors)
    return values, files[0]  # type: ignore[return-value]


def _error(field: str, message: str, kind: str) -> dict:
    return {"loc": ("body", field) if field != "body" else ("body",), "msg": message, "type": kind}


def _validated[M: BaseModel](model: type[M], values: dict) -> M:
    try:
        return model(**values)
    except ValidationError as exc:
        raise _invalid(
            [
                {**error, "loc": ("body", *error["loc"])}
                for error in exc.errors(include_input=False, include_url=False, include_context=False)
            ]
        ) from None


def _version_read(version: KnowledgeSourceVersion, active_id: uuid.UUID) -> KnowledgeVersionRead:
    return KnowledgeVersionRead.model_validate(version).model_copy(
        update={"is_active": version.id == active_id}
    )


def _source_read(source: KnowledgeSource, active: KnowledgeSourceVersion) -> KnowledgeSourceRead:
    fields = KnowledgeSourceRead.model_fields.keys() - {"active_version"}
    return KnowledgeSourceRead.model_validate(
        {name: getattr(source, name) for name in fields}
        | {"active_version": _version_read(active, source.active_version_id)}
    )


@router.post("/search", response_model=KnowledgeSearchResponse, responses=_RESPONSES)
async def search_documents(
    payload: KnowledgeSearchRequest, request: Request, session: SessionDep, principal: Reader
) -> KnowledgeSearchResponse:
    """Search authorized current document chunks and return database-verified citations."""
    return await search_knowledge(
        session, principal, request.app.state.settings, payload.query,
        max_results=payload.max_results,
        embedding_client=request.app.state.openai_embeddings,
        redactor=request.app.state.redactor,
    )


@router.post("/sources/{source_id}/index", response_model=KnowledgeIndexRead, responses=_RESPONSES)
async def reindex_source(
    source_id: uuid.UUID, request: Request, session: SessionDep, principal: Updater
) -> KnowledgeIndexRead:
    """Rebuild the derived index for an authorized active source version."""
    ctx = _context(request, session, principal)
    await knowledge_service.get_source(ctx, source_id)
    result = await index_active_source(
        IndexContext(ctx.session_factory, principal, ctx.storage, ctx.settings,
                     ctx.embedding_client, ctx.redactor),
        source_id,
    )
    return KnowledgeIndexRead(
        status=result.status, generation_id=result.generation_id,
        parent_count=result.parent_count, child_count=result.child_count,
        failure_code=result.failure_code,
    )


@router.post(
    "/sources",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeSourceRead,
    responses=_UPLOAD_RESPONSES,
    openapi_extra=_multipart_doc(
        {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "scope_type": {"type": "string", "enum": [s.value for s in KnowledgeScope]},
            "scope_id": {"type": "string", "format": "uuid"},
            "change_note": {"type": "string"},
        }
    ),
)
async def register_source(
    request: Request, session: SessionDep, principal: Creator
) -> KnowledgeSourceRead:
    """Register a knowledge source together with its first document (version 1)."""
    values, upload = await _upload_form(request, _REGISTRATION_FIELDS)
    data = _validated(SourceRegistration, values)
    ctx = _context(request, session, principal)
    source, version = await knowledge_service.register_source(ctx, data, upload, upload.filename)
    return _source_read(source, version)


@router.get("/sources", response_model=KnowledgeSourcePage, responses=_RESPONSES)
async def list_sources(
    request: Request,
    session: SessionDep,
    principal: Reader,
    q: Annotated[str | None, Query(max_length=100, description="Title/description search")] = None,
    tag: Annotated[str | None, Query(max_length=50)] = None,
    scope_type: KnowledgeScope | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> KnowledgeSourcePage:
    """Active sources the caller can read, most recently updated first."""
    ctx = _context(request, session, principal)
    rows, has_more = await knowledge_service.list_sources(
        ctx, limit=limit, offset=offset, query=q, tag=tag, scope_type=scope_type
    )
    return KnowledgeSourcePage(items=[_source_read(s, v) for s, v in rows], has_more=has_more)


@router.get("/sources/{source_id}", response_model=KnowledgeSourceRead, responses=_RESPONSES)
async def get_source(
    source_id: uuid.UUID, request: Request, session: SessionDep, principal: Reader
) -> KnowledgeSourceRead:
    source, version = await knowledge_service.get_source(
        _context(request, session, principal), source_id
    )
    return _source_read(source, version)


@router.patch(
    "/sources/{source_id}",
    response_model=KnowledgeSourceRead,
    responses={**_RESPONSES, 409: {"description": "revision_conflict", **_ERROR}},
)
async def update_source(
    source_id: uuid.UUID,
    payload: SourceUpdate,
    request: Request,
    session: SessionDep,
    principal: Updater,
) -> KnowledgeSourceRead:
    """Change title, description or tags. ``revision`` must match the current revision."""
    source, version = await knowledge_service.update_metadata(
        _context(request, session, principal), source_id, payload
    )
    return _source_read(source, version)


@router.delete(
    "/sources/{source_id}", response_model=KnowledgeRemovalRead, responses=_RESPONSES
)
async def remove_source(
    source_id: uuid.UUID,
    payload: SourceRemoval,
    request: Request,
    session: SessionDep,
    principal: Remover,
) -> KnowledgeRemovalRead:
    """Soft-remove a source (with a reason). Its versions and files are kept for audit."""
    source = await knowledge_service.remove_source(
        _context(request, session, principal), source_id, payload
    )
    return KnowledgeRemovalRead(
        id=source.id, status=source.status, removed_at=source.removed_at, removed_by=source.removed_by
    )


@router.post(
    "/sources/{source_id}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeVersionRead,
    responses={**_UPLOAD_RESPONSES, 409: {"description": "content_unchanged", **_ERROR}},
    openapi_extra=_multipart_doc({"change_note": {"type": "string"}}),
)
async def upload_version(
    source_id: uuid.UUID, request: Request, session: SessionDep, principal: Updater
) -> KnowledgeVersionRead:
    """Upload a replacement document. It becomes the active version once validated."""
    values, upload = await _upload_form(request, _VERSION_FIELDS)
    data = _validated(VersionUpload, values)
    source, version = await knowledge_service.upload_version(
        _context(request, session, principal), source_id, upload, upload.filename, data.change_note
    )
    return _version_read(version, source.active_version_id)


@router.get(
    "/sources/{source_id}/versions", response_model=KnowledgeVersionPage, responses=_RESPONSES
)
async def list_versions(
    source_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: Reader,
    limit: Limit = 50,
    offset: Offset = 0,
) -> KnowledgeVersionPage:
    """Version history, newest first. ``is_active`` marks the current version."""
    source, versions, has_more = await knowledge_service.list_versions(
        _context(request, session, principal), source_id, limit=limit, offset=offset
    )
    return KnowledgeVersionPage(
        items=[_version_read(v, source.active_version_id) for v in versions], has_more=has_more
    )


@router.get(
    "/sources/{source_id}/versions/{version_id}",
    response_model=KnowledgeVersionRead,
    responses=_RESPONSES,
)
async def get_version(
    source_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: Reader,
) -> KnowledgeVersionRead:
    source, version = await knowledge_service.get_version(
        _context(request, session, principal), source_id, version_id
    )
    return _version_read(version, source.active_version_id)


@router.get(
    "/sources/{source_id}/versions/{version_id}/download",
    response_class=FileResponse,
    responses={**_RESPONSES, 200: {"description": "The document, as an attachment"}},
)
async def download_version(
    source_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: Reader,
) -> FileResponse:
    """Download one version. Authorized and checksum-verified before any byte is sent."""
    version, path = await knowledge_service.download_path(
        _context(request, session, principal), source_id, version_id
    )
    media_type = version.media_type
    if media_type.startswith("text/"):
        media_type += "; charset=utf-8"
    return FileResponse(
        path,
        media_type=media_type,
        filename=version.original_filename,
        content_disposition_type="attachment",
        headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; sandbox"},
    )
