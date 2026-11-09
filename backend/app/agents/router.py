"""Request routing: deterministic rules first, model classification only as a fallback.

Rules are checked in priority order. Operation requests come first so that an imperative
such as "restart my app" can never fall through to a read route. When no rule matches,
DeepSeek may classify the request, but only through the strict ``RouteClassification``
contract; anything else becomes ``UNSUPPORTED``. A route never authorizes anything: the
tools for a route are fixed in code and enforce access themselves.
"""

from __future__ import annotations

import re

from pydantic import field_validator

from app.agents.contracts import Route, RoutingDecision, RoutingMethod
from app.agents.prompt_builder import AgentPromptBuilder
from app.integrations.deepseek_client import DeepSeekClient
from app.safety.input_guard import TrustLevel, UntrustedContent
from app.safety.output_validation import (
    ModelOutputError,
    StrictModelOutput,
    log_rejected_output,
    parse_structured,
)
from app.safety.redaction import Redactor

_OPERATION_VERBS = (
    r"restart|start|stop|deploy|redeploy|undeploy|delete|remove|scale|increase|decrease|"
    r"reduce|resize|create|provision|bind|unbind|update|upgrade|roll\s*back|rollback|kill|"
    r"terminate|shut\s*down|enable|disable|set|change|modify|rotate|purge|drop|move|rename"
)
# Optional politeness before an imperative: "please", "can you", "go ahead and", ...
_IMPERATIVE_PREFIX = (
    r"(?:(?:please|kindly|now|then|ok|okay)[,\s]+"
    r"|(?:can|could|would|will)\s+(?:you|we)\s+(?:please\s+)?"
    r"|(?:go\s+ahead\s+and|let'?s|i\s+(?:want|need)\s+you\s+to|i'?d\s+like\s+you\s+to)\s+)*"
)
_OPERATION = re.compile(rf"^\s*{_IMPERATIVE_PREFIX}(?:{_OPERATION_VERBS})\b", re.IGNORECASE)
_KNOWLEDGE_SEARCH = re.compile(
    r"\b(knowledge\s+base|uploaded\s+(documents?|files?)|our\s+(documents?|docs)|"
    r"search\s+(the\s+)?(documents?|docs|knowledge)|according\s+to\s+(the\s+)?"
    r"(uploaded\s+)?(document|docs))\b",
    re.IGNORECASE,
)

