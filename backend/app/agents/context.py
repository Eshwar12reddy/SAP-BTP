"""Bounded, authorization-aware context assembly for the single agent."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Principal
from app.core.config import Settings
from app.models import Message, MessageRole
from app.safety.input_guard import TrustLevel, UntrustedContent, sanitize
from app.safety.redaction import Redactor
from app.services.memory_service import retrieve_memory_context


@dataclass(frozen=True)
class ContextTurn:
    message_id: uuid.UUID
    role: str
    content: str


@dataclass(frozen=True)
class ContextMemory:
    memory_id: uuid.UUID
    category: str
    content: str
    verification_status: str
    source_reference: str | None


@dataclass(frozen=True)
class ContextBundle:
    """Runtime-only assembled data; identifiers are safe to checkpoint separately."""

    turns: tuple[ContextTurn, ...] = ()
    memories: tuple[ContextMemory, ...] = ()
    prior_findings: tuple[str, ...] = ()
    btp_context: str | None = None
    max_chars: int = 24_000
    message_ids: tuple[uuid.UUID, ...] = ()

    @property
    def memory_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(memory.memory_id for memory in self.memories)

    def prompt_contents(self) -> list[UntrustedContent]:
        contents: list[UntrustedContent] = []
        if self.turns:
            history = "\n".join(f"{turn.role}: {turn.content}" for turn in self.turns)
            contents.append(
                UntrustedContent(TrustLevel.RETRIEVED_CONTENT, "conversation_history", history)
            )
        if self.prior_findings:
            contents.append(
                UntrustedContent(
                    TrustLevel.TOOL_RESULT,
                    "prior_findings",
                    "\n".join(self.prior_findings),
                )
            )
        if self.memories:
            memory_text = "\n".join(
                f"[{memory.category}; {memory.verification_status}] {memory.content}"
                for memory in self.memories
            )
            contents.append(
                UntrustedContent(TrustLevel.RETRIEVED_CONTENT, "long_term_memory", memory_text)
            )
        if self.btp_context:
            contents.append(
                UntrustedContent(TrustLevel.RETRIEVED_CONTENT, "btp_context", self.btp_context)
            )
        return contents


def _safe_text(text: str, redactor: Redactor, limit: int = 4_000) -> str:
    cleaned, _ = sanitize(text)
    return redactor.redact(cleaned).text[:limit]


def _prior_findings(rows: list[Message], redactor: Redactor) -> tuple[str, ...]:
    findings: list[str] = []
    for row in rows:
        if row.role != MessageRole.ASSISTANT.value or not row.agent_metadata:
            continue
        metadata = row.agent_metadata
        tool_results = metadata.get("tool_results", []) if isinstance(metadata, dict) else []
        if not isinstance(tool_results, list):
            continue
        statuses = []
        for item in tool_results[:8]:
            if isinstance(item, dict):
                name = str(item.get("tool_name", "tool"))[:64]
                status = str(item.get("status", "unknown"))[:32]
                statuses.append(f"{name}={status}")
        if statuses:
            route = str(metadata.get("route", "unknown"))[:64]
            findings.append(f"Earlier authorized investigation route={route}; tool outcomes: {', '.join(statuses)}")
    return tuple(findings[-8:])


async def assemble_context(
    session: AsyncSession,
    principal: Principal,
    *,
    conversation_id: uuid.UUID,
    current_message_id: uuid.UUID | None,
    query: str,
    btp_account_id: uuid.UUID | None,
    btp_environment_id: uuid.UUID | None,
    settings: Settings,
    redactor: Redactor,
) -> ContextBundle:
    rows = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq.desc())
            .limit(settings.AGENT_CONTEXT_MAX_MESSAGES + 1)
        )
    )
    rows.reverse()
    turns: list[ContextTurn] = []
    for row in rows:
        if current_message_id is not None and row.id == current_message_id:
            continue
        turns.append(
            ContextTurn(
                message_id=row.id,
                role=row.role,
                content=_safe_text(row.content, redactor),
            )
        )

    memories = await retrieve_memory_context(
        session,
        principal,
        query=query,
        btp_account_id=btp_account_id,
        btp_environment_id=btp_environment_id,
        limit=settings.AGENT_MEMORY_MAX_RESULTS,
        max_chars=settings.AGENT_MEMORY_MAX_CHARS,
    )
    context_memories = tuple(
        ContextMemory(
            memory_id=memory.id,
            category=memory.category,
            content=_safe_text(memory.content, redactor, 3_000),
            verification_status=memory.verification_status,
            source_reference=memory.source_investigation_reference,
        )
        for memory in memories
    )
    findings = _prior_findings(rows, redactor)
    btp_context = None
    if btp_account_id is not None or btp_environment_id is not None:
        # The IDs remain in trusted runtime/graph state for authorization, but are not
        # sent to DeepSeek. Scoped tools receive those IDs server-side when needed.
        btp_context = "A server-authorized BTP context is selected for this conversation."
    # Deterministic compaction: retain the newest turns while preserving order and IDs.
    # Four characters/token is a conservative bound for mixed technical text; the
    # character setting remains an additional hard ceiling before PromptBuilder runs.
    context_limit = min(settings.AGENT_CONTEXT_MAX_CHARS, settings.AGENT_CONTEXT_MAX_TOKENS * 4)
    used = 0
    compacted: list[ContextTurn] = []
    for turn in reversed(turns):
        cost = len(turn.content) + len(turn.role) + 4
        if compacted and used + cost > context_limit:
            break
        compacted.append(turn)
        used += cost
    compacted.reverse()
    return ContextBundle(
        turns=tuple(compacted),
        memories=context_memories,
        prior_findings=findings,
        btp_context=btp_context,
        max_chars=context_limit,
        message_ids=tuple(turn.message_id for turn in compacted),
    )
