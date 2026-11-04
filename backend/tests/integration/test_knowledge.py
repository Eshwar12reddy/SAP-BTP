"""Focused Phase 9 end-to-end checks using only the isolated ``*_test`` database."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.auth.permissions import RoleName
from app.models import AuditEvent, KnowledgeSource, KnowledgeSourceVersion
from app.models.knowledge_index import KnowledgeIndexGeneration
from tests.support import create_tree, user_with_role

API = "/api/v1/knowledge"


class _MockEmbeddings:
    configured = True

    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.inputs.extend(texts)
        vector = [1.0] + [0.0] * 1535
        return [vector.copy() for _ in texts]

    async def close(self) -> None:
        return None


def upload_fields(scope_type: str = "btp_account", scope_id: str | None = None) -> dict[str, str]:
    return {
        "title": "Operations runbook",
        "scope_type": scope_type,
        **({"scope_id": scope_id} if scope_id else {}),
    }


async def register(client: AsyncClient, headers: dict[str, str], data: dict[str, str], content: bytes):
    return await client.post(
        f"{API}/sources",
        data=data,
        files={"file": ("runbook.md", content, "text/markdown")},
        headers=headers,
    )


async def test_manager_registers_downloads_versions_and_soft_removes(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, manager = await user_with_role(
        client.app, client, "knowledge-manager@example.com", RoleName.KNOWLEDGE_MANAGER, tree.sub
    )
    created = await register(
        client, manager, upload_fields(scope_id=str(tree.sub.id)), b"First runbook.\n"
    )
    assert created.status_code == 201, created.text
    source = created.json()
    source_id = source["id"]
    first = source["active_version"]
    assert source["owner_id"] == source["created_by"]
    assert first["version_number"] == 1 and first["is_active"]
    assert "storage_key" not in source and "storage_key" not in first

    downloaded = await client.get(
        f'{API}/sources/{source_id}/versions/{first["id"]}/download', headers=manager
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"First runbook.\n"
    assert downloaded.headers["cache-control"] == "no-store"

    replacement = await client.post(
        f'{API}/sources/{source_id}/versions',
        data={"change_note": "Updated steps"},
        files={"file": ("runbook.md", b"Second runbook.\n", "text/markdown")},
        headers=manager,
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["version_number"] == 2
    versions = (await client.get(f'{API}/sources/{source_id}/versions', headers=manager)).json()
    assert [item["version_number"] for item in versions["items"]] == [2, 1]
    assert [item["is_active"] for item in versions["items"]] == [True, False]

    current_source = (await client.get(f'{API}/sources/{source_id}', headers=manager)).json()
    updated = await client.patch(
        f'{API}/sources/{source_id}',
        json={"revision": current_source["revision"], "title": "Revised runbook"},
        headers=manager,
    )
    assert updated.status_code == 200
    stale = await client.patch(
        f'{API}/sources/{source_id}',
        json={"revision": current_source["revision"], "title": "Stale update"},
        headers=manager,
    )
    assert stale.status_code == 409

    removed = await client.request(
        "DELETE", f'{API}/sources/{source_id}', json={"reason": "Superseded"}, headers=manager
    )
    assert removed.status_code == 200 and removed.json()["status"] == "removed"
    assert (await client.get(f'{API}/sources/{source_id}', headers=manager)).status_code == 404
    assert (await client.get(f'{API}/sources', headers=manager)).json()["items"] == []

    async with client.app.state.session_factory() as session:
        events = set(
            await session.scalars(select(AuditEvent.event_type).where(AuditEvent.event_type.like("knowledge.%")))
        )
    assert {
        "knowledge.source.created",
        "knowledge.source.updated",
        "knowledge.source.removed",
        "knowledge.version.uploaded",
        "knowledge.version.validated",
    } <= events


async def test_reader_cannot_write_and_btp_scope_is_enforced(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, reader = await user_with_role(
        client.app, client, "knowledge-reader@example.com", RoleName.KNOWLEDGE_READER, tree.sub
    )
    denied = await register(client, reader, upload_fields(scope_id=str(tree.sub.id)), b"content")
    assert denied.status_code == 403

    _, manager = await user_with_role(
        client.app, client, "knowledge-scoped-manager@example.com", RoleName.KNOWLEDGE_MANAGER, tree.sub
    )
    created = await register(
        client, manager, upload_fields(scope_id=str(tree.sub.id)), b"Scoped content.\n"
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]
    _, other = await user_with_role(
        client.app, client, "knowledge-other@example.com", RoleName.KNOWLEDGE_READER, tree.other_sub
    )
    assert (await client.get(f"{API}/sources/{source_id}", headers=other)).status_code == 404
    assert (await client.get(f'{API}/sources/{source_id}/versions/{created.json()["active_version"]["id"]}/download', headers=other)).status_code == 404


async def test_invalid_scope_and_forged_owner_fields_are_rejected(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, manager = await user_with_role(
        client.app, client, "knowledge-forgery@example.com", RoleName.KNOWLEDGE_MANAGER, tree.sub
    )
    invalid = await register(
        client,
        manager,
        upload_fields(scope_type="global"),
        b"Content.\n",
    )
    assert invalid.status_code == 403
    forged = await client.post(
        f"{API}/sources",
        data=upload_fields(scope_id=str(tree.sub.id)) | {"owner_id": str(uuid.uuid4())},
        files={"file": ("runbook.md", b"Content.\n", "text/markdown")},
        headers=manager,
    )
    assert forged.status_code == 422


async def test_invalid_upload_is_rejected_without_publishing_object(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, manager = await user_with_role(
        client.app, client, "knowledge-invalid-file@example.com", RoleName.KNOWLEDGE_MANAGER, tree.sub
    )
    response = await register(
        client, manager, upload_fields(scope_id=str(tree.sub.id)), b"\x00not text"
    )
    assert response.status_code == 400
    storage = client.app.state.knowledge_storage
    assert list(storage.object_keys()) == []
    assert list(storage.staging.glob("*.part")) == []


async def test_database_rejects_version_mutation_and_deletion(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, manager = await user_with_role(
        client.app, client, "knowledge-immutable@example.com", RoleName.KNOWLEDGE_MANAGER, tree.sub
    )
    created = await register(
        client, manager, upload_fields(scope_id=str(tree.sub.id)), b"Immutable.\n"
    )
    assert created.status_code == 201, created.text
    version_id = uuid.UUID(created.json()["active_version"]["id"])
    source_id = uuid.UUID(created.json()["id"])
    async with client.app.state.session_factory() as session:
        source = await session.get(KnowledgeSource, source_id)
        pending = KnowledgeSourceVersion(
            source_id=source_id,
            version_number=2,
            original_filename="pending.txt",
            media_type="text/plain",
            size_bytes=1,
            sha256="1" * 64,
            storage_key=client.app.state.knowledge_storage.new_key(),
            uploaded_by=source.owner_id,
            validation_status="pending",
            validated_at=None,
        )
        session.add(pending)
        await session.commit()
        await session.execute(
            update(KnowledgeSourceVersion)
            .where(KnowledgeSourceVersion.id == pending.id)
            .values(validation_status="validated", validated_at=datetime.now(UTC))
        )
        await session.commit()
    async with client.app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                update(KnowledgeSourceVersion)
                .where(KnowledgeSourceVersion.id == version_id)
                .values(sha256="0" * 64)
            )
            await session.commit()
        await session.rollback()
    async with client.app.state.session_factory() as session:
        version = await session.get(KnowledgeSourceVersion, version_id)
        await session.delete(version)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
    async with client.app.state.session_factory() as session:
        source = await session.get(KnowledgeSource, uuid.UUID(created.json()["id"]))
        assert source.active_version_id == version_id
    second = await register(
        client,
        manager,
        upload_fields(scope_id=str(tree.sub.id)) | {"title": "Second source"},
        b"Other source.\n",
    )
    assert second.status_code == 201, second.text
    async with client.app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                update(KnowledgeSource)
                .where(KnowledgeSource.id == source_id)
                .values(active_version_id=uuid.UUID(second.json()["active_version"]["id"]))
            )
            await session.commit()
        await session.rollback()
    async with client.app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                update(KnowledgeSource)
                .where(KnowledgeSource.id == source_id)
                .values(active_version_status="pending")
            )
            await session.commit()
        await session.rollback()


async def test_opt_in_vector_indexing_hybrid_search_and_scope_filter(client: AsyncClient, monkeypatch) -> None:
    class _OfflineEncoding:
        def encode(self, value: str, disallowed_special=()) -> list[int]:
            return [ord(character) for character in value]

        def decode(self, tokens: list[int]) -> str:
            return "".join(chr(token) for token in tokens)

        def decode_with_offsets(self, tokens: list[int]) -> tuple[str, list[int]]:
            return self.decode(tokens), list(range(len(tokens)))

    encoding = _OfflineEncoding()
    monkeypatch.setattr("app.rag.chunking._encoding", lambda _name: encoding)
    monkeypatch.setattr("app.services.retrieval_service._encoding", lambda _name: encoding)
    tree = await create_tree(client.app)
    _, manager = await user_with_role(
        client.app, client, "knowledge-vector-manager@example.com", RoleName.KNOWLEDGE_MANAGER, tree.sub
    )
    mock = _MockEmbeddings()
    client.app.state.openai_embeddings = mock
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"OPENAI_EMBEDDINGS_ENABLED": True}
    )
    response = await register(
        client,
        manager,
        upload_fields(scope_id=str(tree.sub.id)),
        ("# Vector setup\n" + "The service binding uses a credential rotation workflow. " * 12).encode(),
    )
    assert response.status_code == 201, response.text
    assert mock.inputs and "credential rotation workflow" in mock.inputs[0]
    assert all(len(value) < 600 for value in mock.inputs)

    found = await client.post(f"{API}/search", json={"query": "credential rotation"}, headers=manager)
    assert found.status_code == 200, found.text
    result = found.json()
    assert result["retrieval_mode"] == "hybrid"
    assert result["semantic_status"] == "available"
    assert result["items"]
    assert result["items"][0]["citation"]["source_id"] == response.json()["id"]
    assert mock.inputs[-1] == "credential rotation"

    async with client.app.state.session_factory() as session:
        await session.execute(
            update(KnowledgeIndexGeneration)
            .where(KnowledgeIndexGeneration.source_id == uuid.UUID(response.json()["id"]))
            .values(embedding_model_id="a-different-model")
        )
        await session.commit()
    mismatch = await client.post(f"{API}/search", json={"query": "credential rotation"}, headers=manager)
    assert mismatch.status_code == 200
    assert mismatch.json()["retrieval_mode"] == "lexical_fallback"
    assert mismatch.json()["semantic_status"] == "model_mismatch"

    _, other = await user_with_role(
        client.app, client, "knowledge-vector-other@example.com", RoleName.KNOWLEDGE_READER, tree.other_sub
    )
    before = len(mock.inputs)
    _, other_manager = await user_with_role(
        client.app, client, "knowledge-vector-other-manager@example.com", RoleName.KNOWLEDGE_MANAGER, tree.other_sub
    )
    denied_index = await client.post(
        f'{API}/sources/{response.json()["id"]}/index', headers=other_manager
    )
    assert denied_index.status_code == 404
    assert len(mock.inputs) == before  # out-of-scope document chunks were not embedded
    hidden = await client.post(f"{API}/search", json={"query": "credential rotation"}, headers=other)
    assert hidden.status_code == 200
    assert hidden.json()["items"] == []
    assert len(mock.inputs) == before + 1  # only the query was embedded; no source chunk was exposed
async def test_concurrent_uploads_receive_distinct_version_numbers(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, manager = await user_with_role(
        client.app, client, "knowledge-concurrent@example.com", RoleName.KNOWLEDGE_MANAGER, tree.sub
    )
    created = await register(
        client, manager, upload_fields(scope_id=str(tree.sub.id)), b"Version one.\n"
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]

    async def replace(text: str):
        return await client.post(
            f"{API}/sources/{source_id}/versions",
            files={"file": ("runbook.md", text.encode(), "text/markdown")},
            headers=manager,
        )

    responses = await asyncio.gather(replace("Version two.\n"), replace("Version three.\n"))
    assert [response.status_code for response in responses] == [201, 201], [r.text for r in responses]
    assert {response.json()["version_number"] for response in responses} == {2, 3}
