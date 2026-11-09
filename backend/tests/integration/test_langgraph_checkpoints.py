"""Isolated PostgreSQL checkpoint verification."""

from __future__ import annotations

import uuid
from typing import TypedDict

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.engine import URL


class CheckpointState(TypedDict):
    value: str


def _connection_string(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.mark.asyncio
async def test_postgres_checkpoint_survives_saver_reopen(test_database_url: URL) -> None:
    thread_id = f"langgraph-test-{uuid.uuid4()}"

    async def write_value(state: CheckpointState) -> dict[str, str]:
        return {"value": state["value"]}

    builder = StateGraph(CheckpointState)
    builder.add_node("write_value", write_value)
    builder.add_edge(START, "write_value")
    builder.add_edge("write_value", END)
    config = {"configurable": {"thread_id": thread_id}}
    connection_string = _connection_string(test_database_url)

    try:
        async with AsyncPostgresSaver.from_conn_string(connection_string) as saver:
            await saver.setup()
            graph = builder.compile(checkpointer=saver)
            result = await graph.ainvoke({"value": "checkpointed"}, config)
            assert result["value"] == "checkpointed"

        async with AsyncPostgresSaver.from_conn_string(connection_string) as saver:
            graph = builder.compile(checkpointer=saver)
            snapshot = await graph.aget_state(config)
            assert snapshot.values["value"] == "checkpointed"
    finally:
        async with AsyncPostgresSaver.from_conn_string(connection_string) as saver:
            await saver.adelete_thread(thread_id)
