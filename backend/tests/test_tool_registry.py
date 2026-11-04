"""Tool registry execution boundary, with fake tools and an in-memory principal."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.agents.contracts import AgentContext
from app.auth.authorization import Grant, Principal
from app.auth.permissions import Permission, RoleName, ScopeType
from app.integrations.sap_btp.provider import UnavailableBTPProvider
from app.models import User
from app.tools.btp import BTP_TOOLS, register_btp_tools
from app.tools.contracts import (
    OperationType,
    SourceType,
    Tool,
    ToolDenied,
    ToolInput,
    ToolOutput,
    ToolStatus,
    ToolUnavailable,
)
from app.tools.registry import ToolRegistry


class EchoInput(ToolInput):
    count: int


class EchoOutput(ToolOutput):
    count: int


def principal(role: RoleName | None = RoleName.VIEWER) -> Principal:
    grants = ()
    if role is not None:
        grants = (
            Grant(uuid.uuid4(), role, ScopeType.ENVIRONMENT, None, uuid.uuid4(), False, None),
        )
    return Principal(user=User(id=uuid.uuid4()), session_id=uuid.uuid4(), grants=grants)


def context(role: RoleName | None = RoleName.VIEWER) -> AgentContext:
    # No session_factory: denials are not audited here (the integration tests cover that).
    return AgentContext(
        principal=principal(role), session=None, btp_provider=UnavailableBTPProvider()  # type: ignore[arg-type]
    )


def tool(name: str = "echo_tool", run=None, **overrides) -> Tool:
    async def echo(arguments: EchoInput, _: AgentContext) -> EchoOutput:
        return EchoOutput(count=arguments.count)

    fields = {
        "name": name,
        "description": "test tool",
        "input_model": EchoInput,
        "output_model": EchoOutput,
        "required_permission": Permission.BTP_READ,
        "operation_type": OperationType.READ,
        "source_type": SourceType.STORED_METADATA,
        "run": run or echo,
    }
    fields.update(overrides)
    return Tool(**fields)


def registry(*tools: Tool) -> ToolRegistry:
    result = ToolRegistry()
    for item in tools:
        result.register(item)
    return result


def test_registration_lookup_and_listing() -> None:
    reg = ToolRegistry()
    register_btp_tools(reg)
    assert [t.name for t in reg.list_tools()] == [t.name for t in BTP_TOOLS]
    assert reg.get("btp_applications_list").source_type is SourceType.LIVE_BTP
    assert reg.get("btp_catalog_read").source_type is SourceType.STORED_METADATA
    assert all(t.operation_type is OperationType.READ for t in reg.list_tools())
    assert reg.get("shell_exec") is None


@pytest.mark.parametrize("name", ["", "Echo", "x", "echo-tool", "os.system", "a" * 65])
def test_invalid_tool_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="invalid tool name"):
        ToolRegistry().register(tool(name))


def test_duplicate_registration_is_rejected() -> None:
    reg = registry(tool())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(tool())


async def test_success_returns_typed_data_without_live_claim() -> None:
    result = await registry(tool()).execute("echo_tool", {"count": 3}, context())
    assert result.status is ToolStatus.SUCCESS
    assert result.data == {"count": 3}
    assert result.source_type is SourceType.STORED_METADATA
    assert result.live_check_performed is False
    assert result.timestamp.tzinfo is not None


async def test_live_flag_is_set_by_registry_only_for_successful_live_tools() -> None:
    live = registry(tool(source_type=SourceType.LIVE_BTP))
    assert (await live.execute("echo_tool", {"count": 1}, context())).live_check_performed

    async def unavailable(arguments: EchoInput, _: AgentContext) -> EchoOutput:
        raise ToolUnavailable("btp_integration_unavailable")

    failed = registry(tool(source_type=SourceType.LIVE_BTP, run=unavailable))
    result = await failed.execute("echo_tool", {"count": 1}, context())
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.error_code == "btp_integration_unavailable"
    assert result.live_check_performed is False
    assert result.data is None


async def test_tool_output_cannot_smuggle_a_live_claim() -> None:
    class Sneaky(ToolOutput):
        count: int
        live_check_performed: bool

    async def sneaky(arguments: EchoInput, _: AgentContext) -> Sneaky:
        return Sneaky(count=1, live_check_performed=True)

    result = await registry(tool(output_model=Sneaky, run=sneaky)).execute(
        "echo_tool", {"count": 1}, context()
    )
    assert result.live_check_performed is False  # only data carries the tool's field


async def test_unregistered_tool_is_rejected() -> None:
    result = await registry(tool()).execute("btp_delete_everything", {}, context())
    assert result.status is ToolStatus.DENIED
    assert result.error_code == "tool_not_registered"
    assert result.source_type is None


async def test_write_tools_never_execute() -> None:
    ran = False

    async def write(arguments: EchoInput, _: AgentContext) -> EchoOutput:
        nonlocal ran
        ran = True
        return EchoOutput(count=0)

    reg = registry(tool(operation_type=OperationType.WRITE, run=write))
    result = await reg.execute("echo_tool", {"count": 1}, context(RoleName.OPERATOR))
    assert (result.status, result.error_code) == (ToolStatus.DENIED, "write_tools_disabled")
    assert not ran


async def test_missing_permission_is_denied_before_validation() -> None:
    reg = registry(tool(required_permission=Permission.BTP_READ))
    result = await reg.execute("echo_tool", {"bogus": True}, context(RoleName.ADMINISTRATOR))
    assert (result.status, result.error_code) == (ToolStatus.DENIED, "missing_permission")
    no_grants = await reg.execute("echo_tool", {"count": 1}, context(None))
    assert no_grants.status is ToolStatus.DENIED


@pytest.mark.parametrize(
    "arguments",
    [{}, {"count": "many"}, {"count": 1, "extra": "x"}, {"count": [1]}],
)
async def test_invalid_input_is_rejected_without_echoing_values(arguments: dict) -> None:
    result = await registry(tool()).execute("echo_tool", arguments, context())
    assert (result.status, result.error_code) == (ToolStatus.INVALID_INPUT, "invalid_tool_input")
    assert result.data is None


async def test_denied_unavailable_timeout_and_failure_map_to_statuses() -> None:
    async def denied(arguments: EchoInput, _: AgentContext) -> EchoOutput:
        raise ToolDenied()

    async def slow(arguments: EchoInput, _: AgentContext) -> EchoOutput:
        await asyncio.sleep(5)
        return EchoOutput(count=1)

    async def broken(arguments: EchoInput, _: AgentContext) -> EchoOutput:
        raise RuntimeError("password=hunter2 at db.internal")

    async def bad_output(arguments: EchoInput, _: AgentContext) -> dict:
        return {"unexpected": True}

    cases = [
        (tool(run=denied), ToolStatus.DENIED, "not_found_or_not_authorized"),
        (tool(run=slow, timeout_seconds=0.05), ToolStatus.FAILED, "tool_timeout"),
        (tool(run=broken), ToolStatus.FAILED, "tool_failed"),
        (tool(run=bad_output), ToolStatus.FAILED, "tool_failed"),
    ]
    for item, status, code in cases:
        result = await registry(item).execute("echo_tool", {"count": 1}, context())
        assert (result.status, result.error_code) == (status, code)
        assert result.data is None
        assert "hunter2" not in result.model_dump_json()
