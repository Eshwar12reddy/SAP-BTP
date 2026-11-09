"""The agent's prompt construction boundary.

All untrusted text still goes through the existing safety guard.  This small adapter gives
the orchestrator a named prompt-building dependency without duplicating the security rules.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.safety.input_guard import PromptBuild, UntrustedContent, build_messages
from app.safety.redaction import Redactor


class AgentPromptBuilder:
    """Build one bounded system-plus-data prompt using the canonical safety guard."""

    def build(
        self,
        instructions: str,
        contents: Sequence[UntrustedContent],
        redactor: Redactor,
    ) -> PromptBuild:
        return build_messages(instructions, contents, redactor)


def build_agent_prompt(
    instructions: str,
    contents: Sequence[UntrustedContent],
    redactor: Redactor,
) -> PromptBuild:
    """Functional compatibility helper for callers that prefer dependency-free use."""

    return AgentPromptBuilder().build(instructions, contents, redactor)
