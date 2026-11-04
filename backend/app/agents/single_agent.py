"""The single agent: route a request, run registered tools, answer with honest evidence.

Tool outcomes (catalog listings, unavailable live checks, refusals) are rendered as fixed
text: there is nothing for a model to add, and nothing it could embellish. DeepSeek writes
only explanatory answers, and every answer ends with a source line produced by this code,
not by the model. Per request: at most ``MAX_LLM_CALLS`` model calls and
``MAX_TOOL_CALLS`` tool executions. Each request is answered on its own; earlier messages
are not sent to the model, so text from revoked scopes cannot reach it.
"""

from __future__ import annotations

import logging
import re

from app.agents.contracts import (
    AgentContext,
    AgentOutcome,
    AgentRequest,
    AgentResponse,
    Route,
    RoutingDecision,
    RoutingMethod,
)
from app.agents.router import route_request
from app.integrations.deepseek_client import DeepSeekClient
from app.safety.input_guard import (
    TrustLevel,
    UntrustedContent,
    build_messages,
    normalize_for_matching,
    sanitize,
)
from app.safety.output_validation import check_answer
from app.safety.redaction import Redactor
from app.tools.btp import (
    APPLICATIONS_TOOL,
    CATALOG_TOOL,
    ApplicationsOutput,
    CatalogEnvironment,
    CatalogOutput,
)
from app.tools.contracts import SourceType, ToolResult, ToolStatus
from app.tools.knowledge import KNOWLEDGE_SEARCH_TOOL, KnowledgeSearchOutput
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

MAX_LLM_CALLS = 2
MAX_TOOL_CALLS = 2

_SYSTEM_RULES = (
    "You are the AI Operations & Architecture Assistant for SAP BTP.\n"
    "Rules that always apply:\n"
    "- In this answer you have no access to the user's SAP BTP accounts, environments, "
    "applications or monitoring data.\n"
    "- Never claim to have checked, inspected, verified, monitored or queried any system.\n"
    "- Never invent account or application names, statuses, metrics, prices, or document "
    "titles, links or citations.\n"
    "- If information is missing, say what is needed.\n"
    "- The user's question is in the untrusted_content block below."
)
_ROUTE_INSTRUCTIONS = {
    Route.GENERAL_KNOWLEDGE: "Answer from general knowledge, concisely.",
    Route.DOCUMENTATION: (
        "No documentation source is connected. Explain from general knowledge and do not "
        "quote or cite documents."
    ),
    Route.ARCHITECTURE: (
        "Recommend an architecture using the sections 'Assumptions', 'Recommendation' and "
        "'Trade-offs'. This is general advice, not an assessment of the user's infrastructure."
    ),
    Route.SIZING: (
        "No validated sizing engine is available. List the inputs a real sizing needs that "
        "the request does not give (load profile, payload size, memory per request, latency "
        "target, runtime). Only if enough is given, add a section titled 'Preliminary "
        "estimate' with explicit assumptions and ranges. Never present numbers as measured "
        "or validated."
    ),
}

_MODEL_SOURCE = "Source: general AI model knowledge (DeepSeek). No live SAP BTP system was checked."
_FOOTERS = {
    Route.GENERAL_KNOWLEDGE: _MODEL_SOURCE,
    Route.ARCHITECTURE: _MODEL_SOURCE,
    Route.DOCUMENTATION: (
        "Source: general AI model knowledge (DeepSeek). No documentation was retrieved and "
        "no live SAP BTP system was checked."
    ),
    Route.SIZING: (
        "Source: general AI model knowledge (DeepSeek). This is not a validated sizing "
        "calculation, and no live SAP BTP system was checked."
    ),
}
_STORED_SOURCE = (
    "Source: the BTP account mapping stored in this application, limited to your access. "
    "This is not a live SAP BTP check."
)
_NO_LIVE_CHECK = "No live SAP BTP check was performed."

