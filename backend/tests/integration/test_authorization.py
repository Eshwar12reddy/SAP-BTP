"""Roles, permissions, grant lifecycle and BTP scope enforcement."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.auth.permissions import ROLE_PERMISSIONS, RoleName
from app.models import (
    AccessGrant,
    AuditEvent,
    BTPAccount,
    BTPEnvironment,
    Permission,
    Role,
    RolePermission,
)
from tests.support import create_tree, create_user, grant, login, user_with_role

BTP = "/api/v1/btp"


async def ids(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


# ---------- seeded roles mirror the code ----------


async def test_database_roles_match_code_definitions(client: AsyncClient) -> None:
    async with client.app.state.session_factory() as session:
        rows = await session.execute(
            select(Role.name, Permission.key)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
        )
        db: dict[str, set[str]] = {}
        for role, key in rows:
            db.setdefault(role, set()).add(key)
    assert db == {role.value: {p.value for p in perms} for role, perms in ROLE_PERMISSIONS.items()}


# ---------- the four roles ----------


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (RoleName.VIEWER, {"chat:use", "btp:read", "knowledge:read"}),
        (RoleName.OPERATOR, {"chat:use", "btp:read", "knowledge:read", "operations:request", "operations:execute_low_risk"}),
        (RoleName.APPROVER, {"chat:use", "btp:read", "knowledge:read", "approvals:read", "approvals:decide"}),
    ],
)
async def test_btp_roles(client: AsyncClient, role: RoleName, expected: set[str]) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, f"{role}@example.com", role, tree.sub)
    permissions = (await client.get("/api/v1/users/me/permissions", headers=headers)).json()
    assert {p["permission"] for p in permissions} == expected
    assert all(s["scope_id"] == str(tree.sub.id) for p in permissions for s in p["scopes"])
    assert (await client.get("/api/v1/conversations", headers=headers)).status_code == 200
    assert await ids(await client.get(f"{BTP}/accounts", headers=headers)) == {str(tree.sub.id)}
    # No administration for non-administrators.
    denied = await client.get("/api/v1/admin/users", headers=headers)
    assert denied.status_code == 403


async def test_administrator_gets_no_btp_data_or_chat(client: AsyncClient) -> None:
    await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "root@example.com", RoleName.ADMINISTRATOR)
    assert (await client.get("/api/v1/admin/users", headers=headers)).status_code == 200
    assert (await client.get(f"{BTP}/accounts", headers=headers)).status_code == 403
    assert (await client.get("/api/v1/conversations", headers=headers)).status_code == 403


async def test_user_without_grants_is_denied_everything(client: AsyncClient) -> None:
    await create_user(client.app, "nobody@example.com")
    headers = await login(client, "nobody@example.com")
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    for path in ("/api/v1/conversations", f"{BTP}/accounts", "/api/v1/admin/users"):
        response = await client.get(path, headers=headers)
        assert response.status_code == 403, path
    async with client.app.state.session_factory() as session:
        denials = await session.scalar(
            select(func.count()).where(AuditEvent.event_type == "authz.access_denied")
        )
    assert denials == 3


async def test_multiple_grants_combine(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    user = await create_user(client.app, "multi@example.com")
    await grant(client.app, user, RoleName.VIEWER, tree.sub)
    await grant(client.app, user, RoleName.OPERATOR, tree.other_sub)
    headers = await login(client, "multi@example.com")
    assert await ids(await client.get(f"{BTP}/accounts", headers=headers)) == {
        str(tree.sub.id),
        str(tree.other_sub.id),
    }
    permissions = {p["permission"]: p for p in (await client.get("/api/v1/users/me/permissions", headers=headers)).json()}
    # operations:request comes only from the other_sub grant.
    assert [s["scope_id"] for s in permissions["operations:request"]["scopes"]] == [str(tree.other_sub.id)]


@pytest.mark.parametrize("state", ["revoked", "expired"])
async def test_inactive_grants_give_no_access(client: AsyncClient, state: str) -> None:
    tree = await create_tree(client.app)
    user = await create_user(client.app, f"{state}@example.com")
    kwargs = {"revoked": True} if state == "revoked" else {}
    row = await grant(client.app, user, RoleName.VIEWER, tree.sub, **kwargs)
    if state == "expired":
        async with client.app.state.session_factory() as session:
            await session.execute(
                update(AccessGrant)
                .where(AccessGrant.id == row.id)
                .values(created_at=datetime.now(UTC) - timedelta(days=2), expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
    headers = await login(client, f"{state}@example.com")
    assert (await client.get(f"{BTP}/accounts", headers=headers)).status_code == 403
    assert (await client.get(f"{BTP}/accounts/{tree.sub.id}", headers=headers)).status_code == 403


async def test_revocation_applies_to_existing_tokens_immediately(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    user = await create_user(client.app, "soon-gone@example.com")
    row = await grant(client.app, user, RoleName.VIEWER, tree.sub)
    headers = await login(client, "soon-gone@example.com")
    assert (await client.get(f"{BTP}/accounts/{tree.sub.id}", headers=headers)).status_code == 200
    async with client.app.state.session_factory() as session:
        await session.execute(update(AccessGrant).where(AccessGrant.id == row.id).values(revoked_at=datetime.now(UTC)))
        await session.commit()
    # Same, still-valid access token.
    assert (await client.get(f"{BTP}/accounts/{tree.sub.id}", headers=headers)).status_code == 403


# ---------- BTP scopes ----------


@pytest.mark.parametrize("label", ["dev", "test", "prod"])
async def test_single_environment_user(client: AsyncClient, label: str) -> None:
    tree = await create_tree(client.app)
    mine = getattr(tree, label)
    others = [e for e in (tree.dev, tree.test, tree.prod, tree.sub2_dev, tree.other_dev) if e.id != mine.id]
    _, headers = await user_with_role(client.app, client, f"{label}@example.com", RoleName.VIEWER, mine)

    # Authorized: reaches the adapter, which honestly reports no live integration.
    apps = await client.get(f"{BTP}/environments/{mine.id}/applications", headers=headers)
    assert apps.status_code == 503 and apps.json()["error"]["code"] == "btp_integration_unavailable"
    for other in others:  # cross-environment, including same-named "Development" elsewhere
        response = await client.get(f"{BTP}/environments/{other.id}/applications", headers=headers)
        assert response.status_code == 404, other.name

    # No parent-scope inheritance: the subaccount itself stays invisible.
    assert await ids(await client.get(f"{BTP}/accounts", headers=headers)) == set()
    assert (await client.get(f"{BTP}/accounts/{tree.sub.id}", headers=headers)).status_code == 404
    assert (await client.get(f"{BTP}/accounts/{tree.sub.id}/environments", headers=headers)).status_code == 404

    scopes = (await client.get("/api/v1/users/me/scopes", headers=headers)).json()["btp_read"]
    assert [e["id"] for e in scopes["environments"]] == [str(mine.id)]
    assert scopes["environments"][0]["btp_account_id"] is None  # parent id not disclosed
    assert scopes["accounts"] == []


async def test_subaccount_grant_without_descendants_covers_only_the_subaccount(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "sub@example.com", RoleName.VIEWER, tree.sub)
    assert await ids(await client.get(f"{BTP}/accounts", headers=headers)) == {str(tree.sub.id)}
    account = (await client.get(f"{BTP}/accounts/{tree.sub.id}", headers=headers)).json()
    assert account["parent_account_id"] is None  # global account not covered
    assert await ids(await client.get(f"{BTP}/accounts/{tree.sub.id}/environments", headers=headers)) == set()
    assert (await client.get(f"{BTP}/environments/{tree.dev.id}/applications", headers=headers)).status_code == 404
    assert (await client.get(f"{BTP}/accounts/{tree.ga.id}", headers=headers)).status_code == 404


async def test_explicit_descendant_grant_on_global_account(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app, client, "ga@example.com", RoleName.VIEWER, tree.ga, include_descendants=True
    )
    assert await ids(await client.get(f"{BTP}/accounts", headers=headers)) == {
        str(tree.ga.id), str(tree.sub.id), str(tree.sub2.id)
    }
    assert await ids(await client.get(f"{BTP}/accounts/{tree.sub.id}/environments", headers=headers)) == {
        str(tree.dev.id), str(tree.test.id), str(tree.prod.id)
    }
    child = (await client.get(f"{BTP}/accounts/{tree.sub.id}", headers=headers)).json()
    assert child["parent_account_id"] == str(tree.ga.id)
    # Cross-account: another global account and its children stay out of reach.
    for target in (tree.other_ga.id, tree.other_sub.id):
        assert (await client.get(f"{BTP}/accounts/{target}", headers=headers)).status_code == 404
    assert (await client.get(f"{BTP}/environments/{tree.other_dev.id}/applications", headers=headers)).status_code == 404


async def test_global_account_grant_without_descendants(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "ga-only@example.com", RoleName.VIEWER, tree.ga)
    assert await ids(await client.get(f"{BTP}/accounts", headers=headers)) == {str(tree.ga.id)}
    assert (await client.get(f"{BTP}/accounts/{tree.sub.id}", headers=headers)).status_code == 404


async def test_unauthorized_and_nonexistent_resources_look_identical(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "probe@example.com", RoleName.VIEWER, tree.sub)
    hidden = await client.get(f"{BTP}/accounts/{tree.other_sub.id}", headers=headers)
    missing = await client.get(f"{BTP}/accounts/{uuid.uuid4()}", headers=headers)
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]
    async with client.app.state.session_factory() as session:
        denials = await session.scalar(
            select(func.count()).where(AuditEvent.event_type == "authz.access_denied")
        )
    assert denials == 2


async def test_scope_filtering_happens_before_pagination(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    user = await create_user(client.app, "pager@example.com")
    await grant(client.app, user, RoleName.VIEWER, tree.sub)
    await grant(client.app, user, RoleName.VIEWER, tree.other_sub)
    headers = await login(client, "pager@example.com")
    first = (await client.get(f"{BTP}/accounts", params={"limit": 1}, headers=headers)).json()
    second = (await client.get(f"{BTP}/accounts", params={"limit": 1, "offset": 1}, headers=headers)).json()
    assert first["has_more"] is True and second["has_more"] is False
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {str(tree.sub.id), str(tree.other_sub.id)}


# ---------- integrity enforced by the database ----------


async def test_hierarchy_and_grant_integrity_constraints(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    user = await create_user(client.app, "integrity@example.com")
    factory = client.app.state.session_factory

    async def rejected(row) -> None:
        async with factory() as session:
            session.add(row)
            with pytest.raises(IntegrityError):
                await session.commit()

    # Environment directly under a global account.
    await rejected(BTPEnvironment(btp_account_id=tree.ga.id, name="Bad", runtime_type="kyma"))
    # Subaccount whose "parent" is another subaccount; global account with a parent.
    await rejected(BTPAccount(external_account_id="x1", name="x", account_type="subaccount",
                              parent_account_id=tree.sub.id, parent_account_type="global_account"))
    await rejected(BTPAccount(external_account_id="x2", name="x", account_type="global_account",
                              parent_account_id=tree.ga.id, parent_account_type="global_account"))
    async with factory() as session:
        viewer = await session.scalar(select(Role).where(Role.name == "viewer"))
        admin = await session.scalar(select(Role).where(Role.name == "administrator"))
    # Scope type that lies about the account type.
    await rejected(AccessGrant(user_id=user.id, role_id=viewer.id, role_name="viewer",
                               scope_type="global_account", btp_account_id=tree.sub.id))
    # Viewer at platform scope; administrator at environment scope; role name not matching id.
    await rejected(AccessGrant(user_id=user.id, role_id=viewer.id, role_name="viewer", scope_type="platform"))
    await rejected(AccessGrant(user_id=user.id, role_id=admin.id, role_name="administrator",
                               scope_type="environment", btp_environment_id=tree.dev.id))
    await rejected(AccessGrant(user_id=user.id, role_id=viewer.id, role_name="administrator", scope_type="platform"))
    # Scope id pointing at nothing.
    await rejected(AccessGrant(user_id=user.id, role_id=viewer.id, role_name="viewer",
                               scope_type="environment", btp_environment_id=uuid.uuid4()))
    # Duplicate active grant.
    await grant(client.app, user, RoleName.VIEWER, tree.sub)
    await rejected(AccessGrant(user_id=user.id, role_id=viewer.id, role_name="viewer",
                               scope_type="subaccount", btp_account_id=tree.sub.id))
    # Email must be stored normalized (case-insensitive uniqueness).
    from app.models import User

    await rejected(User(email="Integrity@Example.com", display_name="dup", password_hash="x"))
