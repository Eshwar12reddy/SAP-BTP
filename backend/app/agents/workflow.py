"""LangGraph workflow for the single secure BTP operations agent.

The graph owns lifecycle, routing checkpoints and resumability.  The existing
``SingleAgent`` remains the compatibility execution adapter for the already-tested route,
prompt, tool and response behavior.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from app.agents.contracts import (
    AgentContext,
    AgentOutcome,
    AgentRequest,
    AgentResponse,
    Route,
    RoutingDecision,
    RoutingMethod,
)
from app.agents.errors import AgentError
from app.agents.evidence import build_evidence
from app.agents.investigation import (
    FindingConfidence,
    FindingSeverity,
    InvestigationFinding,
    InvestigationPlan,
    InvestigationStep,
)
from app.agents.single_agent import SingleAgent
from app.agents.state import AgentGraphState, GraphStep, InvestigationStatus
from app.auth.authorization import coverage_for, load_active_grants
from app.auth.permissions import Permission
from app.integrations.deepseek_client import DeepSeekError
from app.models import ApprovalDecision, ApprovalStatus
from app.services import approval_service
from app.tools.btp import (
    APPLICATIONS_TOOL,
    ARCHITECTURE_DESIGN_TOOL,
    ARCHITECTURE_REQUIREMENTS_TOOL,
    CATALOG_TOOL,
    DIAGNOSE_APPLICATION_TOOL,
    REQUIREMENTS_ANALYZE_TOOL,
    RESOURCE_SIZING_TOOL,
    SERVICE_RECOMMEND_TOOL,
    SIZING_REQUIREMENTS_TOOL,
    DiagnosisOutput,
)
from app.tools.infrastructure import (
    INFRASTRUCTURE_PLAN_ANALYZE_TOOL,
    INFRASTRUCTURE_PLAN_GENERATE_TOOL,
)
from app.tools.knowledge import KNOWLEDGE_SEARCH_TOOL

logger = logging.getLogger(__name__)

_PLAN_BY_ROUTE: dict[Route, list[str]] = {
    Route.BTP_ACCOUNT: [CATALOG_TOOL],
    Route.BTP_APPLICATIONS: [CATALOG_TOOL, APPLICATIONS_TOOL],
    Route.BTP_DIAGNOSIS: [DIAGNOSE_APPLICATION_TOOL],
    Route.BTP_INVENTORY: [],
    Route.BTP_HEALTH: [],
    Route.BTP_RECOMMENDATIONS: [REQUIREMENTS_ANALYZE_TOOL, SERVICE_RECOMMEND_TOOL],
    Route.BTP_ARCHITECTURE: [ARCHITECTURE_REQUIREMENTS_TOOL, ARCHITECTURE_DESIGN_TOOL],
    Route.BTP_SIZING: [SIZING_REQUIREMENTS_TOOL, RESOURCE_SIZING_TOOL],
    Route.BTP_INFRASTRUCTURE_PLAN: [INFRASTRUCTURE_PLAN_ANALYZE_TOOL, INFRASTRUCTURE_PLAN_GENERATE_TOOL],
    Route.KNOWLEDGE_SEARCH: [KNOWLEDGE_SEARCH_TOOL.name],
}

_INVESTIGATION_STEPS: dict[Route, list[InvestigationStep]] = {
    Route.BTP_DIAGNOSIS: [
        InvestigationStep(step_id="collect_runtime", description="Collect authorized application, process, event and binding metadata.", evidence_source="cloud_foundry_v3"),
        InvestigationStep(step_id="evaluate_rules", description="Apply deterministic runtime and relationship rules to collected evidence.", evidence_source="backend_diagnosis_rules"),
        InvestigationStep(step_id="report_gaps", description="Report unsupported or incomplete evidence explicitly.", evidence_source="provider_failures_and_limits"),
    ],
    Route.BTP_APPLICATIONS: [
        InvestigationStep(step_id="read_catalog", description="Resolve the authorized BTP environment.", evidence_source="stored_btp_metadata"),
        InvestigationStep(step_id="read_applications", description="Read applications through the authorized Cloud Foundry connector.", evidence_source="cloud_foundry_v3"),
    ],
}


@dataclass
class AgentGraphContext:
    """Runtime-only dependencies; this object is never written to a checkpoint."""

    agent: SingleAgent
    authorization: AgentContext
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    max_iterations: int = 12
    max_tool_calls: int = 2
    max_checkpoint_bytes: int = 256 * 1024
    error: BaseException | None = None


def _advance(
    state: AgentGraphState,
    step: GraphStep,
    **updates: Any,
) -> dict[str, Any]:
    completed = list(state.completed_steps)
    if step not in completed:
        completed.append(step)
    pending = [item for item in state.pending_steps if item != step]
    result = {
        "completed_steps": completed,
        "pending_steps": pending,
        "current_step": step,
        "iteration": state.iteration + 1,
        **updates,
    }
    return result


def _failure(code: str, message: str, *, retryable: bool = False) -> AgentError:
    return AgentError(code=code, message=message, retryable=retryable)


def _guard(state: AgentGraphState, runtime: AgentGraphContext) -> dict[str, Any] | None:
    if state.status is InvestigationStatus.CANCELED or runtime.cancel_event.is_set():
        return _advance(
            state,
            GraphStep.CANCELED,
            status=InvestigationStatus.CANCELED,
            pending_steps=[],
        )
    if state.iteration >= runtime.max_iterations:
        return _advance(
            state,
            state.current_step,
            status=InvestigationStatus.FAILED,
            failure=_failure(
                "max_graph_iterations",
                "The investigation reached its execution limit before completion.",
            ),
            pending_steps=[],
        )
    checkpoint_size = len(
        json.dumps(state.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)
    )
    if checkpoint_size > runtime.max_checkpoint_bytes:
        return _advance(
            state,
            state.current_step,
            status=InvestigationStatus.FAILED,
            failure=_failure("checkpoint_too_large", "The investigation state exceeded its size limit."),
            response=None,
            tool_results=[],
            evidence=[],
            pending_steps=[],
        )
    return None


async def validate_request(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    context = runtime.context
    stopped = _guard(state, context)
    if stopped is not None:
        return stopped
    try:
        AgentRequest(message=state.message, conversation_id=state.conversation_id)
    except ValidationError:
        return _advance(
            state,
            GraphStep.VALIDATE_REQUEST,
            status=InvestigationStatus.FAILED,
            failure=_failure("invalid_agent_request", "The investigation request is invalid."),
            pending_steps=[],
        )
    if context.authorization.principal.user_id != state.user_id:
        return _advance(
            state,
            GraphStep.VALIDATE_REQUEST,
            status=InvestigationStatus.FAILED,
            failure=_failure("thread_owner_mismatch", "The investigation owner could not be verified."),
            pending_steps=[],
        )
    return _advance(state, GraphStep.VALIDATE_REQUEST, status=InvestigationStatus.RUNNING)


async def resolve_authorized_context(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    context = runtime.context
    stopped = _guard(state, context)
    if stopped is not None:
        return stopped
    principal = context.authorization.principal
    # Refresh grants before every resumed graph and clear coverage memoization.  The
    # dependency normally did this at request entry; a checkpoint may outlive that request.
    if context.authorization.session is not None:
        principal.grants = await load_active_grants(
            context.authorization.session, principal.user_id
        )
        principal._coverage.clear()
        if state.btp_account_id is not None or state.btp_environment_id is not None:
            coverage = await coverage_for(
                context.authorization.session, principal, Permission.BTP_READ
            )
            if state.btp_account_id is not None and not coverage.covers_account(state.btp_account_id):
                return _advance(
                    state,
                    GraphStep.RESOLVE_AUTHORIZED_CONTEXT,
                    status=InvestigationStatus.FAILED,
                    failure=_failure(
                        "btp_scope_revoked",
                        "Access to this investigation's BTP scope is no longer assigned.",
                    ),
                    pending_steps=[],
                )
            if (
                state.btp_environment_id is not None
                and not coverage.covers_environment(state.btp_environment_id)
            ):
                return _advance(
                    state,
                    GraphStep.RESOLVE_AUTHORIZED_CONTEXT,
                    status=InvestigationStatus.FAILED,
                    failure=_failure(
                        "btp_scope_revoked",
                        "Access to this investigation's BTP scope is no longer assigned.",
                    ),
                    pending_steps=[],
                )
    if not principal.has_permission(Permission.CHAT_USE):
        return _advance(
            state,
            GraphStep.RESOLVE_AUTHORIZED_CONTEXT,
            status=InvestigationStatus.FAILED,
            failure=_failure("chat_access_denied", "Chat access is not assigned to this user."),
            pending_steps=[],
        )
    return _advance(state, GraphStep.RESOLVE_AUTHORIZED_CONTEXT)


async def classify_intent(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    context = runtime.context
    stopped = _guard(state, context)
    if stopped is not None:
        return stopped
    if state.failure is not None:
        return _advance(state, GraphStep.CLASSIFY_INTENT)
    try:
        decision = await context.agent.classify(state.message, context=context.authorization)
    except DeepSeekError as exc:
        context.error = exc
        return _advance(
            state,
            GraphStep.CLASSIFY_INTENT,
            status=InvestigationStatus.FAILED,
            failure=_failure(exc.code, exc.message, retryable=exc.retryable),
            pending_steps=[],
        )
    except Exception as exc:
        context.error = exc
        logger.exception("Agent classification failed", extra={"event": "agent_classification_failed"})
        return _advance(
            state,
            GraphStep.CLASSIFY_INTENT,
            status=InvestigationStatus.FAILED,
            failure=_failure("agent_classification_failed", "The request could not be classified."),
            pending_steps=[],
        )
    return _advance(
        state,
        GraphStep.CLASSIFY_INTENT,
        route=decision.route.value,
        routing_method=decision.method.value,
        llm_calls=state.llm_calls + (1 if decision.method.value == "model" else 0),
    )


async def build_investigation_plan(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    stopped = _guard(state, runtime.context)
    if stopped is not None:
        return stopped
    if state.failure is not None or state.route is None:
        return _advance(state, GraphStep.BUILD_INVESTIGATION_PLAN, plan=[], pending_steps=[])
    route = Route(state.route)
    plan = _PLAN_BY_ROUTE.get(route, [])
    target = state.btp_environment_id or state.btp_account_id
    structured = InvestigationPlan(
        investigation_type=route.value,
        target_ref=str(target) if target else None,
        steps=_INVESTIGATION_STEPS.get(route, []),
    )
    pending = [GraphStep.REQUEST_APPROVAL, GraphStep.BUILD_RESPONSE, GraphStep.PERSIST_COMPLETION] if route is Route.OPERATION_REQUEST else [
        GraphStep.SELECT_AUTHORIZED_TOOLS,
        GraphStep.EXECUTE_READ_ONLY_TOOLS,
        GraphStep.EVALUATE_RESULTS,
        GraphStep.BUILD_RESPONSE,
        GraphStep.PERSIST_COMPLETION,
    ]
    return _advance(
        state,
        GraphStep.BUILD_INVESTIGATION_PLAN,
        plan=plan,
        investigation_plan=structured,
        pending_steps=pending,
    )


def _operation_proposal(state: AgentGraphState) -> tuple[str, dict]:
    # The message has already passed the input sanitizer and redactor in the
    # orchestrator. It is still stored as a bounded description, never as a shell
    # command or executable argument list.
    summary = state.message[:1200]
    return (
        ("Requested infrastructure changes require explicit human approval. "
        "This phase is proposal-only: no SAP BTP or shell executor is available, "
        "so approving it will not change infrastructure."),
        {"request_summary": summary, "executor": "unavailable", "read_only_phase": True},
    )


def _approval_response(state: AgentGraphState, body: str) -> AgentResponse:
    return AgentResponse(
        content=body,
        route=Route.OPERATION_REQUEST,
        routing_method=RoutingMethod(state.routing_method or RoutingMethod.RULE.value),
        outcome=AgentOutcome.OPERATION_NOT_PERFORMED,
        sources=[],
        tool_results=[],
        llm_calls=state.llm_calls,
        model=None,
    )


async def request_approval(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    stopped = _guard(state, runtime.context)
    if stopped is not None:
        return stopped
    if state.failure is not None:
        return _advance(state, GraphStep.REQUEST_APPROVAL)
    thread_id = runtime.context.authorization.principal.user_id
    thread = f"agent:{thread_id}:{state.conversation_id}"
    body, canonical_args = _operation_proposal(state)
    approval = await approval_service.create_or_get_pending(
        runtime.context.authorization.session,
        user_id=runtime.context.authorization.principal.user_id,
        thread_id=thread,
        conversation_id=state.conversation_id,
        operation_type="btp_infrastructure_change_proposal",
        operation_summary=body,
        canonical_args=canonical_args,
    )
    if approval.status != ApprovalStatus.PENDING.value:
        return _advance(
            state,
            GraphStep.REQUEST_APPROVAL,
            approval_reference=str(approval.id),
            response=_safe_response(_approval_response(state, approval.result_summary or body)),
        )
    decision = interrupt(
        {
            "type": "human_approval",
            "approval_id": str(approval.id),
            "operation_type": approval.operation_type,
            "operation_summary": approval.operation_summary,
            "risk_level": approval.risk_level,
            "execution_available": approval.execution_available,
            "expires_at": approval.expires_at.isoformat(),
            "decisions": [item.value for item in ApprovalDecision],
        }
    )
    if not isinstance(decision, dict) or decision.get("approval_id") != str(approval.id):
        return _advance(
            state,
            GraphStep.REQUEST_APPROVAL,
            status=InvestigationStatus.FAILED,
            failure=_failure("invalid_approval_resume", "The approval response did not match the pending proposal."),
            pending_steps=[],
        )
    try:
        choice = ApprovalDecision(decision.get("decision"))
    except ValueError:
        return _advance(
            state,
            GraphStep.REQUEST_APPROVAL,
            status=InvestigationStatus.FAILED,
            failure=_failure("invalid_approval_decision", "The approval decision is not supported."),
            pending_steps=[],
        )
    if choice in (ApprovalDecision.EXPLAIN_COMMAND, ApprovalDecision.OTHER_APPROACH):
        return _advance(state, GraphStep.REQUEST_APPROVAL, response=_safe_response(_approval_response(state, body)))
    decided = await approval_service.apply_decision(
        runtime.context.authorization.session,
        approval,
        actor_user_id=runtime.context.authorization.principal.user_id,
        decision=choice,
    )
    return _advance(
        state,
        GraphStep.REQUEST_APPROVAL,
        approval_reference=str(decided.id),
        status=(
            InvestigationStatus.CANCELED
            if choice is ApprovalDecision.CANCEL
            else state.status
        ),
        response=_safe_response(_approval_response(state, decided.result_summary or body)),
    )


async def select_authorized_tools(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    stopped = _guard(state, runtime.context)
    if stopped is not None:
        return stopped
    if state.failure is not None:
        return _advance(state, GraphStep.SELECT_AUTHORIZED_TOOLS)
    # The plan is an application-owned allowlist.  The model never supplies tool names.
    if len(state.plan) > runtime.context.max_tool_calls:
        return _advance(
            state,
            GraphStep.SELECT_AUTHORIZED_TOOLS,
            status=InvestigationStatus.FAILED,
            failure=_failure("tool_plan_too_large", "The investigation plan is too large."),
            pending_steps=[],
        )
    return _advance(state, GraphStep.SELECT_AUTHORIZED_TOOLS)


def _safe_response(response: AgentResponse) -> dict[str, Any]:
    payload = response.model_dump(mode="json")
    for tool in payload.get("tool_results", []):
        if isinstance(tool, dict):
            # Checkpoints retain status/evidence metadata, never arbitrary tool payloads.
            tool.pop("data", None)
    return payload


async def execute_read_only_tools(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    stopped = _guard(state, runtime.context)
    if stopped is not None:
        return stopped
    if state.failure is not None:
        return _advance(state, GraphStep.EXECUTE_READ_ONLY_TOOLS)
    try:
        decision = RoutingDecision.model_validate(
            {"route": state.route, "method": state.routing_method}
        )
        request = AgentRequest(message=state.message, conversation_id=state.conversation_id)
        response = await runtime.context.agent.handle_decision(
            request, runtime.context.authorization, decision
        )
    except DeepSeekError as exc:
        runtime.context.error = exc
        return _advance(
            state,
            GraphStep.EXECUTE_READ_ONLY_TOOLS,
            status=InvestigationStatus.FAILED,
            failure=_failure(exc.code, exc.message, retryable=exc.retryable),
            pending_steps=[],
        )
    except Exception as exc:
        runtime.context.error = exc
        logger.exception("Agent execution failed", extra={"event": "agent_execution_failed"})
        return _advance(
            state,
            GraphStep.EXECUTE_READ_ONLY_TOOLS,
            status=InvestigationStatus.FAILED,
            failure=_failure("agent_execution_failed", "The investigation could not be completed."),
            pending_steps=[],
        )
    safe = _safe_response(response)
    tool_results = safe.get("tool_results", [])
    evidence = build_evidence(response.sources, response.tool_results)
    findings: list[InvestigationFinding] = []
    missing_evidence: list[str] = []
    recommendations: list[str] = []
    for result in response.tool_results:
        if result.tool_name != DIAGNOSE_APPLICATION_TOOL or result.data is None:
            continue
        try:
            diagnosis = DiagnosisOutput.model_validate(result.data).diagnosis
        except ValidationError:
            continue
        missing_evidence.extend(diagnosis.missing_information)
        for item in diagnosis.findings:
            findings.append(
                InvestigationFinding(
                    finding_id=item.finding_id,
                    rule_id=item.rule_id,
                    resource_ref=item.resource_id,
                    evidence_refs=item.evidence_references,
                    observed_at=item.timestamp,
                    severity=FindingSeverity(item.severity),
                    confidence=FindingConfidence(item.confidence),
                    explanation=item.observation,
                    possible_causes=item.possible_causes,
                    next_steps=item.recommended_next_steps,
                    missing_evidence=diagnosis.missing_information[:8],
                )
            )
            recommendations.extend(item.recommended_next_steps)
    return _advance(
        state,
        GraphStep.EXECUTE_READ_ONLY_TOOLS,
        response=safe,
        tool_results=tool_results,
        evidence=evidence,
        findings=findings,
        missing_evidence=list(dict.fromkeys(missing_evidence))[:30],
        recommendations=list(dict.fromkeys(recommendations))[:30],
        llm_calls=max(state.llm_calls, response.llm_calls),
        tool_calls=len(response.tool_results),
    )


async def evaluate_results(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    stopped = _guard(state, runtime.context)
    if stopped is not None:
        return stopped
    if state.failure is None and state.response is not None:
        try:
            AgentResponse.model_validate(state.response)
        except ValidationError:
            return _advance(
                state,
                GraphStep.EVALUATE_RESULTS,
                status=InvestigationStatus.FAILED,
                failure=_failure("invalid_agent_result", "The agent returned an invalid result."),
                response=None,
                pending_steps=[],
            )
    return _advance(state, GraphStep.EVALUATE_RESULTS)


async def build_response(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    stopped = _guard(state, runtime.context)
    if stopped is not None:
        return stopped
    return _advance(state, GraphStep.BUILD_RESPONSE)


async def persist_completion(
    state: AgentGraphState, runtime: Runtime[AgentGraphContext]
) -> dict[str, Any]:
    status = (
        InvestigationStatus.CANCELED
        if runtime.context.cancel_event.is_set() or state.status is InvestigationStatus.CANCELED
        else InvestigationStatus.FAILED
        if state.failure is not None
        else InvestigationStatus.COMPLETED
    )
    return _advance(
        state,
        GraphStep.PERSIST_COMPLETION,
        status=status,
        pending_steps=[],
        final_response_reference=(
            f"conversation:{state.conversation_id}" if state.response is not None else None
        ),
    )


def _continue_or_persist(state: AgentGraphState) -> str:
    return "persist" if state.failure is not None or state.status is InvestigationStatus.CANCELED else "continue"


def build_agent_graph(
    agent: SingleAgent, checkpointer: BaseCheckpointSaver | None
):
    """Build the one graph used by ``AgentOrchestrator``."""

    builder = StateGraph(AgentGraphState, context_schema=AgentGraphContext)
    builder.add_node("validate_request", validate_request)
    builder.add_node("resolve_authorized_context", resolve_authorized_context)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("build_investigation_plan", build_investigation_plan)
    builder.add_node("request_approval", request_approval)
    builder.add_node("select_authorized_tools", select_authorized_tools)
    builder.add_node("execute_read_only_tools", execute_read_only_tools)
    builder.add_node("evaluate_results", evaluate_results)
    builder.add_node("build_response", build_response)
    builder.add_node("persist_completion", persist_completion)
    builder.add_edge(START, "validate_request")
    builder.add_conditional_edges(
        "validate_request", _continue_or_persist, {"continue": "resolve_authorized_context", "persist": "persist_completion"}
    )
    builder.add_conditional_edges(
        "resolve_authorized_context", _continue_or_persist, {"continue": "classify_intent", "persist": "persist_completion"}
    )
    builder.add_conditional_edges(
        "classify_intent", _continue_or_persist, {"continue": "build_investigation_plan", "persist": "persist_completion"}
    )
    builder.add_conditional_edges(
        "build_investigation_plan",
        lambda state: "approval" if state.route == Route.OPERATION_REQUEST.value else "tools",
        {"approval": "request_approval", "tools": "select_authorized_tools"},
    )
    builder.add_edge("request_approval", "build_response")
    builder.add_conditional_edges(
        "select_authorized_tools", _continue_or_persist, {"continue": "execute_read_only_tools", "persist": "persist_completion"}
    )
    builder.add_edge("execute_read_only_tools", "evaluate_results")
    builder.add_conditional_edges(
        "evaluate_results", _continue_or_persist, {"continue": "build_response", "persist": "persist_completion"}
    )
    builder.add_edge("build_response", "persist_completion")
    builder.add_edge("persist_completion", END)
    return builder.compile(checkpointer=checkpointer)


def config_for_thread(thread_id: str, recursion_limit: int) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}
