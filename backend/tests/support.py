"""Test factories: users, grants, BTP hierarchy and authenticated clients."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.auth.permissions import RoleName, ScopeType
from app.models import AccessGrant, AccountType, BTPAccount, BTPEnvironment, Role, User

PASSWORD = "correct horse battery staple"


@dataclass
class BTPTree:
    """Two global accounts, each with subaccounts and Development/Testing/Production."""

    ga: BTPAccount
    sub: BTPAccount
    dev: BTPEnvironment
    test: BTPEnvironment
    prod: BTPEnvironment
    sub2: BTPAccount
    sub2_dev: BTPEnvironment
    other_ga: BTPAccount
    other_sub: BTPAccount
    other_dev: BTPEnvironment


async def create_user(
    app: FastAPI,
    email: str,
    *,
    password: str = PASSWORD,
    display_name: str | None = None,
    active: bool = True,
) -> User:
    async with app.state.session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            email=email.lower(),
            display_name=display_name or email.split("@")[0],
            password_hash=hash_password(password),
            is_active=active,
        )
        session.add(user)
        await session.commit()
        return user


async def grant(
    app: FastAPI,
    user: User,
    role: RoleName,
    scope: BTPAccount | BTPEnvironment | None = None,
    *,
    include_descendants: bool = False,
    expires_at: datetime | None = None,
    revoked: bool = False,
) -> AccessGrant:
    async with app.state.session_factory() as session:
        role_row = await session.scalar(select(Role).where(Role.name == role.value))
        fields: dict = {"scope_type": ScopeType.PLATFORM.value}
        if isinstance(scope, BTPEnvironment):
            fields = {"scope_type": ScopeType.ENVIRONMENT.value, "btp_environment_id": scope.id}
        elif isinstance(scope, BTPAccount):
            fields = {"scope_type": scope.account_type, "btp_account_id": scope.id}
        row = AccessGrant(
            id=uuid.uuid4(),
            user_id=user.id,
            role_id=role_row.id,
            role_name=role_row.name,
            include_descendants=include_descendants,
            expires_at=expires_at,
            **fields,
        )
        session.add(row)
        await session.commit()
        if revoked:
            row.revoked_at = datetime.now(row.created_at.tzinfo)
            await session.commit()
        return row


async def create_tree(app: FastAPI) -> BTPTree:
    async with app.state.session_factory() as session:

        def account(name: str, kind: AccountType, parent: BTPAccount | None = None) -> BTPAccount:
            row = BTPAccount(
                id=uuid.uuid4(),
                external_account_id=f"ext-{uuid.uuid4()}",
                name=name,
                account_type=kind.value,
                parent_account_id=parent.id if parent else None,
                parent_account_type=AccountType.GLOBAL_ACCOUNT.value if parent else None,
            )
            session.add(row)
            return row

        def environment(sub: BTPAccount, name: str) -> BTPEnvironment:
            row = BTPEnvironment(id=uuid.uuid4(), btp_account_id=sub.id, name=name, runtime_type="cloudfoundry")
            session.add(row)
            return row

        ga = account("Acme Global", AccountType.GLOBAL_ACCOUNT)
        other_ga = account("Other Global", AccountType.GLOBAL_ACCOUNT)
        await session.flush()
        sub = account("Acme EU", AccountType.SUBACCOUNT, ga)
        sub2 = account("Acme US", AccountType.SUBACCOUNT, ga)
        other_sub = account("Other EU", AccountType.SUBACCOUNT, other_ga)
        await session.flush()
        tree = BTPTree(
            ga=ga,
            sub=sub,
            dev=environment(sub, "Development"),
            test=environment(sub, "Testing"),
            prod=environment(sub, "Production"),
            sub2=sub2,
            sub2_dev=environment(sub2, "Development"),
            other_ga=other_ga,
            other_sub=other_sub,
            other_dev=environment(other_sub, "Development"),
        )
        await session.commit()
        return tree


@asynccontextmanager
async def client_for(app: FastAPI):
    """An independent client (own cookie jar) for the same running app."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    """Log in on ``client`` (cookies land in its jar) and return the bearer header."""
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def csrf_headers(client: AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("btp_csrf") or ""}


async def user_with_role(
    app: FastAPI,
    client: AsyncClient,
    email: str,
    role: RoleName,
    scope: BTPAccount | BTPEnvironment | None = None,
    **grant_kwargs,
) -> tuple[User, dict[str, str]]:
    user = await create_user(app, email)
    await grant(app, user, role, scope, **grant_kwargs)
    return user, await login(client, email)
