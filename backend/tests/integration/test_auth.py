"""Login, sessions, refresh rotation, replay detection, CSRF, logout, password changes,
rate limiting."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.auth.permissions import RoleName
from app.models import AuditEvent, Session, SessionRefreshToken, User
from tests.support import (
    PASSWORD,
    client_for,
    create_tree,
    create_user,
    csrf_headers,
    grant,
    login,
)

AUTH = "/api/v1/auth"


async def audit_count(client: AsyncClient, event_type: str, outcome: str | None = None) -> int:
    async with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        query = select(func.count()).where(AuditEvent.event_type == event_type)
        if outcome:
            query = query.where(AuditEvent.outcome == outcome)
        return await session.scalar(query)


async def refresh(client: AsyncClient, **headers):
    return await client.post(f"{AUTH}/refresh", headers=csrf_headers(client) | headers)


# ---------- login ----------


async def test_login_issues_tokens_and_secure_cookies(client: AsyncClient) -> None:
    await create_user(client.app, "alice@example.com")
    response = await client.post(
        f"{AUTH}/login", json={"email": "  Alice@Example.COM ", "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer" and body["user"]["email"] == "alice@example.com"
    assert "password" not in response.text and "password_hash" not in response.text
    assert response.headers["cache-control"] == "no-store"

    cookies = {c.split("=")[0]: c.lower() for c in response.headers.get_list("set-cookie")}
    refresh_cookie = cookies["btp_refresh"]
    assert "httponly" in refresh_cookie and "secure" in refresh_cookie
    assert "samesite=strict" in refresh_cookie and "path=/api/v1/auth" in refresh_cookie
    csrf_cookie = cookies["btp_csrf"]
    assert "httponly" not in csrf_cookie and "secure" in csrf_cookie and "samesite=strict" in csrf_cookie
    # The refresh token is never in the body (it lives only in the HttpOnly cookie).
    assert client.cookies.get("btp_refresh") not in response.text
    assert await audit_count(client, "auth.login.success") == 1


@pytest.mark.parametrize("case", ["wrong_password", "unknown_user", "disabled"])
async def test_login_failures_are_generic(client: AsyncClient, case: str) -> None:
    await create_user(client.app, "bob@example.com", active=case != "disabled")
    email = "nobody@example.com" if case == "unknown_user" else "bob@example.com"
    password = "wrong password!!" if case == "wrong_password" else PASSWORD
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert response.json()["error"]["message"] == "Invalid email or password."
    assert "set-cookie" not in response.headers
    assert await audit_count(client, "auth.login.failure", "failure") == 1


async def test_login_rate_limited_per_email(client: AsyncClient) -> None:
    await create_user(client.app, "carol@example.com")
    for _ in range(5):
        r = await client.post(f"{AUTH}/login", json={"email": "carol@example.com", "password": "nope nope nope"})
        assert r.status_code == 401
    # Even the correct password is refused while the limit applies.
    blocked = await client.post(f"{AUTH}/login", json={"email": "carol@example.com", "password": PASSWORD})
    assert blocked.status_code == 429
    assert 0 < int(blocked.headers["retry-after"]) <= 900
    assert await audit_count(client, "auth.login.rate_limited", "denied") == 1
    # Other accounts are unaffected (below the per-IP limit).
    await create_user(client.app, "dave@example.com")
    assert (await client.post(f"{AUTH}/login", json={"email": "dave@example.com", "password": PASSWORD})).status_code == 200


async def test_login_rate_limited_per_ip(client: AsyncClient) -> None:
    for i in range(20):
        await client.post(f"{AUTH}/login", json={"email": f"user{i}@example.com", "password": "nope nope nope"})
    await create_user(client.app, "erin@example.com")
    blocked = await client.post(f"{AUTH}/login", json={"email": "erin@example.com", "password": PASSWORD})
    assert blocked.status_code == 429


async def test_login_input_validation(client: AsyncClient) -> None:
    assert (await client.post(f"{AUTH}/login", json={"email": "a@b.co"})).status_code == 422
    too_long = {"email": "a@b.co", "password": "x" * 129}
    assert (await client.post(f"{AUTH}/login", json=too_long)).status_code == 422


# ---------- access tokens & me ----------


async def test_me_requires_valid_bearer(client: AsyncClient) -> None:
    assert (await client.get(f"{AUTH}/me")).status_code == 401
    bad = await client.get(f"{AUTH}/me", headers={"Authorization": "Bearer garbage"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "token_invalid"
    assert bad.headers["www-authenticate"] == "Bearer"


async def test_me_reports_user_roles_and_permissions(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    user = await create_user(client.app, "frank@example.com")
    await grant(client.app, user, RoleName.VIEWER, tree.dev)
    await grant(client.app, user, RoleName.OPERATOR, tree.sub)
    headers = await login(client, "frank@example.com")
    body = (await client.get(f"{AUTH}/me", headers=headers)).json()
    assert body["roles"] == ["viewer", "operator"]
    assert "operations:request" in body["permissions"] and "users:read" not in body["permissions"]
    assert {(g["scope_type"], g["scope_name"]) for g in body["grants"]} == {
        ("environment", "Development"),
        ("subaccount", "Acme EU"),
    }


async def test_disabling_user_invalidates_existing_access_token(client: AsyncClient) -> None:
    await create_user(client.app, "gina@example.com")
    headers = await login(client, "gina@example.com")
    assert (await client.get(f"{AUTH}/me", headers=headers)).status_code == 200
    async with client.app.state.session_factory() as session:
        await session.execute(update(User).where(User.email == "gina@example.com").values(is_active=False))
        await session.commit()
    rejected = await client.get(f"{AUTH}/me", headers=headers)
    assert rejected.status_code == 401 and rejected.json()["error"]["code"] == "session_expired"


# ---------- refresh rotation ----------


async def test_refresh_rotates_token(client: AsyncClient) -> None:
    await create_user(client.app, "hank@example.com")
    await login(client, "hank@example.com")
    first = client.cookies.get("btp_refresh")
    response = await refresh(client)
    assert response.status_code == 200
    second = client.cookies.get("btp_refresh")
    assert second and second != first
    new_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert (await client.get(f"{AUTH}/me", headers=new_headers)).status_code == 200


async def test_refresh_requires_csrf(client: AsyncClient) -> None:
    await create_user(client.app, "ivy@example.com")
    await login(client, "ivy@example.com")
    missing = await client.post(f"{AUTH}/refresh")
    assert missing.status_code == 403 and missing.json()["error"]["code"] == "csrf_failed"
    wrong = await client.post(f"{AUTH}/refresh", headers={"X-CSRF-Token": "forged"})
    assert wrong.status_code == 403
    hostile = await refresh(client, Origin="https://evil.example")
    assert hostile.status_code == 403 and hostile.json()["error"]["code"] == "origin_not_allowed"
    assert (await refresh(client)).status_code == 200


async def test_csrf_token_is_bound_to_its_session(client: AsyncClient) -> None:
    await create_user(client.app, "jack@example.com")
    async with client_for(client.app) as other:
        await login(other, "jack@example.com")
        other_csrf = other.cookies.get("btp_csrf")
    await login(client, "jack@example.com")
    refresh_token = client.cookies.get("btp_refresh")
    # A CSRF pair from another session (e.g. a planted cookie) does not unlock this one.
    async with client_for(client.app) as attacker:
        response = await attacker.post(
            f"{AUTH}/refresh",
            headers={"X-CSRF-Token": other_csrf, "Cookie": f"btp_refresh={refresh_token}; btp_csrf={other_csrf}"},
        )
    assert response.status_code == 403 and response.json()["error"]["code"] == "csrf_failed"


async def test_reuse_of_rotated_token_revokes_session(client: AsyncClient) -> None:
    client.app.state.settings.REFRESH_REUSE_GRACE_SECONDS = 0  # type: ignore[attr-defined]
    await create_user(client.app, "kate@example.com")
    headers = await login(client, "kate@example.com")
    stolen = client.cookies.get("btp_refresh")
    assert (await refresh(client)).status_code == 200
    legit = client.cookies.get("btp_refresh")

    csrf = client.cookies.get("btp_csrf")

    async def present(refresh_token: str):
        # A fresh client, so only the explicit cookies are sent.
        async with client_for(client.app) as c:
            return await c.post(
                f"{AUTH}/refresh",
                headers={"X-CSRF-Token": csrf, "Cookie": f"btp_refresh={refresh_token}; btp_csrf={csrf}"},
            )

    # The attacker replays the old token.
    await asyncio.sleep(0.01)
    replay = await present(stolen)
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "session_revoked"
    assert await audit_count(client, "auth.refresh.reuse_detected", "denied") == 1

    # The whole session is dead: the legitimate token and the access token stop working.
    assert (await present(legit)).status_code == 401
    assert (await client.get(f"{AUTH}/me", headers=headers)).status_code == 401


async def test_concurrent_refresh_issues_exactly_one_rotation(client: AsyncClient) -> None:
    await create_user(client.app, "liam@example.com")
    headers = await login(client, "liam@example.com")
    cookie = f"btp_refresh={client.cookies.get('btp_refresh')}; btp_csrf={client.cookies.get('btp_csrf')}"
    responses = await asyncio.gather(
        *(
            client.post(f"{AUTH}/refresh", headers=csrf_headers(client) | {"Cookie": cookie})
            for _ in range(5)
        )
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 409, 409, 409, 409]
    assert all(r.json()["error"]["code"] == "refresh_in_progress" for r in responses if r.status_code == 409)
    # Losing the race is not treated as theft: the session survives.
    assert (await client.get(f"{AUTH}/me", headers=headers)).status_code == 200
    async with client.app.state.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SessionRefreshToken)) == 2


async def test_refresh_respects_idle_and_absolute_expiry(client: AsyncClient) -> None:
    await create_user(client.app, "mia@example.com")
    for column in ("idle_expires_at", "absolute_expires_at"):
        async with client_for(client.app) as c:
            await login(c, "mia@example.com")
            past = datetime.now(UTC) - timedelta(seconds=1)
            async with client.app.state.session_factory() as session:
                values = {column: past}
                if column == "absolute_expires_at":
                    values["idle_expires_at"] = past
                await session.execute(update(Session).where(Session.revoked_at.is_(None)).values(**values))
                await session.commit()
            response = await refresh(c)
            assert response.status_code == 401, column
            assert response.json()["error"]["code"] == "session_expired"


async def test_refresh_extends_idle_but_never_past_absolute(client: AsyncClient) -> None:
    await create_user(client.app, "noah@example.com")
    await login(client, "noah@example.com")
    soon = datetime.now(UTC) + timedelta(hours=1)
    async with client.app.state.session_factory() as session:
        await session.execute(update(Session).values(absolute_expires_at=soon, idle_expires_at=soon))
        await session.commit()
    assert (await refresh(client)).status_code == 200
    async with client.app.state.session_factory() as session:
        row = await session.scalar(select(Session))
    assert row.idle_expires_at <= row.absolute_expires_at


# ---------- logout ----------


async def test_logout_revokes_session(client: AsyncClient) -> None:
    await create_user(client.app, "olga@example.com")
    headers = await login(client, "olga@example.com")
    response = await client.post(f"{AUTH}/logout", headers=headers)
    assert response.status_code == 204
    assert any("btp_refresh=" in c and "max-age=0" in c.lower() for c in response.headers.get_list("set-cookie"))
    assert (await client.get(f"{AUTH}/me", headers=headers)).status_code == 401
    assert await audit_count(client, "auth.logout") == 1


async def test_logout_with_refresh_cookie_when_access_token_is_gone(client: AsyncClient) -> None:
    await create_user(client.app, "paul@example.com")
    headers = await login(client, "paul@example.com")
    response = await client.post(f"{AUTH}/logout", headers=csrf_headers(client))
    assert response.status_code == 204
    assert (await client.get(f"{AUTH}/me", headers=headers)).status_code == 401


async def test_logout_all_ends_every_session(client: AsyncClient) -> None:
    await create_user(client.app, "quinn@example.com")
    async with client_for(client.app) as laptop, client_for(client.app) as phone:
        laptop_headers = await login(laptop, "quinn@example.com")
        phone_headers = await login(phone, "quinn@example.com")
        assert (await laptop.post(f"{AUTH}/logout-all", headers=laptop_headers)).status_code == 204
        assert (await phone.get(f"{AUTH}/me", headers=phone_headers)).status_code == 401
        assert (await refresh(phone)).status_code == 401
    assert await audit_count(client, "auth.logout_all") == 1


# ---------- password change ----------


async def test_change_password(client: AsyncClient) -> None:
    await create_user(client.app, "rita@example.com")
    async with client_for(client.app) as other_device:
        other_headers = await login(other_device, "rita@example.com")
        headers = await login(client, "rita@example.com")

        wrong = await client.post(
            f"{AUTH}/change-password",
            headers=headers,
            json={"current_password": "not my password", "new_password": "a brand new passphrase"},
        )
        assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "invalid_current_password"
        weak = await client.post(
            f"{AUTH}/change-password",
            headers=headers,
            json={"current_password": PASSWORD, "new_password": "short"},
        )
        assert weak.status_code == 400 and weak.json()["error"]["code"] == "weak_password"
        same = await client.post(
            f"{AUTH}/change-password",
            headers=headers,
            json={"current_password": PASSWORD, "new_password": PASSWORD},
        )
        assert same.status_code == 400

        ok = await client.post(
            f"{AUTH}/change-password",
            headers=headers,
            json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
        )
        assert ok.status_code == 200 and ok.json() == {"other_sessions_revoked": 1}
        # This session continues; the other device is signed out.
        assert (await client.get(f"{AUTH}/me", headers=headers)).status_code == 200
        assert (await other_device.get(f"{AUTH}/me", headers=other_headers)).status_code == 401

    old = await client.post(f"{AUTH}/login", json={"email": "rita@example.com", "password": PASSWORD})
    assert old.status_code == 401
    await login(client, "rita@example.com", "a brand new passphrase")
    assert await audit_count(client, "auth.password.changed") == 1
    assert await audit_count(client, "auth.password.change_failed") == 1


# ---------- no public registration ----------


async def test_there_is_no_self_registration(client: AsyncClient) -> None:
    payload = {"email": "new@example.com", "display_name": "New", "password": "a long password"}
    for path in ("/api/v1/auth/register", "/api/v1/users", "/api/v1/auth/signup"):
        assert (await client.post(path, json=payload)).status_code in (404, 405)
    assert (await client.post("/api/v1/admin/users", json=payload)).status_code == 401
