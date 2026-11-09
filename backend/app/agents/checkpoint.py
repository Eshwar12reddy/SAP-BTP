"""Controlled LangGraph checkpoint lifecycle.

Normal application startup uses an in-memory saver unless a dedicated checkpoint database
is explicitly enabled.  This module never calls ``setup``; schema creation is an explicit
operator action so startup cannot mutate the application database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import Settings


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver]:
    if not settings.LANGGRAPH_CHECKPOINT_ENABLED:
        yield InMemorySaver()
        return
    connection_string = settings.langgraph_checkpoint_database_url.set(
        drivername="postgresql"
    ).render_as_string(hide_password=False)
    async with AsyncPostgresSaver.from_conn_string(connection_string) as saver:
        yield saver


async def setup_postgres_checkpointer(settings: Settings) -> None:
    """Explicitly create/checkpoint-migrate a configured checkpoint database."""

    connection_string = settings.langgraph_checkpoint_database_url.set(
        drivername="postgresql"
    ).render_as_string(hide_password=False)
    async with AsyncPostgresSaver.from_conn_string(connection_string) as saver:
        await saver.setup()


async def prune_checkpoint_threads(saver: BaseCheckpointSaver, thread_ids: list[str]) -> None:
    """Keep only the latest checkpoint for explicitly selected threads.

    Retention is operator-controlled; this helper never discovers or deletes threads on its
    own.  The PostgreSQL saver owns the pruning SQL and concurrency behavior.
    """

    prune = getattr(saver, "aprune", None)
    if prune is None:
        return
    await prune(thread_ids, strategy="keep_latest")
