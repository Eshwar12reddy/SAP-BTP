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
from app.agents.prompt_builder import AgentPromptBuilder
from app.agents.response_builder import AgentResponseBuilder
from app.agents.router import route_request
from app.agents.tool_gateway import ToolCallRequest, ToolGateway
from app.integrations.deepseek_client import DeepSeekClient
from app.safety.input_guard import (
    TrustLevel,
    UntrustedContent,
    normalize_for_matching,
    sanitize,
)
from app.safety.output_validation import check_answer
from app.safety.redaction import Redactor
from app.services.diagnosis_service import explain_diagnosis
from app.tools.btp import (
    APPLICATION_HEALTH_TOOL,
    APPLICATIONS_TOOL,
    ARCHITECTURE_DESIGN_TOOL,
    ARCHITECTURE_REQUIREMENTS_TOOL,
    CATALOG_TOOL,
    DESTINATIONS_TOOL,
    DIAGNOSE_APPLICATION_TOOL,
    ENTITLEMENTS_TOOL,
    HEALTH_OVERVIEW_TOOL,
    ORGANIZATIONS_TOOL,
    QUOTA_DIAGNOSE_TOOL,
    QUOTAS_TOOL,
    REQUIREMENTS_ANALYZE_TOOL,
    RESOURCE_SIZING_TOOL,
    RESOURCE_USAGE_TOOL,
    SERVICE_COMPARE_TOOL,
    SERVICE_INSTANCES_TOOL,
    SERVICE_OFFERINGS_TOOL,
    SERVICE_PLANS_TOOL,
    SERVICE_PREREQUISITES_TOOL,
    SERVICE_RECOMMEND_TOOL,
    SIZING_REQUIREMENTS_TOOL,
    SPACES_TOOL,
    ApplicationHealthOutput,
    ApplicationsOutput,
    ArchitectureDesignOutput,
    ArchitectureRequirementsOutput,
    CatalogEnvironment,
    CatalogOutput,
    DiagnosisOutput,
    HealthOverviewOutput,
    QuotaDiagnoseOutput,
    QuotasOutput,
    RequirementsAnalyzeOutput,
    ResourceSizingOutput,
    ResourceUsageOutput,
    ServiceCompareOutput,
    ServiceInstancesOutput,
    ServiceOfferingsOutput,
    ServicePlansOutput,
    ServicePrerequisitesOutput,
    ServiceRecommendOutput,
    SizingRequirementsOutput,
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
        self._prompt_builder = AgentPromptBuilder()

    async def handle(self, request: AgentRequest, context: AgentContext) -> AgentResponse:
        # Invisible characters can hide instructions or dodge the routing rules.
        text, _ = sanitize(request.message)
        if not text.strip():
            run = _Run(self._deepseek, self._registry, context, self._redactor)
            logger.warning(
                "Agent input rejected",
                extra={"event": "ai_input_rejected", "reason": "no_readable_text"},
            )
            decision = RoutingDecision(route=Route.UNSUPPORTED, method=RoutingMethod.RULE)
            return run.respond(decision, AgentOutcome.NEEDS_CLARIFICATION, _UNREADABLE, [])
        match_text = normalize_for_matching(text)
        decision = await self.classify(text, match_text=match_text)
        return await self.handle_decision(request, context, decision, text=text, match_text=match_text)

    async def classify(
        self,
        text: str,
        *,
        match_text: str | None = None,
        context: AgentContext | None = None,
    ) -> RoutingDecision:
        """Classify once so LangGraph can checkpoint the decision before execution."""

        cleaned, _ = sanitize(text)
        if not cleaned.strip():
            return RoutingDecision(route=Route.UNSUPPORTED, method=RoutingMethod.RULE)
        return await route_request(
            cleaned,
            self._deepseek,
            self._redactor,
            match_text=match_text or normalize_for_matching(cleaned),
            prompt_builder=self._prompt_builder,
            context_contents=(context.assembled_context.prompt_contents() if context and context.assembled_context else ()),
        )

    async def handle_decision(
        self,
        request: AgentRequest,
        context: AgentContext,
        decision: RoutingDecision,
        *,
        text: str | None = None,
        match_text: str | None = None,
    ) -> AgentResponse:
        """Execute a pre-classified request without re-running the classifier."""

        run = _Run(self._deepseek, self._registry, context, self._redactor)
        text = text if text is not None else sanitize(request.message)[0]
        if not text.strip():
            logger.warning(
                "Agent input rejected",
                extra={"event": "ai_input_rejected", "reason": "no_readable_text"},
            )
            unsupported = RoutingDecision(route=Route.UNSUPPORTED, method=RoutingMethod.RULE)
            return run.respond(unsupported, AgentOutcome.NEEDS_CLARIFICATION, _UNREADABLE, [])
        match_text = match_text or normalize_for_matching(text)
        if decision.method is RoutingMethod.MODEL:
            run.llm_calls = 1
        if decision.route is Route.OPERATION_REQUEST:
            logger.info(
                "Operation request refused; nothing executed",
                extra={
                    "event": "ai_operation_request_refused",
                    "reason": decision.method.value,
                },
            )
        response = await run.dispatch(decision, text, match_text)
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
        self._gateway = ToolGateway(registry, max_calls=MAX_TOOL_CALLS)
        self._responses = AgentResponseBuilder()
        self._prompt_builder = AgentPromptBuilder()
        self.llm_calls = 0
        self.tool_results: list[ToolResult] = []

    async def route(self, text: str, match_text: str) -> RoutingDecision:
        decision = await route_request(
            text,
            self._deepseek,
            self._redactor,
            match_text=match_text,
            prompt_builder=self._prompt_builder,
        )
        if decision.method is RoutingMethod.MODEL:
            self.llm_calls += 1
        return decision

    async def dispatch(
        self, decision: RoutingDecision, text: str, match_text: str
    ) -> AgentResponse:
        """Route execution after classification; shared by compatibility and graph callers."""

        if decision.route is Route.OPERATION_REQUEST:
            return self.respond(decision, AgentOutcome.OPERATION_NOT_PERFORMED, _OPERATION_REFUSAL, [])
        if decision.route is Route.UNSUPPORTED:
            return self.respond(decision, AgentOutcome.NEEDS_CLARIFICATION, _CLARIFICATION, [])
        if decision.route is Route.BTP_ACCOUNT:
            return await self.catalog(decision, match_text)
        if decision.route is Route.BTP_DIAGNOSIS:
            return await self.diagnosis(decision, text)
        if decision.route is Route.BTP_APPLICATIONS:
            return await self.applications(decision, match_text)
        if decision.route is Route.BTP_INVENTORY:
            return await self.inventory(decision, match_text)
        if decision.route is Route.BTP_HEALTH:
            return await self.health(decision, match_text)
        if decision.route is Route.BTP_RECOMMENDATIONS:
            return await self.recommendations(decision, text)
        if decision.route is Route.BTP_ARCHITECTURE:
            return await self.architecture(decision, text)
        if decision.route is Route.BTP_SIZING:
            return await self.sizing(decision, text)
        if decision.route is Route.BTP_INFRASTRUCTURE_PLAN:
            return await self.infrastructure_plan(decision, text)
        if decision.route is Route.KNOWLEDGE_SEARCH:
            return await self.knowledge_search(decision, text)
        return await self.model_answer(decision, text)

    async def infrastructure_plan(self, decision: RoutingDecision, text: str) -> AgentResponse:
        return self.respond(
            decision,
            AgentOutcome.NEEDS_CLARIFICATION,
            "I can generate a static review package, but I need the authorized architecture proposal ID, optional sizing result ID, and one supported format: Cloud Foundry manifest, MTA descriptor, or limited SAP/btp Terraform. No deployment or provisioning action is available.",
            [SourceType.CALCULATION],
            "Source: authorized Phase 24 infrastructure-plan generator. No SAP BTP system was changed or queried.",
        )

    async def run_tool(self, name: str, arguments: dict) -> ToolResult:
        request = ToolCallRequest(name=name, arguments=arguments)
        result = await self._gateway.execute(request, self._context)
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
        return self._responses.build(
            decision,
            outcome,
            body,
            sources,
            self.tool_results,
            self.llm_calls,
            footer=footer,
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

    async def diagnosis(self, decision: RoutingDecision, text: str) -> AgentResponse:
        application_id = _extract_application_id(text)
        if application_id is None:
            return self.respond(
                decision,
                AgentOutcome.NEEDS_CLARIFICATION,
                "Please provide the exact Cloud Foundry application ID or GUID. "
                "I will use only the currently selected and authorized environment.",
                [],
            )
        result = await self.run_tool(
            DIAGNOSE_APPLICATION_TOOL,
            {"application_id": application_id},
        )
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, f"diagnosis for application {application_id}")
        diagnosis = DiagnosisOutput.model_validate(result.data).diagnosis
        if (
            self._context.settings is not None
            and self._context.settings.BTP_DIAGNOSIS_AI_EXPLANATION_ENABLED
            and self._context.deepseek_client is not None
            and self._context.deepseek_client.configured
        ):
            self.llm_calls += 1
            explanation = await explain_diagnosis(
                diagnosis,
                self._context.deepseek_client,
                self._redactor,
                self._context.settings,
            )
            if explanation:
                diagnosis = diagnosis.model_copy(update={"ai_explanation": explanation})
        body = _render_diagnosis(diagnosis)
        footer = (
            f"Source: bounded live SAP BTP data from Cloud Foundry V3, retrieved "
            f"{result.timestamp:%Y-%m-%d %H:%M:%S} UTC. No infrastructure change was made."
        )
        return self.respond(decision, AgentOutcome.ANSWERED, body, [SourceType.LIVE_BTP], footer)

    async def inventory(self, decision: RoutingDecision, text: str) -> AgentResponse:
        lowered = text.lower()
        if "destination" in lowered:
            tool_name, arguments, subject = DESTINATIONS_TOOL, {}, "SAP destinations"
        elif "entitlement" in lowered:
            tool_name, arguments, subject = ENTITLEMENTS_TOOL, {}, "SAP entitlements"
        elif "quota" in lowered:
            quota_type = "space" if "space" in lowered and "organization" not in lowered else "all"
            tool_name, arguments, subject = QUOTAS_TOOL, {"quota_type": quota_type}, "Cloud Foundry quotas"
        elif "offering" in lowered:
            tool_name, arguments, subject = SERVICE_OFFERINGS_TOOL, {}, "Cloud Foundry service offerings"
        elif "plan" in lowered:
            tool_name, arguments, subject = SERVICE_PLANS_TOOL, {}, "Cloud Foundry service plans"
        elif "service instance" in lowered:
            tool_name, arguments, subject = SERVICE_INSTANCES_TOOL, {}, "Cloud Foundry service instances"
        elif "binding" in lowered:
            return self.respond(
                decision,
                AgentOutcome.NEEDS_CLARIFICATION,
                "Please provide the exact authorized Cloud Foundry application ID to list its service bindings.",
                [],
            )
        elif "organization" in lowered or " org" in lowered:
            tool_name, arguments, subject = ORGANIZATIONS_TOOL, {}, "Cloud Foundry organizations"
        else:
            tool_name, arguments, subject = SPACES_TOOL, {}, "Cloud Foundry spaces"
        result = await self.run_tool(tool_name, arguments)
        if result.status is ToolStatus.UNAVAILABLE:
            if result.error_code == "btp_destinations_not_configured":
                body = "SAP Destination Service inventory is NOT CONFIGURED. No destination data was retrieved."
            elif result.error_code == "btp_entitlements_not_configured":
                body = "SAP entitlement inventory is NOT CONFIGURED. No entitlement data was retrieved."
            else:
                body = f"I could not retrieve {subject}: the live integration is unavailable."
            return self.respond(decision, AgentOutcome.TOOL_UNAVAILABLE, body, [], _NO_LIVE_CHECK)
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, subject)
        if tool_name == SERVICE_OFFERINGS_TOOL:
            output = ServiceOfferingsOutput.model_validate(result.data)
            body = "Service offerings in the provider catalog:\n" + "\n".join(
                f"- {_md(item.name)} ({'available' if item.available else 'unknown availability'}; entitlement UNKNOWN)"
                for item in output.observations
            ) if output.observations else "The live provider catalog returned no service offerings."
        elif tool_name == SERVICE_PLANS_TOOL:
            output = ServicePlansOutput.model_validate(result.data)
            body = "Service plans in the provider catalog:\n" + "\n".join(
                f"- {_md(item.name)} (entitlement UNKNOWN)" for item in output.observations
            ) if output.observations else "The live provider catalog returned no service plans."
        elif tool_name == QUOTAS_TOOL:
            output = QuotasOutput.model_validate(result.data)
            body = "Cloud Foundry quotas:\n" + "\n".join(
                f"- {_md(item.name)} ({item.quota_type}; memory={item.total_memory_mb.state}"
                f"{f'/{item.total_memory_mb.value} MB' if item.total_memory_mb.value is not None else ''})"
                for item in output.observations
            ) if output.observations else "The live check returned no authorized quotas."
        elif tool_name == SERVICE_INSTANCES_TOOL:
            output = ServiceInstancesOutput.model_validate(result.data)
            body = "Service instances:\n" + "\n".join(
                f"- {_md(item.name)}" for item in output.observations
            ) if output.observations else "The live check returned no authorized service instances."
        else:
            body = f"The live check returned {subject}. See the tool result for typed observations."
        footer = f"Source: live Cloud Foundry V3 data, retrieved {result.timestamp:%Y-%m-%d %H:%M:%S} UTC."
        return self.respond(decision, AgentOutcome.ANSWERED, body, [SourceType.LIVE_BTP], footer)

    async def health(self, decision: RoutingDecision, text: str) -> AgentResponse:
        lowered = text.lower()
        application_id = _extract_application_id(text)
        resource_id = _extract_resource_id(text)
        if "unused" in lowered or "usage" in lowered and "memory" not in lowered:
            tool_name, arguments, subject = RESOURCE_USAGE_TOOL, {}, "potentially unused resources"
        elif "quota" in lowered:
            if resource_id and ("organization" in lowered or "space" in lowered):
                resource_type = "space" if "space" in lowered else "organization"
                tool_name = QUOTA_DIAGNOSE_TOOL
                arguments = {"resource_id": resource_id, "resource_type": resource_type}
            else:
                tool_name, arguments, subject = QUOTAS_TOOL, {"quota_type": "all"}, "Cloud Foundry quota observations"
        elif application_id:
            tool_name = APPLICATION_HEALTH_TOOL
            arguments = {"application_id": application_id}
            if "expected" in lowered or "should be running" in lowered:
                arguments["expected_running"] = True
            subject = f"health for application {application_id}"
        else:
            tool_name, arguments, subject = HEALTH_OVERVIEW_TOOL, {}, "application health overview"
        result = await self.run_tool(tool_name, arguments)
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, subject if "subject" in locals() else "health findings")
        if tool_name == HEALTH_OVERVIEW_TOOL:
            overview = HealthOverviewOutput.model_validate(result.data).overview
            findings = sorted(
                overview.findings,
                key=lambda item: (item.severity not in {"CRITICAL", "HIGH"}, item.confidence),
            )[:12]
            body = (
                f"Checked {overview.applications_checked} authorized application(s).\n\n"
                + "\n".join(
                    f"- [{finding.severity}/{finding.confidence}] {_md(finding.observation)}"
                    for finding in findings
                )
                if findings
                else "No supported health anomaly was detected in the collected evidence."
            )
        elif tool_name == APPLICATION_HEALTH_TOOL:
            diagnosis = ApplicationHealthOutput.model_validate(result.data).diagnosis
            body = _render_diagnosis(diagnosis)
        elif tool_name == QUOTA_DIAGNOSE_TOOL:
            diagnosis = QuotaDiagnoseOutput.model_validate(result.data).diagnosis
            body = "\n".join(
                f"- [{finding.severity}/{finding.confidence}] {_md(finding.observation)}"
                for finding in diagnosis.findings
            ) or "No quota finding was returned."
        else:
            usage = ResourceUsageOutput.model_validate(result.data).candidates
            body = (
                "Potentially unused candidates — requires verification:\n"
                + "\n".join(f"- {_md(item.display_name)} ({item.status})" for item in usage.items)
                if usage.items
                else "No potentially unused candidates were returned in the authorized scope."
            )
        footer = f"Source: bounded live Cloud Foundry evidence, retrieved {result.timestamp:%Y-%m-%d %H:%M:%S} UTC."
        return self.respond(decision, AgentOutcome.ANSWERED, body, [SourceType.LIVE_BTP], footer)

    async def recommendations(self, decision: RoutingDecision, text: str) -> AgentResponse:
        """Use the deterministic catalog boundary for service advice and comparisons."""
        from app.services.service_reference_catalog import CATALOG_BY_KEY

        history = "\n".join(
            turn.content for turn in (self._context.assembled_context.turns if self._context.assembled_context else ())
        )
        combined = f"{history}\n{text}"[-8_000:]
        lowered = text.lower()
        known_keys = [
            key for key, entry in CATALOG_BY_KEY.items()
            if key in combined.lower() or entry.service_name.lower() in combined.lower()
        ]
        known_keys = list(dict.fromkeys(known_keys))
        if "prerequisite" in lowered and known_keys:
            result = await self.run_tool(SERVICE_PREREQUISITES_TOOL, {"service_key": known_keys[0]})
            if result.status is not ToolStatus.SUCCESS:
                return self.tool_problem(decision, result, "documented service prerequisites")
            output = ServicePrerequisitesOutput.model_validate(result.data).response
            body = (
                f"Prerequisites for **{_md(output.service.service_name)}**:\n"
                + "\n".join(f"- {item}" for item in output.prerequisites)
                + ("\n\nLimitations:\n" + "\n".join(f"- {item}" for item in output.limitations) if output.limitations else "")
            )
            return self.respond(decision, AgentOutcome.ANSWERED, body, [SourceType.DOCUMENTATION], "Source: verified reference catalog documentation; account entitlement was not checked.")
        if ("compare" in lowered or "which one" in lowered or "which option" in lowered) and len(known_keys) >= 2:
            result = await self.run_tool(SERVICE_COMPARE_TOOL, {"service_keys": known_keys[:8], "request_text": combined})
            if result.status is not ToolStatus.SUCCESS:
                return self.tool_problem(decision, result, "service comparison")
            output = ServiceCompareOutput.model_validate(result.data).response
            body = "Service comparison:\n\n" + "\n".join(
                f"- **{_md(row.service_name)}** — {row.capability_fit}. {row.meaningful_difference} "
                f"Runtime: {row.runtime_status}; region: {row.region_status}; entitlement: {row.entitlement_status}."
                for row in output.rows
            )
            if output.missing_information:
                body += "\n\nMissing information: " + "; ".join(output.missing_information)
            return self.respond(decision, AgentOutcome.ANSWERED, body, [SourceType.DOCUMENTATION], "Source: verified SAP BTP reference catalog; prices, plans and entitlements were not inferred.")

        analysis_result = await self.run_tool(REQUIREMENTS_ANALYZE_TOOL, {"request_text": combined})
        if analysis_result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, analysis_result, "service requirements")
        analysis = RequirementsAnalyzeOutput.model_validate(analysis_result.data).analysis
        include_live = self._context.btp_read_service is not None and self._context.btp_environment_id is not None
        result = await self.run_tool(SERVICE_RECOMMEND_TOOL, {"request_text": combined, "include_live_evidence": include_live})
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, "documented SAP BTP service recommendations")
        output = ServiceRecommendOutput.model_validate(result.data).response
        if output.no_suitable_options:
            body = "No suitable documented service candidate was found for the explicit requirements."
        else:
            lines = ["Documented SAP BTP service recommendations:"]
            for item in output.recommendations[:8]:
                lines.append(
                    f"- **{_md(item.service_name)}** — {item.suitability_explanation} "
                    f"Eligibility: {item.eligibility_status}; runtime: {item.runtime_status}; "
                    f"region: {item.region_status}; entitlement: {item.entitlement_status}; quota: {item.quota_status}."
                )
                if item.prerequisites:
                    lines.append("  Prerequisites: " + "; ".join(item.prerequisites[:3]))
                if item.relevant_alternatives:
                    lines.append("  Alternatives: " + ", ".join(item.relevant_alternatives))
            body = "\n".join(lines)
        if analysis.missing_critical_information:
            body += "\n\nTo narrow this down, please provide: " + "; ".join(analysis.missing_critical_information)
        if output.live_catalog.retrieval_status != "NOT_REQUESTED":
            body += "\n\nAccount evidence: " + "; ".join(output.live_catalog.notes or [output.live_catalog.retrieval_status])
        footer = "Source: versioned SAP BTP reference catalog, with account-specific provider visibility shown separately when available. No infrastructure change was made."
        return self.respond(decision, AgentOutcome.ANSWERED, body, [SourceType.DOCUMENTATION], footer)

    async def architecture(self, decision: RoutingDecision, text: str) -> AgentResponse:
        """Render a server-generated architecture proposal from validated contracts."""
        history = "\n".join(
            turn.content for turn in (self._context.assembled_context.turns if self._context.assembled_context else ())
        )
        combined = f"{history}\n{text}"[-8_000:]
        analysis_result = await self.run_tool(ARCHITECTURE_REQUIREMENTS_TOOL, {"request_text": combined})
        if analysis_result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, analysis_result, "architecture requirements")
        analysis = ArchitectureRequirementsOutput.model_validate(analysis_result.data).analysis
        include_live = self._context.btp_read_service is not None and self._context.btp_environment_id is not None
        result = await self.run_tool(ARCHITECTURE_DESIGN_TOOL, {"request_text": combined, "include_live_evidence": include_live})
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, "SAP BTP architecture proposal")
        proposal = ArchitectureDesignOutput.model_validate(result.data).proposal
        lines = [f"# {proposal.title}", "", proposal.overview, "", "## Runtime options", *[f"- **{item.name}** — {item.suitability}: {item.rationale}" for item in proposal.runtime_options], "", "## Components"]
        lines.extend(f"- **{item.name}** (`{item.component_id}`) — {item.purpose}" for item in proposal.components)
        lines.extend(["", "## Connections", *[f"- `{item.source_component_id}` → `{item.target_component_id}` — {item.purpose} ({item.communication_pattern})" for item in proposal.connections[:20]], "", "## Security design", *[f"- **{item.control}** — {item.recommendation}" for item in proposal.security_controls], "", "## Risks and assumptions"])
        lines.extend(f"- **Risk:** {item.risk} — {item.mitigation}" for item in proposal.risks[:10])
        lines.extend(f"- **Assumption:** {item.statement}" for item in proposal.assumptions[:10])
        if proposal.alternatives:
            lines.extend(["", "## Alternatives", *[f"- **{item.name}** — {item.change_from_primary}; benefits: {', '.join(item.benefits[:2])}; drawbacks: {', '.join(item.drawbacks[:2])}" for item in proposal.alternatives]])
        lines.extend(["", "## Validation", f"- Status: **{'valid' if proposal.validation.valid else 'review required'}**", *[f"- {item.severity}: {item.message}" for item in proposal.validation.findings], "", "## Diagram", "```mermaid", proposal.diagram.mermaid, "```"])
        if analysis.clarification_questions:
            lines.extend(["", "## Clarifications", *[f"- {question}" for question in analysis.clarification_questions]])
        footer = "Source: server-generated proposal using the verified SAP BTP reference catalog and official documentation references. Account, region, entitlement, quota and pricing facts are not inferred. No infrastructure change was made."
        return self.respond(decision, AgentOutcome.ANSWERED, "\n".join(lines), [SourceType.DOCUMENTATION], footer)

    async def sizing(self, decision: RoutingDecision, text: str) -> AgentResponse:
        """Run the deterministic sizing boundary and render its formulas unchanged."""
        analysis_result = await self.run_tool(SIZING_REQUIREMENTS_TOOL, {"request_text": text[-8_000:]})
        if analysis_result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, analysis_result, "sizing requirements")
        analysis = SizingRequirementsOutput.model_validate(analysis_result.data).analysis
        result = await self.run_tool(RESOURCE_SIZING_TOOL, {"request": {"requirements": analysis.requirements.model_dump(mode="json"), "scenario_id": "baseline", "persist": False}})
        if result.status is not ToolStatus.SUCCESS:
            return self.tool_problem(decision, result, "deterministic resource sizing")
        sizing = ResourceSizingOutput.model_validate(result.data).result
        lines = [f"# Deterministic sizing ({sizing.scenario_id})", "", f"Status: **{sizing.status}**", f"Formula version: `{sizing.formula_version}`"]
        for item in sizing.calculations:
            value = "unknown" if item.final_value is None else f"{item.final_value} {item.result_unit}"
            lines.extend(["", f"## {item.calculation_type}", f"- Result: **{value}**", f"- Formula: `{item.formula.expression}`", f"- Inputs: {', '.join(f'{key}={value}' for key, value in item.input_values.items())}"])
            lines.extend(f"- Warning: {warning}" for warning in item.warnings)
        if sizing.quota_impacts:
            lines.extend(["", "## Quota impact"])
            lines.extend(f"- {item.dimension}: {item.quota_state}; remaining={item.remaining_capacity}; utilization={item.projected_utilization_percent or 'unknown'}" for item in sizing.quota_impacts)
        if sizing.missing_inputs:
            lines.extend(["", "## Missing inputs", *[f"- {item}" for item in sizing.missing_inputs]])
        if sizing.warnings:
            lines.extend(["", "## Warnings", *[f"- {item}" for item in sizing.warnings]])
        footer = "Source: deterministic server-side sizing formulas and explicitly supplied inputs. User count does not determine capacity by itself; no infrastructure change was made."
        return self.respond(decision, AgentOutcome.ANSWERED, "\n".join(lines), [SourceType.CALCULATION], footer)

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
        prompt = self._prompt_builder.build(
            f"{_SYSTEM_RULES}\n\n{instructions}",
            [
                UntrustedContent(TrustLevel.AUTHENTICATED_USER_INPUT, "user_message", text),
                *(
                    self._context.assembled_context.prompt_contents()
                    if self._context.assembled_context
                    else []
                ),
            ],
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


_APPLICATION_ID = re.compile(
    r"\b(?:application|app)\s+(?:id|guid)\s*(?:is|=|:)?\s*"
    r"([A-Za-z0-9][A-Za-z0-9._:-]{0,159})\b",
    re.IGNORECASE,
)
_EXPLICIT_APP_TOKEN = re.compile(r"\b(?:app|application)[-_:][A-Za-z0-9][A-Za-z0-9._:-]{0,159}\b", re.IGNORECASE)
_RESOURCE_ID_TOKEN = re.compile(
    r"\b(?:organization|org|space|quota)\s+(?:id|guid)?\s*(?:is|=|:)?\s*"
    r"([A-Za-z0-9][A-Za-z0-9._:-]{0,159})\b",
    re.IGNORECASE,
)


def _extract_application_id(text: str) -> str | None:
    explicit = _APPLICATION_ID.search(text)
    if explicit:
        return explicit.group(1)
    token = _EXPLICIT_APP_TOKEN.search(text)
    return token.group(0) if token else None


def _extract_resource_id(text: str) -> str | None:
    match = _RESOURCE_ID_TOKEN.search(text)
    return match.group(1) if match else None


def _render_diagnosis(diagnosis) -> str:
    summary = diagnosis.summary
    lines = [
        f"Diagnosis for {_md(summary.display_name)} (`{_md(summary.application_id)}`)",
        (
            f"State: {_md(summary.state or 'unknown')}"
            f"; requested instances: {summary.requested_instances if summary.requested_instances is not None else 'unknown'}"
            f"; running instances: {summary.running_instances if summary.running_instances is not None else 'unknown'}."
        ),
        "",
    ]
    if diagnosis.findings:
        lines.append("Findings:")
        for finding in diagnosis.findings:
            lines.append(
                f"- **{finding.severity} / {finding.confidence}** {finding.observation} "
                f"(rule `{finding.rule_id}`)"
            )
            if finding.possible_causes:
                lines.append("  Possible causes: " + "; ".join(finding.possible_causes))
            if finding.recommended_next_steps:
                lines.append("  Next steps: " + "; ".join(finding.recommended_next_steps))
            if finding.evidence_references:
                lines.append("  Evidence: " + ", ".join(finding.evidence_references))
    if diagnosis.missing_information:
        lines.extend(["", "Missing information:", *[f"- {item}" for item in diagnosis.missing_information]])
    lines.extend(["", f"Root cause assessment: {diagnosis.root_cause_statement}"])
    if diagnosis.ai_explanation:
        lines.extend(["", "AI explanation (based only on the verified findings):", diagnosis.ai_explanation])
    return "\n".join(lines)


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~])")


def _md(text: str) -> str:
    """Escape configured names so the chat's Markdown renderer shows them literally."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)
