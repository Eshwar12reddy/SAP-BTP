"""Read-only BTP tools. Each re-authorizes the resource against the caller's own scope.

``btp_catalog_read`` reports the account mapping stored in this application, which is
not live BTP data. ``btp_applications_list`` asks the live BTP adapter; while no live
integration is configured it reports ``btp_integration_unavailable`` and returns nothing.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.auth.authorization import coverage_for
from app.auth.permissions import Permission
from app.integrations.sap_btp.provider import BTPIntegrationUnavailable
from app.models import BTPEnvironment
from app.services import btp_service
from app.tools.contracts import (
    OperationType,
    SourceType,
    Tool,
    ToolDenied,
    ToolInput,
    ToolOutput,
    ToolUnavailable,
)
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.agents.contracts import AgentContext

CATALOG_TOOL = "btp_catalog_read"
APPLICATIONS_TOOL = "btp_applications_list"


class CatalogInput(ToolInput):
    pass


class CatalogAccount(BaseModel):
    id: uuid.UUID
    name: str
    account_type: str
    region: str | None


class CatalogEnvironment(BaseModel):
    id: uuid.UUID
    name: str
    runtime_type: str
    # Only present when the caller can also read the parent subaccount.
    account_id: uuid.UUID | None
    account_name: str | None


class CatalogOutput(ToolOutput):
    accounts: list[CatalogAccount]
    environments: list[CatalogEnvironment]


class ApplicationsInput(ToolInput):
    environment_id: uuid.UUID


class Application(BaseModel):
    id: str
    name: str
    state: str | None


class ApplicationsOutput(ToolOutput):
    environment_id: uuid.UUID
    environment_name: str
    provider: str
    applications: list[Application]


async def read_catalog(arguments: CatalogInput, context: AgentContext) -> CatalogOutput:
    coverage = await coverage_for(context.session, context.principal, Permission.BTP_READ)
    accounts, environments = await btp_service.authorized_catalog(context.session, coverage)
    return CatalogOutput(
        accounts=[
            CatalogAccount(id=a.id, name=a.name, account_type=a.account_type, region=a.region)
            for a in accounts
        ],
        environments=[
            CatalogEnvironment(
                id=e.environment.id,
                name=e.environment.name,
                runtime_type=e.environment.runtime_type,
                account_id=e.account_id,
                account_name=e.account_name,
            )
            for e in environments
        ],
    )


async def list_applications(
    arguments: ApplicationsInput, context: AgentContext
) -> ApplicationsOutput:
    coverage = await coverage_for(context.session, context.principal, Permission.BTP_READ)
    if not coverage.covers_environment(arguments.environment_id):
        raise ToolDenied()
    environment = await context.session.get(BTPEnvironment, arguments.environment_id)
    if environment is None:
        raise ToolDenied()
    provider = context.btp_provider
    try:
        applications = await provider.list_applications(environment)
    except BTPIntegrationUnavailable:
        raise ToolUnavailable("btp_integration_unavailable") from None
    return ApplicationsOutput(
        environment_id=environment.id,
        environment_name=environment.name,
        provider=provider.name,
        applications=[Application(id=a.id, name=a.name, state=a.state) for a in applications],
    )


BTP_TOOLS = (
    Tool(
        name=CATALOG_TOOL,
        description="List the BTP accounts and environments the user is authorized to read "
        "(configured mapping stored in this application).",
        input_model=CatalogInput,
        output_model=CatalogOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.STORED_METADATA,
        run=read_catalog,
    ),
    Tool(
        name=APPLICATIONS_TOOL,
        description="List applications in one authorized environment via the live BTP adapter.",
        input_model=ApplicationsInput,
        output_model=ApplicationsOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=list_applications,
        timeout_seconds=30.0,
    ),
)


def register_btp_tools(registry: ToolRegistry) -> None:
    for tool in BTP_TOOLS:
        registry.register(tool)
