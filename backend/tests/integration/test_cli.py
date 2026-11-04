"""Operator CLI: one-time admin bootstrap, BTP mapping, legacy conversation assignment."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app import cli
from app.models import AccessGrant, AuditEvent, BTPAccount, BTPEnvironment, Conversation, User
from tests.support import create_user


def args(**values) -> SimpleNamespace:
    return SimpleNamespace(**values)


async def run(client: AsyncClient, handler, namespace) -> None:
    async with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        await handler(session, namespace)


async def test_bootstrap_admin_is_one_time(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("a strong bootstrap passphrase\n"))
    await run(client, cli.create_admin, args(email="Boss@Example.com", display_name="Boss", password_stdin=True))
    async with client.app.state.session_factory() as session:
        user = await session.scalar(select(User).where(User.email == "boss@example.com"))
        [grant] = list(await session.scalars(select(AccessGrant).where(AccessGrant.user_id == user.id)))
        audit = await session.scalar(select(AuditEvent).where(AuditEvent.event_type == "admin.bootstrap"))
    assert (grant.role_name, grant.scope_type) == ("administrator", "platform")
    assert "passphrase" not in user.password_hash and audit is not None
    login = await client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": "a strong bootstrap passphrase"})
    assert login.status_code == 200

    monkeypatch.setattr("sys.stdin", io.StringIO("another strong passphrase\n"))
    with pytest.raises(cli.CliError, match="one-time"):
        await run(client, cli.create_admin, args(email="second@example.com", display_name="Second", password_stdin=True))


async def test_bootstrap_rejects_weak_password(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    with pytest.raises(cli.CliError, match="at least 12"):
        await run(client, cli.create_admin, args(email="weak@example.com", display_name="W", password_stdin=True))


async def test_btp_mapping_commands_enforce_hierarchy(client: AsyncClient) -> None:
    await run(client, cli.add_global_account, args(external_id="ga-guid", name="Global", region=None))
    await run(client, cli.add_subaccount, args(external_id="sa-guid", name="EU", parent="ga-guid", region="eu10"))
    await run(client, cli.add_environment, args(account="sa-guid", name="Development", runtime="cloudfoundry", external_runtime_id="org-1"))
    with pytest.raises(cli.CliError, match="subaccount"):
        await run(client, cli.add_environment, args(account="ga-guid", name="Bad", runtime="kyma", external_runtime_id=None))
    with pytest.raises(cli.CliError, match="global account"):
        await run(client, cli.add_subaccount, args(external_id="x", name="x", parent="sa-guid", region=None))
    with pytest.raises(cli.CliError, match="already"):
        await run(client, cli.add_global_account, args(external_id="ga-guid", name="dup", region=None))
    async with client.app.state.session_factory() as session:
        assert len(list(await session.scalars(select(BTPAccount)))) == 2
        assert len(list(await session.scalars(select(BTPEnvironment)))) == 1


async def test_assign_legacy_conversations(client: AsyncClient) -> None:
    owner = await create_user(client.app, "heir@example.com")
    async with client.app.state.session_factory() as session:
        legacy = Conversation(title="old")
        session.add(legacy)
        await session.commit()
    with pytest.raises(cli.CliError, match="--all"):
        await run(client, cli.assign_legacy_conversations, args(email="heir@example.com", all=False, conversation_id=None, yes=True))
    await run(client, cli.assign_legacy_conversations, args(email="heir@example.com", all=True, conversation_id=None, yes=True))
    async with client.app.state.session_factory() as session:
        assert (await session.get(Conversation, legacy.id)).owner_id == owner.id