_OPERATION_REFUSAL = (
    "I can't carry out infrastructure changes. Actions such as deploying, restarting, "
    "scaling, configuring or deleting resources are not available in this assistant yet; "
    "they will require an approval workflow. Nothing was changed."
)
_CLARIFICATION = (
    "I'm not sure what you need. I can explain SAP BTP concepts, list the BTP accounts and "
    "environments you can access, check applications in one of your environments, "
    "recommend architectures, and outline what a sizing needs. I can't change "
    "infrastructure."
)
_UNREADABLE = "Your message contains no readable text. Please type your question."
_ENVIRONMENT_ALIASES = {
    "development": ("dev",),
    "production": ("prod",),
    "testing": ("test", "qa"),
    "staging": ("stage",),
}
_ENVIRONMENT_WORDS = re.compile(
    r"\b(dev|development|test|testing|qa|stage|staging|prod|production|sandbox)\b",
    re.IGNORECASE,
)
_KNOWLEDGE_PREFIX = re.compile(
    r"^\s*(?:(?:please|can you|could you)\s+)?(?:search|find|look up)\s+"
    r"(?:the\s+)?(?:knowledge\s+base|(?:uploaded\s+)?(?:documents?|docs|files?))\s*"
    r"(?:for|about)?\s*[:,-]?\s*",
    re.IGNORECASE,
)


class SingleAgent:
    def __init__(
        self, deepseek: DeepSeekClient, registry: ToolRegistry, redactor: Redactor | None = None
    ) -> None:
        self._deepseek = deepseek
        self._registry = registry
        self._redactor = redactor or Redactor()

    async def handle(self, request: AgentRequest, context: AgentContext) -> AgentResponse:
        run = _Run(self._deepseek, self._registry, context, self._redactor)
        # Invisible characters can hide instructions or dodge the routing rules.
        text, _ = sanitize(request.message)
        if not text.strip():
            logger.warning(
                "Agent input rejected",
                extra={"event": "ai_input_rejected", "reason": "no_readable_text"},
            )
            decision = RoutingDecision(route=Route.UNSUPPORTED, method=RoutingMethod.RULE)
            return run.respond(decision, AgentOutcome.NEEDS_CLARIFICATION, _UNREADABLE, [])
        match_text = normalize_for_matching(text)
        decision = await run.route(text, match_text)
        if decision.route is Route.OPERATION_REQUEST:
            logger.info(
                "Operation request refused; nothing executed",
                extra={
                    "event": "ai_operation_request_refused",
                    "reason": decision.method.value,
                },
            )
            response = run.respond(
                decision, AgentOutcome.OPERATION_NOT_PERFORMED, _OPERATION_REFUSAL, []
            )
        elif decision.route is Route.UNSUPPORTED:
            response = run.respond(decision, AgentOutcome.NEEDS_CLARIFICATION, _CLARIFICATION, [])
        elif decision.route is Route.BTP_ACCOUNT:
            response = await run.catalog(decision, match_text)
        elif decision.route is Route.BTP_APPLICATIONS:
            response = await run.applications(decision, match_text)
        elif decision.route is Route.KNOWLEDGE_SEARCH:
            response = await run.knowledge_search(decision, text)
        else:
            response = await run.model_answer(decision, text)
        logger.info(
            "Agent request completed",
            extra={
                "event": "agent_request_completed",
                "route": response.route.value,
                "outcome": response.outcome.value,
                "reason": response.routing_method.value,
                "llm_calls": response.llm_calls,
                "tool_calls": len(response.tool_results),
            },
        )
        return response


