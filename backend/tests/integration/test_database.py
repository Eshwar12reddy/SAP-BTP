"""Direct database behaviour: transactions, constraints and durability (test database only)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.session import create_engine, create_session_factory
from app.models import Conversation, Message
from tests.conftest import make_settings


@pytest.fixture
async def session_factory(settings: Settings):
    engine = create_engine(settings)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_connects_to_isolated_test_database(settings: Settings) -> None:
    engine = create_engine(settings)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1
        database = await connection.scalar(text("SELECT current_database()"))
    await engine.dispose()
    assert database.endswith("_test")


async def test_commit_persists_and_rollback_discards(session_factory) -> None:
    async with session_factory() as session:
        session.add(Conversation(title="kept"))
        await session.commit()

    async with session_factory() as session:
        session.add(Conversation(title="discarded"))
        await session.flush()
        await session.rollback()

    async with session_factory() as session:
        titles = (await session.scalars(select(Conversation.title))).all()
    assert titles == ["kept"]


async def test_message_requires_existing_conversation(session_factory) -> None:
    async with session_factory() as session:
        session.add(Message(conversation_id=uuid.uuid4(), role="user", content="orphan"))
        with pytest.raises(IntegrityError) as info:
            await session.commit()
    assert info.value.orig.sqlstate == "23503"  # foreign_key_violation


async def test_database_enforces_client_message_id_uniqueness(session_factory) -> None:
    async with session_factory() as session:
        conversation = Conversation(title="dupes")
        session.add(conversation)
        await session.commit()
        client_id = uuid.uuid4()
        session.add(Message(conversation_id=conversation.id, role="user", content="a", client_message_id=client_id))
        await session.commit()
        session.add(Message(conversation_id=conversation.id, role="user", content="b", client_message_id=client_id))
        with pytest.raises(IntegrityError) as info:
            await session.commit()
    assert info.value.orig.sqlstate == "23505"  # unique_violation


async def test_messages_without_client_id_do_not_collide(session_factory) -> None:
    async with session_factory() as session:
        conversation = Conversation(title="server messages")
        session.add(conversation)
        await session.commit()
        session.add_all(
            [Message(conversation_id=conversation.id, role="assistant", content=str(i)) for i in range(2)]
        )
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(Message))
    assert count == 2


@pytest.mark.parametrize(("role", "content"), [("tool", "x"), ("user", "")])
async def test_check_constraints(session_factory, role: str, content: str) -> None:
    async with session_factory() as session:
        conversation = Conversation(title="checks")
        session.add(conversation)
        await session.commit()
        session.add(Message(conversation_id=conversation.id, role=role, content=content))
        with pytest.raises(IntegrityError) as info:
            await session.commit()
    assert info.value.orig.sqlstate == "23514"  # check_violation


async def test_data_survives_a_new_engine(clean_database: URL) -> None:
    """Simulates an application restart: a fresh engine and pool see committed data."""
    first = create_engine(make_settings(clean_database))
    async with create_session_factory(first)() as session:
        conversation = Conversation(title="durable")
        session.add(conversation)
        await session.commit()
        conversation_id = conversation.id
    await first.dispose()

    second = create_engine(make_settings(clean_database))
    async with create_session_factory(second)() as session:
        reloaded = await session.get(Conversation, conversation_id)
    await second.dispose()
    assert reloaded is not None and reloaded.title == "durable"