_SIZING = re.compile(
    r"\b(sizing|size|estimate|estimation|capacity|how\s+many\s+instances|"
    r"(memory|cpu|disk|ram)\s+requirements?|requests?\s+(per|a|an)\s+(second|minute|hour|day)|"
    r"rps|throughput|how\s+much\s+(memory|ram|cpu|disk))\b",
    re.IGNORECASE,
)
_RECOMMENDATIONS = (
    re.compile(r"\b(which|what|recommend|suggest|suitable|best)\b.{0,50}\b(service(?!\s+(?:instance|binding|key|broker))|services|plan|plans)\b", re.IGNORECASE),
    re.compile(r"\b(?:use|choose|compare)\b.{0,60}\b(integration\s+suite|hana\s+cloud|event\s+mesh|ai\s+core|destination|connectivity)\b", re.IGNORECASE),
)
_ARCHITECTURE_ADVISOR = (
    re.compile(r"\b(?:sap\s+btp|btp)\b.{0,80}\barchitecture\b", re.IGNORECASE),
    re.compile(r"\barchitecture\s+(?:proposal|blueprint|advisor|review|graph|diagram)\b", re.IGNORECASE),
)
_SIZING_ADVISOR = (
    re.compile(r"\b(?:sap\s+btp|btp|cloud\s+foundry|kyma|abap|hana)\b.{0,100}\b(?:sizing|size|capacity|memory|instances?|storage|quota)\b", re.IGNORECASE),
    re.compile(r"\b(?:calculate|calculate the|resource sizing|resource requirements|memory allocation|storage projection|quota impact|traffic doubles)\b", re.IGNORECASE),
)
_INFRASTRUCTURE_PLAN = re.compile(
    r"\b(generate|create|show|build|prepare|update)\b.{0,80}\b(manifest|mta(?:\.ya?ml|\s+descriptor)?|terraform|infrastructure\s+(?:plan|configuration)|configuration\s+files?)\b",
    re.IGNORECASE,
)
_ARCHITECTURE = re.compile(
    r"\b(architecture|architect|reference\s+design|design\s+(a|an|the|our|my)|recommend|"
    r"suggest|best\s+practices?|how\s+should\s+(we|i)|best\s+way|scalable|"
    r"high\s+availability|integration\s+pattern)\b",
    re.IGNORECASE,
)
_APP = r"(apps?|applications?)"
_APPLICATIONS = (
    re.compile(rf"\b(show|list|display|which|inventory)\b.*\b{_APP}\b", re.IGNORECASE),
    re.compile(
        rf"\b{_APP}\b.*\b(running|deployed|started|stopped|crashed|healthy|status)\b",
        re.IGNORECASE,
    ),
    re.compile(rf"\b(what|my|our)\s+{_APP}\b", re.IGNORECASE),
)
_DIAGNOSIS = (
    re.compile(
        rf"\b(why|diagnos(?:e|is)|root\s+cause|troubleshoot|instability|crash(?:ed|es|ing)?|"
        rf"fewer\s+instances?|instance\s+count|deployment\s+failed|health\s+check)\b.*\b{_APP}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_APP}\b.*\b(why|diagnos(?:e|is)|stopped|crashed|fewer\s+instances?|"
        rf"instability|root\s+cause|deployment\s+failed|health\s+check)\b",
        re.IGNORECASE,
    ),
)
_ACCOUNT_NOUN = r"(global\s+accounts?|sub-?accounts?|accounts?|environments?|spaces?|orgs?)"
_ACCOUNTS = re.compile(
    rf"\b(show|list|display|which|available|my|our|have\s+access|can\s+i\s+(see|access)|"
    rf"do\s+i\s+have)\b.*\b{_ACCOUNT_NOUN}\b",
    re.IGNORECASE,
)
_INVENTORY = re.compile(
    r"\b(list|show|display|which|inventory|what\s+are|how\s+many)\b.*\b"
    r"(service\s+(offerings?|plans?|instances?|bindings?)|destinations?|"
    r"entitlements?|quotas?|cloud\s+foundry\s+(orgs?|organizations?|spaces?))\b",
    re.IGNORECASE,
)
_HEALTH = re.compile(
    r"\b(stopped|crashed|failing|failed|problems?|unhealthy|health|quota\s+(pressure|exhaust|usage)|"
    r"memory\s+(usage|utili[sz]ation)|instance(s)?\s+(missing|unavailable|down)|"
    r"potentially\s+unused|unused\s+(service|resource))\b",
    re.IGNORECASE,
)
_DOCUMENTATION = re.compile(
    r"\b(destinations?|service\s+(instances?|bindings?|keys?|brokers?|marketplace)|bindings?|"
    r"entitlements?|quotas?|role\s+collections?|xsuaa|connectivity|cloud\s+connector|html5|"
    r"mta|multi-target|buildpacks?|manifest|trust\s+configuration|subscriptions?|"
    r"global\s+accounts?|sub-?accounts?|directories|app(lication)?\s+router|approuter)\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(
    r"\b(what|how|why|when|explain|describe|difference|compare|define|tell\s+me\s+about|"
    r"overview)\b|\?\s*$",
    re.IGNORECASE,
)

_RULES: tuple[tuple[Route, tuple[re.Pattern[str], ...]], ...] = (
    (Route.BTP_INFRASTRUCTURE_PLAN, (_INFRASTRUCTURE_PLAN,)),
    (Route.OPERATION_REQUEST, (_OPERATION,)),
    (Route.KNOWLEDGE_SEARCH, (_KNOWLEDGE_SEARCH,)),
    (Route.BTP_RECOMMENDATIONS, _RECOMMENDATIONS),
    (Route.BTP_ARCHITECTURE, _ARCHITECTURE_ADVISOR),
    (Route.BTP_SIZING, _SIZING_ADVISOR),
    (Route.SIZING, (_SIZING,)),
    (Route.ARCHITECTURE, (_ARCHITECTURE,)),
    (Route.BTP_DIAGNOSIS, _DIAGNOSIS),
    (Route.BTP_HEALTH, (_HEALTH,)),
    (Route.BTP_APPLICATIONS, _APPLICATIONS),
    (Route.BTP_INVENTORY, (_INVENTORY,)),
    (Route.BTP_ACCOUNT, (_ACCOUNTS,)),
    (Route.DOCUMENTATION, (_DOCUMENTATION,)),
    (Route.GENERAL_KNOWLEDGE, (_QUESTION,)),
)

