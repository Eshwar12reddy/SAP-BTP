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
from app.integrations.deepseek_client import DeepSeekClient
from app.safety.input_guard import TrustLevel, UntrustedContent, build_messages
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
_ACCOUNT_NOUN = r"(global\s+accounts?|sub-?accounts?|accounts?|environments?|spaces?|orgs?)"
_ACCOUNTS = re.compile(
    rf"\b(show|list|display|which|available|my|our|have\s+access|can\s+i\s+(see|access)|"
    rf"do\s+i\s+have)\b.*\b{_ACCOUNT_NOUN}\b",
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
    (Route.OPERATION_REQUEST, (_OPERATION,)),
    (Route.KNOWLEDGE_SEARCH, (_KNOWLEDGE_SEARCH,)),
    (Route.SIZING, (_SIZING,)),
    (Route.ARCHITECTURE, (_ARCHITECTURE,)),
    (Route.BTP_APPLICATIONS, _APPLICATIONS),
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
    text: str, deepseek: DeepSeekClient, redactor: Redactor, *, match_text: str | None = None
) -> RoutingDecision:
    """``match_text`` is the normalized form used for rules; ``text`` is what the model sees."""
    route = route_by_rules(match_text if match_text is not None else text)
    if route is not None:
        return RoutingDecision(route=route, method=RoutingMethod.RULE)
    prompt = build_messages(
        _CLASSIFIER_PROMPT,
        [UntrustedContent(TrustLevel.AUTHENTICATED_USER_INPUT, "user_message", text)],
        redactor,
    )
    result = await deepseek.chat(prompt.messages, max_tokens=32)
    return RoutingDecision(route=parse_classification(result.content), method=RoutingMethod.MODEL)