class _Run:
    """State for one request: enforces the call budgets and collects tool results."""

    def __init__(
        self,
        deepseek: DeepSeekClient,
        registry: ToolRegistry,
        context: AgentContext,
        redactor: Redactor,
    ) -> None:
        self._deepseek = deepseek
        self._registry = registry
        self._context = context
        self._redactor = redactor
        self.llm_calls = 0
        self.tool_results: list[ToolResult] = []

    async def route(self, text: str, match_text: str) -> RoutingDecision:
        decision = await route_request(text, self._deepseek, self._redactor, match_text=match_text)
        if decision.method is RoutingMethod.MODEL:
            self.llm_calls += 1
        return decision

    async def run_tool(self, name: str, arguments: dict) -> ToolResult:
        if len(self.tool_results) >= MAX_TOOL_CALLS:
            raise RuntimeError("agent tool-call budget exceeded")
        result = await self._registry.execute(name, arguments, self._context)
        self.tool_results.append(result)
        return result

    def respond(
        self,
        decision: RoutingDecision,
        outcome: AgentOutcome,
        body: str,
        sources: list[SourceType],
        footer: str | None = None,
        model: str | None = None,
    ) -> AgentResponse:
        return AgentResponse(
            content=f"{body}\n\n— {footer}" if footer else body,
            route=decision.route,
            routing_method=decision.method,
            outcome=outcome,
            sources=sources,
            tool_results=self.tool_results,
            llm_calls=self.llm_calls,
            model=model,
        )

    async def catalog(self, decision: RoutingDecision, text: str) -> AgentResponse:
        result = await self.run_tool(CATALOG_TOOL, {})
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, "your BTP accounts")
        catalog = CatalogOutput.model_validate(result.data)
        environments = _mentioned(text, catalog.environments) or catalog.environments
        if not catalog.accounts and not environments:
            body = "You don't have access to any configured SAP BTP accounts or environments."
        else:
            sections = []
            if catalog.accounts:
                sections.append(
                    "Accounts you can access:\n"
                    + "\n".join(
                        f"- {_md(a.name)} ({a.account_type.replace('_', ' ')}"
                        f"{', ' + _md(a.region) if a.region else ''})"
                        for a in catalog.accounts
                    )
                )
            if environments:
                sections.append(
                    "Environments you can access:\n"
                    + "\n".join(f"- {_environment_label(e)}" for e in environments)
                )
            # Blank line between sections: the chat renders replies as Markdown.
            body = "\n\n".join(sections)
        return self.respond(
            decision, AgentOutcome.ANSWERED, body, [SourceType.STORED_METADATA], _STORED_SOURCE
        )

    async def applications(self, decision: RoutingDecision, text: str) -> AgentResponse:
        result = await self.run_tool(CATALOG_TOOL, {})
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, "your BTP environments")
        catalog = CatalogOutput.model_validate(result.data)
        candidates, problem = self._resolve_environment(text, catalog.environments)
        if problem:
            return self.respond(decision, AgentOutcome.NEEDS_CLARIFICATION, problem, [])

        target = candidates[0]
        result = await self.run_tool(APPLICATIONS_TOOL, {"environment_id": str(target.id)})
        label = _environment_label(target)
        if result.status is ToolStatus.UNAVAILABLE:
            body = (
                f"I could not list the applications in {label}: the live SAP BTP integration "
                "is not configured. I can't tell which applications exist there or whether "
                "they are running."
            )
            return self.respond(decision, AgentOutcome.TOOL_UNAVAILABLE, body, [], _NO_LIVE_CHECK)
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, f"applications in {label}")

        apps = ApplicationsOutput.model_validate(result.data)
        if apps.applications:
            body = f"Applications in {label}:\n" + "\n".join(
                f"- {_md(a.name)}{f' ({_md(a.state)})' if a.state else ''}"
                for a in apps.applications
            )
        else:
            body = f"The live check found no applications in {label}."
        footer = (
            f"Source: live SAP BTP data from the '{apps.provider}' integration, retrieved "
            f"{result.timestamp:%Y-%m-%d %H:%M:%S} UTC."
        )
        return self.respond(decision, AgentOutcome.ANSWERED, body, [SourceType.LIVE_BTP], footer)

    async def knowledge_search(self, decision: RoutingDecision, query: str) -> AgentResponse:
        """Render backend-verified excerpts directly; document text never reaches DeepSeek."""
        lookup = _KNOWLEDGE_PREFIX.sub("", query).strip() or query
        result = await self.run_tool(KNOWLEDGE_SEARCH_TOOL.name, {"query": lookup, "max_results": 6})
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, "your authorized knowledge documents")
        output = KnowledgeSearchOutput.model_validate(result.data)
        if not output.items:
            body = "I couldn't find a matching passage in your currently accessible knowledge documents."
        else:
            passages = []
            for item in output.items:
                citation = item.citation
                locator = f"version {citation.version_number}"
                if citation.page_start is not None:
                    locator += f", page {citation.page_start}"
                if citation.section_heading:
                    locator += f", {citation.section_heading}"
                passages.append(
                    f"**{_md(citation.source_title)}** ({locator})\n\n"
                    f"> {item.excerpt.replace(chr(10), chr(10) + '> ')}\n\n"
                    f"[Open source]({citation.download_url}) (citation `{citation.citation_id}`)"
                )
            body = "Relevant passages from your accessible documents:\n\n" + "\n\n".join(passages)
        return self.respond(
            decision, AgentOutcome.ANSWERED, body, [SourceType.KNOWLEDGE_DOCUMENTS],
            "Source: current, scope-filtered knowledge documents. Excerpts and citations were verified by the backend.",
        )

    async def model_answer(self, decision: RoutingDecision, text: str) -> AgentResponse:
        if self.llm_calls >= MAX_LLM_CALLS:
            raise RuntimeError("agent model-call budget exceeded")
        instructions = _ROUTE_INSTRUCTIONS[decision.route]
        # Only the user's own question is sent: no BTP data, tool output or history.
        prompt = build_messages(
            f"{_SYSTEM_RULES}\n\n{instructions}",
            [UntrustedContent(TrustLevel.AUTHENTICATED_USER_INPUT, "user_message", text)],
            self._redactor,
        )
        self.llm_calls += 1
        result = await self._deepseek.chat(prompt.messages)
        body = check_answer(result.content.strip(), self._redactor).text
        if result.truncated:
            body += "\n\n[The answer was cut off at the length limit.]"
        return self.respond(
            decision,
            AgentOutcome.ANSWERED,
            body,
            [SourceType.MODEL_KNOWLEDGE],
            _FOOTERS[decision.route],
            model=result.model,
        )

    def tool_problem(
        self, decision: RoutingDecision, result: ToolResult, subject: str
    ) -> AgentResponse:
        if result.status is ToolStatus.DENIED:
            outcome, body = AgentOutcome.DENIED, f"You don't have access to {subject}."
        elif result.status is ToolStatus.UNAVAILABLE:
            outcome, body = AgentOutcome.TOOL_UNAVAILABLE, f"I can't reach {subject} right now."
        else:
            outcome, body = AgentOutcome.FAILED, f"Looking up {subject} failed. No results are available."
        return self.respond(decision, outcome, body, [], _NO_LIVE_CHECK)

    def _resolve_environment(
        self, text: str, environments: list[CatalogEnvironment]
    ) -> tuple[list[CatalogEnvironment], str | None]:
        """Pick one authorized environment, or explain why not. Never trusts model output."""
        context = self._context
        candidates = environments
        if context.btp_environment_id:
            candidates = [e for e in candidates if e.id == context.btp_environment_id]
        elif context.btp_account_id:
            candidates = [e for e in candidates if e.account_id == context.btp_account_id]

        mentioned = _mentioned(text, candidates)
        if mentioned:
            lowered = text.lower()
            named = [e for e in mentioned if e.account_name and e.account_name.lower() in lowered]
            candidates = named or mentioned
        elif _ENVIRONMENT_WORDS.search(text):
            # An environment was named but none of the candidates matches: never substitute.
            scope = " in this conversation's BTP context" if candidates != environments else ""
            return [], f"I couldn't find a matching environment that you can access{scope}."

        if len(candidates) == 1:
            return candidates, None
        if not candidates:
            return [], "You don't have access to any environment to check."
        options = "\n".join(f"- {_environment_label(e)}" for e in candidates)
        return [], f"Which environment should I check? You can access:\n{options}"


def _mentioned(text: str, environments: list[CatalogEnvironment]) -> list[CatalogEnvironment]:
    lowered = text.lower()
    found = []
    for environment in environments:
        name = environment.name.lower()
        words = (name, *_ENVIRONMENT_ALIASES.get(name, ()))
        if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words):
            found.append(environment)
    return found


def _environment_label(environment: CatalogEnvironment) -> str:
    where = f" in {_md(environment.account_name)}" if environment.account_name else ""
    return f"{_md(environment.name)} ({environment.runtime_type}){where}"


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~])")


def _md(text: str) -> str:
    """Escape configured names so the chat's Markdown renderer shows them literally."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)
