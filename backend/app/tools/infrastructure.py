"""Narrow typed infrastructure-plan tools; all are static and non-mutating."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.auth.permissions import Permission
from app.schemas.infrastructure_plan import (
    InfrastructurePlan,
    InfrastructurePlanCompareResponse,
    InfrastructurePlanRequirements,
    InfrastructurePlanRequirementsAnalysis,
)
from app.tools.contracts import OperationType, SourceType, Tool, ToolInput, ToolOutput
from app.tools.registry import ToolRegistry

INFRASTRUCTURE_PLAN_ANALYZE_TOOL = "btp_infrastructure_plan_analyze"
INFRASTRUCTURE_PLAN_GENERATE_TOOL = "btp_infrastructure_plan_generate"
INFRASTRUCTURE_PLAN_VALIDATE_TOOL = "btp_infrastructure_plan_validate"
INFRASTRUCTURE_PLAN_COMPARE_TOOL = "btp_infrastructure_plan_compare"


class InfrastructurePlanInput(ToolInput):
    requirements: InfrastructurePlanRequirements


class InfrastructurePlanOutput(ToolOutput):
    plan: InfrastructurePlan


class InfrastructureAnalyzeOutput(ToolOutput):
    analysis: InfrastructurePlanRequirementsAnalysis


class InfrastructureValidateInput(ToolInput):
    plan: InfrastructurePlan


class InfrastructureValidateOutput(ToolOutput):
    plan: InfrastructurePlan


class InfrastructureCompareInput(ToolInput):
    plan_id: uuid.UUID
    left_revision: int = Field(ge=1)
    right_revision: int = Field(ge=1)


class InfrastructureCompareOutput(ToolOutput):
    comparison: InfrastructurePlanCompareResponse


def _service(context):
    if context.infrastructure_plan_service is None:
        from app.tools.contracts import ToolUnavailable
        raise ToolUnavailable("infrastructure_plan_service_unavailable")
    return context.infrastructure_plan_service


async def analyze_plan(arguments: InfrastructurePlanInput, context):
    return InfrastructureAnalyzeOutput(analysis=await _service(context).analyze(context.session, context.principal, arguments.requirements))


async def generate_plan(arguments: InfrastructurePlanInput, context):
    return InfrastructurePlanOutput(plan=await _service(context).generate(context.session, context.principal, arguments.requirements))


async def validate_plan(arguments: InfrastructureValidateInput, context):
    return InfrastructureValidateOutput(plan=_service(context).validate(arguments.plan))


async def compare_plan(arguments: InfrastructureCompareInput, context):
    return InfrastructureCompareOutput(comparison=await _service(context).compare(context.session, context.principal, arguments.plan_id, arguments.left_revision, arguments.right_revision))


INFRASTRUCTURE_TOOLS = (
    Tool(name=INFRASTRUCTURE_PLAN_ANALYZE_TOOL, description="Analyze supported static infrastructure-plan inputs without executing them.", input_model=InfrastructurePlanInput, output_model=InfrastructureAnalyzeOutput, required_permission=Permission.CHAT_USE, operation_type=OperationType.READ, source_type=SourceType.CALCULATION, run=analyze_plan),
    Tool(name=INFRASTRUCTURE_PLAN_GENERATE_TOOL, description="Generate deterministic review artifacts from an authorized architecture and optional sizing result; never deploys.", input_model=InfrastructurePlanInput, output_model=InfrastructurePlanOutput, required_permission=Permission.CHAT_USE, operation_type=OperationType.READ, source_type=SourceType.CALCULATION, run=generate_plan),
    Tool(name=INFRASTRUCTURE_PLAN_VALIDATE_TOOL, description="Validate an existing infrastructure plan statically without provider calls.", input_model=InfrastructureValidateInput, output_model=InfrastructureValidateOutput, required_permission=Permission.CHAT_USE, operation_type=OperationType.READ, source_type=SourceType.CALCULATION, run=validate_plan),
    Tool(name=INFRASTRUCTURE_PLAN_COMPARE_TOOL, description="Compare owner-scoped immutable infrastructure plan revisions.", input_model=InfrastructureCompareInput, output_model=InfrastructureCompareOutput, required_permission=Permission.CHAT_USE, operation_type=OperationType.READ, source_type=SourceType.CALCULATION, run=compare_plan),
)


def register_infrastructure_tools(registry: ToolRegistry) -> None:
    for tool in INFRASTRUCTURE_TOOLS:
        registry.register(tool)
