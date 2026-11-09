"""Public LangGraph orchestration entry point for the single AI agent."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from app.agents.contracts import (
    AgentContext,
    AgentOutcome,
    AgentRequest,
    AgentResponse,
    Route,
    RoutingMethod,
)
from app.agents.single_agent import SingleAgent
from app.agents.state import AgentGraphState, InvestigationStatus
from app.agents.workflow import AgentGraphContext, build_agent_graph, config_for_thread
from app.auth.permissions import Permission
from app.core.config import Settings
from app.core.exceptions import ConflictError, PermissionDeniedError
from app.models import AgentApproval, ApprovalDecision, ApprovalStatus
from app.safety.input_guard import sanitize
from app.safety.redaction import Redactor
from app.services import approval_service


class AgentOrchestrator(SingleAgent):
    """The one application agent, executed through a resumable LangGraph workflow."""

    def __init__(
        self,
        deepseek,
        registry,
        redactor: Redactor | None = None,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(deepseek, registry, redactor)
        self._settings = settings
        self._checkpointer = checkpointer or InMemorySaver()
        self._graph = build_agent_graph(self, self._checkpointer)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._active: dict[str, AgentGraphContext] = {}

    @staticmethod
    def thread_id(user_id, conversation_id) -> str:
        """Derive a non-guessable-in-context thread key from authenticated ownership."""

        return f"agent:{user_id}:{conversation_id}"

    async def handle(self, request: AgentRequest, context: AgentContext) -> AgentResponse:
        conversation_id = request.conversation_id or uuid.uuid5(
            uuid.NAMESPACE_URL, f"agent:{context.principal.user_id}:{request.message}"
        )
        thread_id = self.thread_id(context.principal.user_id, conversation_id)
        clean, _ = sanitize(request.message)
        safe_message = (context.redactor or self._redactor).redact(clean).text
        settings = context.settings or self._settings
        max_iterations = settings.LANGGRAPH_MAX_ITERATIONS if settings else 12
        max_tool_calls = settings.LANGGRAPH_MAX_TOOL_CALLS if settings else 2
        max_checkpoint_bytes = settings.LANGGRAPH_MAX_CHECKPOINT_BYTES if settings else 256 * 1024
        async with self._locks[thread_id]:
            config = config_for_thread(thread_id, max_iterations + 5)
            snapshot = await self._graph.aget_state(config)
            resume = False
            if snapshot.values:
                try:
                    prior = AgentGraphState.model_validate(snapshot.values)
                except ValidationError as exc:
                    raise PermissionDeniedError("The stored investigation state is invalid.") from exc
                if prior.user_id != context.principal.user_id:
                    raise PermissionDeniedError("You are not authorized to access this investigation.")
                if (
                    prior.status is InvestigationStatus.COMPLETED
                    and prior.response is not None
                    and prior.message == safe_message
                ):
                    return AgentResponse.model_validate(prior.response)
                # Continue an interrupted durable thread from its last checkpoint. A
                # terminal failure is retried with a fresh bounded input instead.
                resume = (
                    prior.status is InvestigationStatus.RUNNING
                    and prior.message == safe_message
                )

            initial = AgentGraphState(
                message=safe_message,
                user_id=context.principal.user_id,
                conversation_id=conversation_id,
                btp_account_id=context.btp_account_id,
                btp_environment_id=context.btp_environment_id,
                context_message_ids=(
                    list(context.assembled_context.message_ids)
                    if context.assembled_context
                    else []
                ),
                memory_ids=(
                    list(context.assembled_context.memory_ids)
                    if context.assembled_context
                    else []
                ),
            )
            runtime = AgentGraphContext(
                agent=self,
                authorization=context,
                max_iterations=max_iterations,
                max_tool_calls=max_tool_calls,
                max_checkpoint_bytes=max_checkpoint_bytes,
            )
            self._active[thread_id] = runtime
            try:
                result = await self._graph.ainvoke(
                    None if resume else initial,
                    config,
                    context=runtime,
                )
            finally:
                self._active.pop(thread_id, None)
            if "__interrupt__" in result:
                snapshot = await self._graph.aget_state(config)
                state = AgentGraphState.model_validate(snapshot.values)
                approval = await approval_service.get_for_thread(
                    context.session,
                    user_id=context.principal.user_id,
                    thread_id=thread_id,
                    conversation_id=conversation_id,
                )
                if approval is None:
                    raise RuntimeError("The approval checkpoint was not persisted.")
                return AgentResponse(
                    content=(
                        "This request is paused for human approval. "
                        "No infrastructure change has been made. Review the pending proposal "
                        "and choose an approval option below."
                    ),
                    route=Route.OPERATION_REQUEST,
                    routing_method=RoutingMethod(state.routing_method or RoutingMethod.RULE.value),
                    outcome=AgentOutcome.OPERATION_NOT_PERFORMED,
                    sources=[],
                    llm_calls=state.llm_calls,
                )
            final = AgentGraphState.model_validate(result)
            if runtime.error is not None:
                raise runtime.error
            if final.failure is not None:
                if final.failure.code in {
                    "btp_scope_revoked",
                    "chat_access_denied",
                    "thread_owner_mismatch",
                }:
                    raise PermissionDeniedError(final.failure.message, code=final.failure.code)
                raise RuntimeError(final.failure.message)
            if final.response is None:
                raise RuntimeError("The agent completed without a response.")
            return AgentResponse.model_validate(final.response)

    async def resume_approval(
        self,
        conversation_id,
        context: AgentContext,
        approval_id: uuid.UUID,
        decision: ApprovalDecision,
    ) -> tuple[AgentApproval, AgentResponse]:
        """Resume the exact authorized thread through LangGraph ``Command(resume=...)``."""
        if context.session is None:
            raise PermissionDeniedError("The approval could not be re-authorized.")
        thread_id = self.thread_id(context.principal.user_id, conversation_id)
        approval = await approval_service.get_owned(
            context.session,
            approval_id,
            user_id=context.principal.user_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
        )
        if approval is None:
            raise PermissionDeniedError("The approval is not available in this authorized thread.")
        if approval.status != ApprovalStatus.PENDING.value:
            raise ConflictError(
                "This approval is no longer pending and cannot be decided again.",
                code="approval_not_pending",
            )
        if decision in (ApprovalDecision.EXPLAIN_COMMAND, ApprovalDecision.OTHER_APPROACH):
            approval = await approval_service.record_nonterminal_choice(
                context.session,
                approval,
                actor_user_id=context.principal.user_id,
                decision=decision,
            )
            message = (
                approval.operation_summary
                if decision is ApprovalDecision.EXPLAIN_COMMAND
                else "No alternative executor is registered. You may leave this proposal pending or cancel it."
            )
            return approval, AgentResponse(
                content=message,
                route=Route.OPERATION_REQUEST,
                routing_method=RoutingMethod.RULE,
                outcome=AgentOutcome.OPERATION_NOT_PERFORMED,
                sources=[],
            )
        settings = context.settings or self._settings
        max_iterations = settings.LANGGRAPH_MAX_ITERATIONS if settings else 12
        runtime = AgentGraphContext(
            agent=self,
            authorization=context,
            max_iterations=max_iterations,
            max_tool_calls=settings.LANGGRAPH_MAX_TOOL_CALLS if settings else 2,
            max_checkpoint_bytes=settings.LANGGRAPH_MAX_CHECKPOINT_BYTES if settings else 256 * 1024,
        )
        async with self._locks[thread_id]:
            self._active[thread_id] = runtime
            try:
                result = await self._graph.ainvoke(
                    Command(resume={"approval_id": str(approval_id), "decision": decision.value}),
                    config_for_thread(thread_id, max_iterations + 5),
                    context=runtime,
                )
            finally:
                self._active.pop(thread_id, None)
        if "__interrupt__" in result:
            raise RuntimeError("The approval remained interrupted after a terminal decision.")
        final = AgentGraphState.model_validate(result)
        if final.failure is not None:
            raise RuntimeError(final.failure.message)
        approval = await approval_service.get_owned(
            context.session,
            approval_id,
            user_id=context.principal.user_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
        )
        if approval is None:
            raise RuntimeError("The approval result could not be reloaded.")
        if final.response is None:
            raise RuntimeError("The approval completed without a response.")
        return approval, AgentResponse.model_validate(final.response)

    async def get_status(self, conversation_id, context: AgentContext) -> AgentGraphState | None:
        if not context.principal.has_permission(Permission.CHAT_USE):
            raise PermissionDeniedError("Chat access is not assigned to this user.")
        thread_id = self.thread_id(context.principal.user_id, conversation_id)
        snapshot = await self._graph.aget_state(config_for_thread(thread_id, 20))
        if not snapshot.values:
            return None
        state = AgentGraphState.model_validate(snapshot.values)
        if state.user_id != context.principal.user_id or state.conversation_id != conversation_id:
            raise PermissionDeniedError("You are not authorized to access this investigation.")
        return state

    async def cancel(self, conversation_id, context: AgentContext) -> AgentGraphState | None:
        state = await self.get_status(conversation_id, context)
        if state is None or state.status in (InvestigationStatus.COMPLETED, InvestigationStatus.CANCELED):
            return state
        thread_id = self.thread_id(context.principal.user_id, conversation_id)
        approval = await approval_service.get_for_thread(
            context.session,
            user_id=context.principal.user_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
        )
        if approval is not None and approval.status == ApprovalStatus.PENDING.value:
            await approval_service.apply_decision(
                context.session,
                approval,
                actor_user_id=context.principal.user_id,
                decision=ApprovalDecision.CANCEL,
            )
        active = self._active.get(thread_id)
        if active is not None:
            active.cancel_event.set()
        await self._graph.aupdate_state(
            config_for_thread(thread_id, 20),
            {
                "status": InvestigationStatus.CANCELED,
                "current_step": "canceled",
                "pending_steps": [],
            },
        )
        return await self.get_status(conversation_id, context)

