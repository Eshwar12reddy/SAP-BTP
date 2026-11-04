"""Operator command line: ``python -m app.cli <command>``.

    create-admin                 one-time bootstrap of the first platform administrator
    btp add-global-account       register a global account in the BTP mapping
    btp add-subaccount           register a subaccount under a global account
    btp add-environment          register an environment (label + runtime) in a subaccount
    btp list                     show the configured mapping
    assign-legacy-conversations  give pre-authentication conversations an owner

Passwords are never accepted as arguments (they would land in shell history); they are
prompted for, or read from stdin with ``--password-stdin``.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password, password_policy_problems
from app.auth.permissions import RoleName, ScopeType
from app.core.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.models import (
    AccessGrant,
    AccountType,
    BTPAccount,
    BTPEnvironment,
    Conversation,
    Role,
    RuntimeType,
    User,
)
from app.models.user import normalize_email
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome


class CliError(Exception):
    pass


def _read_password(from_stdin: bool, email: str) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            raise CliError("Passwords do not match.")
    problems = password_policy_problems(password, email=email)
    if problems:
        raise CliError("Password " + "; ".join(problems) + ".")
    return password


async def _run(handler, args) -> None:
    engine = create_engine(get_settings())
    try:
        async with create_session_factory(engine)() as session:
            await handler(session, args)
    finally:
        await engine.dispose()


# ---------- create-admin ----------


async def create_admin(session: AsyncSession, args) -> None:
    email = normalize_email(args.email)
    existing_admin = await session.scalar(
        select(func.count()).where(
            AccessGrant.role_name == RoleName.ADMINISTRATOR.value,
            AccessGrant.scope_type == ScopeType.PLATFORM.value,
            AccessGrant.revoked_at.is_(None),
        )
    )
    if existing_admin:
        raise CliError(
            "A platform administrator already exists. Bootstrap is one-time only; "
            "use the administration UI or API to add administrators."
        )
    if await session.scalar(select(User.id).where(User.email == email)):
        raise CliError("A user with this email already exists.")
    password = _read_password(args.password_stdin, email)
    role = await session.scalar(select(Role).where(Role.name == RoleName.ADMINISTRATOR.value))
    if role is None:
        raise CliError("Roles are missing; run 'python -m alembic upgrade head' first.")

    user = User(
        id=uuid.uuid4(),
        email=email,
        display_name=" ".join(args.display_name.split()) or email,
        password_hash=await asyncio.to_thread(hash_password, password),
    )
    session.add(user)
    await session.flush()
    session.add(
        AccessGrant(
            user_id=user.id,
            role_id=role.id,
            role_name=role.name,
            scope_type=ScopeType.PLATFORM.value,
        )
    )
    audit_service.record(
        session,
        AuditEventType.ADMIN_BOOTSTRAPPED,
        Outcome.SUCCESS,
        target_user_id=user.id,
        details={"via": "cli"},
    )
    await session.commit()
    print(f"Created platform administrator {email} ({user.id}).")
    print("Note: administrators have no BTP or chat access until granted a BTP role.")


# ---------- btp mapping ----------


async def _account_by_ref(session: AsyncSession, ref: str) -> BTPAccount | None:
    try:
        account = await session.get(BTPAccount, uuid.UUID(ref))
        if account:
            return account
    except ValueError:
        pass
    return await session.scalar(select(BTPAccount).where(BTPAccount.external_account_id == ref))


def _audit_mapping(session: AsyncSession, action: str, **scope) -> None:
    audit_service.record(
        session,
        AuditEventType.BTP_MAPPING_CHANGED,
        Outcome.SUCCESS,
        details={"via": "cli", "action": action},
        **scope,
    )


async def add_global_account(session: AsyncSession, args) -> None:
    account = BTPAccount(
        id=uuid.uuid4(),
        external_account_id=args.external_id,
        name=args.name,
        account_type=AccountType.GLOBAL_ACCOUNT.value,
        region=args.region,
    )
    session.add(account)
    await _flush(session, "An account with this external id already exists.")
    _audit_mapping(session, "add_global_account", scope_type="global_account", btp_account_id=account.id)
    await _commit(session, "An account with this external id already exists.")
    print(f"global_account {account.id}  {account.name}")


async def add_subaccount(session: AsyncSession, args) -> None:
    parent = await _account_by_ref(session, args.parent)
    if parent is None or parent.account_type != AccountType.GLOBAL_ACCOUNT:
        raise CliError("--parent must reference an existing global account.")
    account = BTPAccount(
        id=uuid.uuid4(),
        external_account_id=args.external_id,
        name=args.name,
        account_type=AccountType.SUBACCOUNT.value,
        region=args.region,
        parent_account_id=parent.id,
        parent_account_type=AccountType.GLOBAL_ACCOUNT.value,
    )
    session.add(account)
    await _flush(session, "An account with this external id already exists.")
    _audit_mapping(session, "add_subaccount", scope_type="subaccount", btp_account_id=account.id)
    await _commit(session, "An account with this external id already exists.")
    print(f"subaccount {account.id}  {account.name}  (in {parent.name})")


async def add_environment(session: AsyncSession, args) -> None:
    account = await _account_by_ref(session, args.account)
    if account is None or account.account_type != AccountType.SUBACCOUNT:
        raise CliError("--account must reference an existing subaccount.")
    environment = BTPEnvironment(
        id=uuid.uuid4(),
        btp_account_id=account.id,
        name=args.name,
        runtime_type=args.runtime,
        external_runtime_id=args.external_runtime_id,
    )
    session.add(environment)
    await _flush(session, "This environment name or runtime id is already registered.")
    _audit_mapping(session, "add_environment", scope_type="environment", btp_environment_id=environment.id)
    await _commit(session, "This environment name or runtime id is already registered.")
    print(f"environment {environment.id}  {environment.name} ({environment.runtime_type}) in {account.name}")


async def list_mapping(session: AsyncSession, args) -> None:
    accounts = list(await session.scalars(select(BTPAccount).order_by(BTPAccount.name)))
    environments = list(await session.scalars(select(BTPEnvironment).order_by(BTPEnvironment.name)))
    if not accounts:
        print("No BTP accounts are configured.")
    for ga in (a for a in accounts if a.account_type == AccountType.GLOBAL_ACCOUNT):
        print(f"{ga.id}  global_account  {ga.name}  [{ga.external_account_id}]")
        for sa in (a for a in accounts if a.parent_account_id == ga.id):
            print(f"  {sa.id}  subaccount  {sa.name}  [{sa.external_account_id}]")
            for env in (e for e in environments if e.btp_account_id == sa.id):
                print(f"    {env.id}  environment  {env.name}  ({env.runtime_type})")


async def _flush(session: AsyncSession, duplicate_message: str) -> None:
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise CliError(duplicate_message) from None


async def _commit(session: AsyncSession, duplicate_message: str) -> None:
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise CliError(duplicate_message) from None


# ---------- legacy conversations ----------


async def assign_legacy_conversations(session: AsyncSession, args) -> None:
    email = normalize_email(args.email)
    owner = await session.scalar(select(User).where(User.email == email))
    if owner is None:
        raise CliError("No user with that email.")
    query = select(Conversation.id, Conversation.title).where(Conversation.owner_id.is_(None))
    if args.conversation_id:
        query = query.where(Conversation.id.in_([uuid.UUID(c) for c in args.conversation_id]))
    elif not args.all:
        raise CliError("Pass --all or one or more --conversation-id values.")
    rows = (await session.execute(query.order_by(Conversation.created_at))).all()
    if not rows:
        print("No matching legacy conversations.")
        return
    print(f"{len(rows)} legacy conversation(s) will be owned by {email}:")
    for conversation_id, title in rows:
        print(f"  {conversation_id}  {title}")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return
    ids = [r[0] for r in rows]
    # owner_id IS NULL in the WHERE makes this safe against concurrent assignment.
    result = await session.execute(
        update(Conversation)
        .where(Conversation.id.in_(ids), Conversation.owner_id.is_(None))
        .values(owner_id=owner.id)
    )
    audit_service.record(
        session,
        AuditEventType.LEGACY_CONVERSATIONS_ASSIGNED,
        Outcome.SUCCESS,
        target_user_id=owner.id,
        details={"via": "cli", "count": result.rowcount, "conversation_ids": [str(i) for i in ids]},
    )
    await session.commit()
    print(f"Assigned {result.rowcount} conversation(s).")


# ---------- entry point ----------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    admin = commands.add_parser("create-admin", help="bootstrap the first platform administrator")
    admin.add_argument("--email", required=True)
    admin.add_argument("--display-name", required=True)
    admin.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    admin.set_defaults(handler=create_admin)

    btp = commands.add_parser("btp", help="manage the configured BTP account mapping")
    btp_commands = btp.add_subparsers(dest="btp_command", required=True)
    ga = btp_commands.add_parser("add-global-account")
    ga.add_argument("--external-id", required=True, help="global account GUID in SAP BTP")
    ga.add_argument("--name", required=True)
    ga.add_argument("--region")
    ga.set_defaults(handler=add_global_account)
    sa = btp_commands.add_parser("add-subaccount")
    sa.add_argument("--external-id", required=True, help="subaccount GUID in SAP BTP")
    sa.add_argument("--name", required=True)
    sa.add_argument("--parent", required=True, help="global account id or external id")
    sa.add_argument("--region")
    sa.set_defaults(handler=add_subaccount)
    env = btp_commands.add_parser("add-environment")
    env.add_argument("--account", required=True, help="subaccount id or external id")
    env.add_argument("--name", required=True, help='label, e.g. "Development"')
    env.add_argument("--runtime", required=True, choices=[r.value for r in RuntimeType])
    env.add_argument("--external-runtime-id", help="e.g. Cloud Foundry org GUID")
    env.set_defaults(handler=add_environment)
    ls = btp_commands.add_parser("list")
    ls.set_defaults(handler=list_mapping)

    legacy = commands.add_parser(
        "assign-legacy-conversations", help="give ownerless (pre-auth) conversations an owner"
    )
    legacy.add_argument("--email", required=True)
    legacy.add_argument("--all", action="store_true")
    legacy.add_argument("--conversation-id", action="append")
    legacy.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    legacy.set_defaults(handler=assign_legacy_conversations)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        asyncio.run(_run(args.handler, args), loop_factory=loop_factory)
    except CliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
