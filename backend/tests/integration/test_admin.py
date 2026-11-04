"""Administration: user lifecycle, grants, delegation boundaries, escalation, audit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.auth.permissions import RoleName
from app.models import AccessGrant, AuditEvent
from tests.support import (
    PASSWORD,
    client_for,
    create_tree,
    create_user,
    grant,
    login,
    user_with_role,
)

ADMIN = "/api/v1/admin"
NEW_USER = {"email": "New.Person@Example.com", "display_name": "  New   Person ", "password": "a long enough password"}


async def events(client: AsyncClient, event_type: str) -> list[AuditEvent]:
    async with client.app.state.session_factory() as session:
        return list(await session.scalars(select(AuditEvent).where(AuditEvent.event_type == event_type)))


@pytest.fixture
async def platform(client: AsyncClient):
    tree = await create_tree(client.app)
    admin, headers = await user_with_role(client.app, client, "root@example.com", RoleName.ADMINISTRATOR)
    return tree, admin, headers


# ---------- user lifecycle (platform administrator) ----------


async def test_create_user(client: AsyncClient, platform) -> None:
    _, admin, headers = platform
    response = await client.post(f"{ADMIN}/users", json=NEW_USER, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.person@example.com" and body["display_name"] == "New Person"
    assert "password" not in response.text
    # The new account can sign in, but has no grants yet.
    login_response = await client.post(
        "/api/v1/auth/login", json={"email": "new.person@example.com", "password": NEW_USER["password"]}
    )
    assert login_response.status_code == 200
    [event] = await events(client, "admin.user.created")
    assert event.actor_user_id == admin.id and str(event.target_user_id) == body["id"]
    assert NEW_USER["password"] not in str(event.details)


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({**NEW_USER, "password": "too short"}, 400, "weak_password"),
        ({**NEW_USER, "email": "not-an-email"}, 422, "validation_error"),
        ({**NEW_USER, "role": "administrator"}, 422, "validation_error"),
    ],
)
async def test_create_user_validation(client: AsyncClient, platform, payload, status, code) -> None:
    _, _, headers = platform
    response = await client.post(f"{ADMIN}/users", json=payload, headers=headers)
    assert response.status_code == status and response.json()["error"]["code"] == code


async def test_duplicate_email_is_case_insensitive(client: AsyncClient, platform) -> None:
    _, _, headers = platform
    assert (await client.post(f"{ADMIN}/users", json=NEW_USER, headers=headers)).status_code == 201
    dup = await client.post(f"{ADMIN}/users", json={**NEW_USER, "email": "NEW.PERSON@example.com"}, headers=headers)
    assert dup.status_code == 409


async def test_list_search_detail_update(client: AsyncClient, platform) -> None:
    tree, _, headers = platform
    target = await create_user(client.app, "searchable@example.com", display_name="Findable Person")
    await grant(client.app, target, RoleName.OPERATOR, tree.sub)
    listing = (await client.get(f"{ADMIN}/users", params={"search": "findable"}, headers=headers)).json()
    assert [u["email"] for u in listing["items"]] == ["searchable@example.com"]
    assert listing["items"][0]["roles"] == ["operator"]
    # LIKE wildcards in the search are literal.
    assert (await client.get(f"{ADMIN}/users", params={"search": "%"}, headers=headers)).json()["items"] == []

    detail = (await client.get(f"{ADMIN}/users/{target.id}", headers=headers)).json()
    assert detail["manageable_account"] is True
    assert [(g["role"], g["scope_name"]) for g in detail["grants"]] == [("operator", "Acme EU")]

    updated = await client.patch(f"{ADMIN}/users/{target.id}", json={"display_name": "Renamed"}, headers=headers)
    assert updated.status_code == 200 and updated.json()["display_name"] == "Renamed"
    assert len(await events(client, "admin.user.updated")) == 1


async def test_disable_and_enable_user(client: AsyncClient, platform) -> None:
    _, _, headers = platform
    target = await create_user(client.app, "disable-me@example.com")
    async with client_for(client.app) as victim:
        victim_headers = await login(victim, "disable-me@example.com")
        response = await client.post(f"{ADMIN}/users/{target.id}/disable", headers=headers)
        assert response.status_code == 200 and response.json()["is_active"] is False
        # Existing tokens die immediately, and login is refused generically.
        assert (await victim.get("/api/v1/auth/me", headers=victim_headers)).status_code == 401
        refused = await victim.post("/api/v1/auth/login", json={"email": "disable-me@example.com", "password": PASSWORD})
        assert refused.status_code == 401 and refused.json()["error"]["code"] == "invalid_credentials"

        assert (await client.post(f"{ADMIN}/users/{target.id}/enable", headers=headers)).json()["is_active"] is True
        await login(victim, "disable-me@example.com")
    [disabled] = await events(client, "admin.user.disabled")
    assert disabled.details["sessions_revoked"] == 1
    assert len(await events(client, "admin.user.enabled")) == 1


async def test_revoke_user_sessions(client: AsyncClient, platform) -> None:
    _, _, headers = platform
    target = await create_user(client.app, "sessions@example.com")
    async with client_for(client.app) as device:
        device_headers = await login(device, "sessions@example.com")
        response = await client.post(f"{ADMIN}/users/{target.id}/sessions/revoke", headers=headers)
        assert response.json() == {"sessions_revoked": 1}
        assert (await device.get("/api/v1/auth/me", headers=device_headers)).status_code == 401
    assert len(await events(client, "admin.sessions.revoked")) == 1


async def test_administrators_cannot_act_on_themselves(client: AsyncClient, platform) -> None:
    tree, admin, headers = platform
    for path in (f"/users/{admin.id}/disable", f"/users/{admin.id}/sessions/revoke"):
        response = await client.post(ADMIN + path, headers=headers)
        assert response.status_code == 403 and response.json()["error"]["code"] == "self_modification_forbidden"
    self_grant = await client.post(
        f"{ADMIN}/users/{admin.id}/grants",
        json={"role": "viewer", "scope_type": "subaccount", "scope_id": str(tree.sub.id)},
        headers=headers,
    )
    assert self_grant.status_code == 403
    async with client.app.state.session_factory() as session:
        own = await session.scalar(select(AccessGrant).where(AccessGrant.user_id == admin.id))
    assert (await client.post(f"{ADMIN}/grants/{own.id}/revoke", headers=headers)).status_code == 403


# ---------- grants ----------


async def test_assign_and_revoke_grant(client: AsyncClient, platform) -> None:
    tree, admin, headers = platform
    target = await create_user(client.app, "grantee@example.com")
    async with client_for(client.app) as grantee:
        grantee_headers = await login(grantee, "grantee@example.com")
        assert (await grantee.get(f"/api/v1/btp/accounts/{tree.sub.id}", headers=grantee_headers)).status_code == 403

        response = await client.post(
            f"{ADMIN}/users/{target.id}/grants",
            json={"role": "viewer", "scope_type": "subaccount", "scope_id": str(tree.sub.id)},
            headers=headers,
        )
        assert response.status_code == 201
        created = response.json()
        assert created["scope_name"] == "Acme EU" and created["include_descendants"] is False
        # Effective on the grantee's next request, without re-login.
        assert (await grantee.get(f"/api/v1/btp/accounts/{tree.sub.id}", headers=grantee_headers)).status_code == 200

        duplicate = await client.post(
            f"{ADMIN}/users/{target.id}/grants",
            json={"role": "viewer", "scope_type": "subaccount", "scope_id": str(tree.sub.id)},
            headers=headers,
        )
        assert duplicate.status_code == 409

        revoked = await client.post(f"{ADMIN}/grants/{created['id']}/revoke", headers=headers)
        assert revoked.status_code == 200 and revoked.json()["revoked_at"]
        assert (await grantee.get(f"/api/v1/btp/accounts/{tree.sub.id}", headers=grantee_headers)).status_code == 403
        assert (await client.post(f"{ADMIN}/grants/{created['id']}/revoke", headers=headers)).status_code == 404

    [assigned] = await events(client, "admin.grant.assigned")
    assert assigned.actor_user_id == admin.id and assigned.target_user_id == target.id
    assert assigned.btp_account_id == tree.sub.id and assigned.scope_type == "subaccount"
    assert len(await events(client, "admin.grant.revoked")) == 1


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"role": "viewer", "scope_type": "platform"}, "invalid_scope"),
        ({"role": "administrator", "scope_type": "environment", "scope_id": "ENV"}, "invalid_scope"),
        ({"role": "viewer", "scope_type": "environment", "scope_id": "ENV", "include_descendants": True}, "invalid_scope"),
        ({"role": "viewer", "scope_type": "global_account", "scope_id": "SUB"}, "not_found"),
        ({"role": "viewer", "scope_type": "subaccount"}, "invalid_scope"),
        ({"role": "viewer", "scope_type": "subaccount", "scope_id": "SUB", "expires_at": "2000-01-01T00:00:00Z"}, "invalid_expiry"),
    ],
)
async def test_grant_validation(client: AsyncClient, platform, payload: dict, code: str) -> None:
    tree, _, headers = platform
    target = await create_user(client.app, "validate@example.com")
    if payload.get("scope_id") == "ENV":
        payload = {**payload, "scope_id": str(tree.dev.id)}
    if payload.get("scope_id") == "SUB":
        payload = {**payload, "scope_id": str(tree.sub.id)}
    response = await client.post(f"{ADMIN}/users/{target.id}/grants", json=payload, headers=headers)
    assert response.json()["error"]["code"] == code, response.text


async def test_expired_grant_can_be_reissued(client: AsyncClient, platform) -> None:
    tree, _, headers = platform
    target = await create_user(client.app, "renew@example.com")
    old = await grant(client.app, target, RoleName.VIEWER, tree.sub)
    async with client.app.state.session_factory() as session:
        await session.execute(
            update(AccessGrant).where(AccessGrant.id == old.id).values(
                created_at=datetime.now(UTC) - timedelta(days=2), expires_at=datetime.now(UTC) - timedelta(days=1)
            )
        )
        await session.commit()
    expiry = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    response = await client.post(
        f"{ADMIN}/users/{target.id}/grants",
        json={"role": "viewer", "scope_type": "subaccount", "scope_id": str(tree.sub.id), "expires_at": expiry},
        headers=headers,
    )
    assert response.status_code == 201
    async with client.app.state.session_factory() as session:
        superseded = await session.get(AccessGrant, old.id)
    assert superseded.revoked_at is not None


async def test_platform_admin_can_appoint_administrators(client: AsyncClient, platform) -> None:
    tree, _, headers = platform
    target = await create_user(client.app, "deputy@example.com")
    for payload in (
        {"role": "administrator", "scope_type": "platform"},
        {"role": "administrator", "scope_type": "global_account", "scope_id": str(tree.ga.id), "include_descendants": True},
    ):
        assert (await client.post(f"{ADMIN}/users/{target.id}/grants", json=payload, headers=headers)).status_code == 201


# ---------- delegated administration ----------


@pytest.fixture
async def delegated(client: AsyncClient):
    """An administrator for the Acme global account subtree only."""
    tree = await create_tree(client.app)
    admin, headers = await user_with_role(
        client.app, client, "acme-admin@example.com", RoleName.ADMINISTRATOR, tree.ga, include_descendants=True
    )
    inside = await create_user(client.app, "inside@example.com")
    await grant(client.app, inside, RoleName.VIEWER, tree.dev)
    outside = await create_user(client.app, "outside@example.com")
    await grant(client.app, outside, RoleName.VIEWER, tree.other_dev)
    return tree, admin, headers, inside, outside


async def test_delegated_admin_sees_only_users_in_scope(client: AsyncClient, delegated) -> None:
    _, _, headers, inside, outside = delegated
    listing = (await client.get(f"{ADMIN}/users", headers=headers)).json()
    # Users holding grants inside the delegation (including the delegated admin).
    assert [u["email"] for u in listing["items"]] == ["acme-admin@example.com", "inside@example.com"]
    assert (await client.get(f"{ADMIN}/users/{inside.id}", headers=headers)).json()["manageable_account"] is False
    assert (await client.get(f"{ADMIN}/users/{outside.id}", headers=headers)).status_code == 404


async def test_delegated_admin_cannot_do_platform_actions(client: AsyncClient, delegated) -> None:
    _, _, headers, inside, _ = delegated
    assert (await client.post(f"{ADMIN}/users", json=NEW_USER, headers=headers)).status_code == 403
    assert (await client.patch(f"{ADMIN}/users/{inside.id}", json={"display_name": "x"}, headers=headers)).status_code == 403
    assert (await client.post(f"{ADMIN}/users/{inside.id}/disable", headers=headers)).status_code == 403
    assert (await client.post(f"{ADMIN}/users/{inside.id}/sessions/revoke", headers=headers)).status_code == 403


async def test_delegated_admin_grants_within_scope_only(client: AsyncClient, delegated) -> None:
    tree, _, headers, inside, outside = delegated
    url = f"{ADMIN}/users/{inside.id}/grants"
    ok = await client.post(url, json={"role": "operator", "scope_type": "environment", "scope_id": str(tree.prod.id)}, headers=headers)
    assert ok.status_code == 201
    # Another global account's environment: indistinguishable from a missing scope.
    cross = await client.post(url, json={"role": "viewer", "scope_type": "environment", "scope_id": str(tree.other_dev.id)}, headers=headers)
    assert cross.status_code == 404
    # Escalation attempts.
    for payload in (
        {"role": "administrator", "scope_type": "platform"},
        {"role": "administrator", "scope_type": "subaccount", "scope_id": str(tree.sub.id)},
    ):
        response = await client.post(url, json=payload, headers=headers)
        assert response.status_code == 403, payload
    # Users outside the delegation cannot be targeted at all.
    hidden = await client.post(
        f"{ADMIN}/users/{outside.id}/grants",
        json={"role": "viewer", "scope_type": "subaccount", "scope_id": str(tree.sub.id)},
        headers=headers,
    )
    assert hidden.status_code == 404
    denials = await events(client, "authz.access_denied")
    assert {d.details["reason"] for d in denials} >= {"scope_outside_delegation", "administrator_grant_requires_platform"}


async def test_delegated_admin_cannot_revoke_outside_scope(client: AsyncClient, delegated) -> None:
    _tree, _, headers, _, outside = delegated
    async with client.app.state.session_factory() as session:
        outside_grant = await session.scalar(select(AccessGrant).where(AccessGrant.user_id == outside.id))
    assert (await client.post(f"{ADMIN}/grants/{outside_grant.id}/revoke", headers=headers)).status_code == 404
    async with client.app.state.session_factory() as session:
        assert (await session.get(AccessGrant, outside_grant.id)).revoked_at is None


async def test_delegated_admin_cannot_revoke_administrator_grants(client: AsyncClient, delegated) -> None:
    tree, _, headers, inside, _ = delegated
    peer_admin = await grant(client.app, inside, RoleName.ADMINISTRATOR, tree.sub)
    assert (await client.post(f"{ADMIN}/grants/{peer_admin.id}/revoke", headers=headers)).status_code == 404


async def test_delegated_audit_and_scopes_are_filtered(client: AsyncClient, delegated) -> None:
    tree, _, headers, inside, _ = delegated
    await client.post(
        f"{ADMIN}/users/{inside.id}/grants",
        json={"role": "viewer", "scope_type": "subaccount", "scope_id": str(tree.sub2.id)},
        headers=headers,
    )
    audit = (await client.get(f"{ADMIN}/audit-events", headers=headers)).json()["items"]
    assert audit and all(e["btp_account_id"] in {str(tree.sub2.id)} for e in audit)
    scopes = (await client.get(f"{ADMIN}/scopes", headers=headers)).json()
    assert scopes["platform"] is False
    assert {a["id"] for a in scopes["accounts"]} == {str(tree.ga.id), str(tree.sub.id), str(tree.sub2.id)}
    assert str(tree.other_dev.id) not in {e["id"] for e in scopes["environments"]}


async def test_platform_audit_log_lists_security_events(client: AsyncClient, platform) -> None:
    _, _admin, headers = platform
    await client.post("/api/v1/auth/login", json={"email": "root@example.com", "password": "wrong password!"})
    page = (await client.get(f"{ADMIN}/audit-events", headers=headers)).json()
    types = [e["event_type"] for e in page["items"]]
    assert "auth.login.success" in types and "auth.login.failure" in types
    filtered = (await client.get(f"{ADMIN}/audit-events", params={"event_type": "auth.login.failure"}, headers=headers)).json()
    assert {e["event_type"] for e in filtered["items"]} == {"auth.login.failure"}
    assert all(e["request_id"] for e in page["items"])