_CLASSIFIER_PROMPT = (
    "Classify the user's message (the untrusted_content block) into exactly one label:\n"
    "GENERAL_KNOWLEDGE: a general question about SAP BTP or cloud technology.\n"
    "DOCUMENTATION: how a specific SAP BTP feature or service works.\n"
    "KNOWLEDGE_SEARCH: search the user's authorized uploaded knowledge documents.\n"
    "BTP_ACCOUNT: list the user's own BTP accounts, subaccounts or environments.\n"
    "BTP_APPLICATIONS: list or check applications in the user's environments.\n"
    "BTP_INVENTORY: retrieve authorized live Cloud Foundry inventory such as organizations, spaces, service offerings, service plans or quotas.\n"
    "BTP_HEALTH: analyze authorized live application health, process evidence, quota pressure or potentially unused resources.\n"
    "BTP_DIAGNOSIS: diagnose one explicitly identified application from live authorized evidence.\n"
    "BTP_RECOMMENDATIONS: analyze requirements and recommend or compare documented SAP BTP services; do not infer entitlement, prices or plans.\n"
    "BTP_ARCHITECTURE: generate or revise a structured SAP BTP architecture proposal with validated components, connections, environments, security controls and evidence.\n"
    "BTP_SIZING: calculate deterministic resource sizing from explicit inputs, formulas and authorized evidence; never invent workload, quota or benchmark values.\n"
    "BTP_INFRASTRUCTURE_PLAN: generate or validate static Cloud Foundry manifests, MTA descriptors or limited SAP/btp Terraform files from authorized architecture and sizing records; never deploy or provision.\n"
    "ARCHITECTURE: design or architecture recommendations.\n"
    "SIZING: capacity, memory, instance or cost estimates.\n"
    "OPERATION_REQUEST: change infrastructure (deploy, restart, scale, delete, configure).\n"
    "UNSUPPORTED: anything else.\n"
    'Reply with only this JSON object and nothing else: {"route": "<LABEL>"}'
)


class RouteClassification(StrictModelOutput):
    """The model's classification. A label only: it selects no tool and grants nothing."""

    route: Route

    @field_validator("route", mode="before")
    @classmethod
    def _label(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value


def route_by_rules(text: str) -> Route | None:
    for route, patterns in _RULES:
        if any(pattern.search(text) for pattern in patterns):
            return route
    return None


def parse_classification(output: str) -> Route:
    """Validate the model's JSON against ``RouteClassification``. Invalid means UNSUPPORTED:
    no repair attempt, no guessed fields, no tool."""
    try:
        return parse_structured(output, RouteClassification).route
    except ModelOutputError as exc:
        log_rejected_output("route_classification", exc.category)
        return Route.UNSUPPORTED


async def route_request(
    text: str,
    deepseek: DeepSeekClient,
    redactor: Redactor,
    *,
    match_text: str | None = None,
    prompt_builder: AgentPromptBuilder | None = None,
    context_contents: tuple[UntrustedContent, ...] = (),
) -> RoutingDecision:
    """``match_text`` is the normalized form used for rules; ``text`` is what the model sees."""
    route = route_by_rules(match_text if match_text is not None else text)
    if route is not None:
        return RoutingDecision(route=route, method=RoutingMethod.RULE)
    prompt = (prompt_builder or AgentPromptBuilder()).build(
        _CLASSIFIER_PROMPT,
        [
            UntrustedContent(TrustLevel.AUTHENTICATED_USER_INPUT, "user_message", text),
            *context_contents,
        ],
        redactor,
    )
    result = await deepseek.chat(prompt.messages, max_tokens=32)
    return RoutingDecision(route=parse_classification(result.content), method=RoutingMethod.MODEL)
