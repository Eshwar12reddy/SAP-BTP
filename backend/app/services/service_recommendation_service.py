"""Deterministic SAP BTP service and plan recommendation engine.

DeepSeek is deliberately not part of eligibility or ranking.  This service only uses the
versioned reference catalog and, when explicitly requested, the already-authorized live
Cloud Foundry catalog.  Live provider visibility is never converted into entitlement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.auth.authorization import Principal
from app.auth.permissions import Permission
from app.core.exceptions import AppError, BadRequestError
from app.schemas.recommendations import (
    LiveCatalogStatus,
    RecommendationEvidence,
    RecommendationRequest,
    RecommendationResponse,
    RequirementsAnalysis,
    ServiceCatalogEntry,
    ServiceComparisonRequest,
    ServiceComparisonResponse,
    ServiceComparisonRow,
    ServicePrerequisitesResponse,
    ServiceRecommendation,
    ServiceRequirements,
)
from app.services.service_reference_catalog import (
    CATALOG_BY_KEY,
    CATALOG_VERSION,
    REFERENCE_CATALOG,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_CAPABILITY_TERMS: dict[str, tuple[str, ...]] = {
    "database": ("database", "persistence", "relational", "hana", "sql", "data store"),
    "integration": ("integration", "s/4", "s4hana", "s4 hana", "erp", "api management", "connect"),
    "identity-security": ("identity", "authentication", "authorization", "oauth", "sso", "security", "secure"),
    "messaging-events": ("event", "event-driven", "messaging", "queue", "asynchronous", "pub/sub"),
    "automation-workflow": ("workflow", "approval", "automation", "business process", "rules"),
    "monitoring-observability": ("monitor", "logging", "observability", "logs", "trace"),
    "artificial-intelligence": (" ai ", "machine learning", "ml ", "llm", "generative", "model"),
    "application-development": ("application", "app", "extension", "build", "develop", "employee management"),
}

_CAPABILITY_KEYS: dict[str, tuple[str, ...]] = {
    "database": ("hana-cloud",),
    "integration": ("integration-suite", "destination-service", "connectivity-service"),
    "identity-security": ("xsuaa", "cloud-identity-services"),
    "messaging-events": ("event-mesh",),
    "automation-workflow": ("build-process-automation",),
    "monitoring-observability": ("cloud-logging",),
    "artificial-intelligence": ("ai-core",),
    "application-development": ("cloud-foundry-runtime", "kyma-runtime", "abap-environment", "business-application-studio"),
}

_PRIORITY = {
    "integration-suite": 10,
    "hana-cloud": 9,
    "xsuaa": 8,
    "destination-service": 7,
    "connectivity-service": 6,
    "event-mesh": 5,
    "build-process-automation": 5,
    "ai-core": 5,
    "cloud-foundry-runtime": 4,
    "kyma-runtime": 4,
    "abap-environment": 4,
}

_REGION_WORD = re.compile(r"\b(?:region|located|deploy(?:ed)?|hosted)\s+(?:in|to)\s+([a-z0-9-]+)", re.IGNORECASE)
_EXCLUDE = re.compile(r"\b(?:avoid|exclude|without|do not use|don't use|not use)\s+([^.;,]+)", re.IGNORECASE)


@dataclass(frozen=True)
class _LiveEvidence:
    status: LiveCatalogStatus
    names: frozenset[str]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _has_term(text: str, term: str) -> bool:
    return term in text


def _capabilities_for(text: str) -> list[str]:
    lowered = f" {_clean(text)} "
    result: list[str] = []
    for capability, terms in _CAPABILITY_TERMS.items():
        if any(_has_term(lowered, term) for term in terms):
            result.append(capability)
    # A request to build an application needs a runtime even if no other capability was
    # named.  This is a documented design assumption, not a claim about capacity.
    if not result and any(term in lowered for term in ("build", "create", "develop", "application")):
        result.append("application-development")
    return result


def analyze_requirements(text: str) -> RequirementsAnalysis:
    """Extract only explicit signals from a user message."""

    cleaned = re.sub(r"\s+", " ", text.strip())
    lowered = _clean(cleaned)
    capabilities = _capabilities_for(cleaned)
    if ("s/4" in lowered or "s4hana" in lowered or "s4 hana" in lowered) and "integration" not in capabilities:
        capabilities.append("integration")
    runtime = "cloudfoundry" if "cloud foundry" in lowered or "cloudfoundry" in lowered else (
        "kyma" if "kyma" in lowered or "kubernetes" in lowered else (
            "abap" if "abap environment" in lowered or "abap" in lowered else "unknown"
        )
    )
    region_match = _REGION_WORD.search(cleaned)
    region = region_match.group(1) if region_match else None
    missing: list[str] = []
    assumptions: list[str] = []
    if runtime == "unknown":
        missing.append("runtime preference")
    if "workload" in lowered or "traffic" in lowered or "requests per" in lowered:
        workload = cleaned[:500]
    else:
        workload = None
        missing.append("workload characteristics")
    if "budget" not in lowered and "cost" not in lowered and "price" not in lowered:
        missing.append("budget or commercial constraints")
    if "s/4" in lowered or "s4hana" in lowered or "s4 hana" in lowered:
        assumptions.append("S/4HANA integration means the application must consume documented APIs, events or connectivity paths; the exact S/4HANA edition is not known.")
    exclusions = [part.strip()[:160] for part in _EXCLUDE.findall(cleaned)][:10]
    requirements = ServiceRequirements(
        business_objective=cleaned[:2_000] if cleaned else None,
        application_type="employee management application" if "employee management" in lowered else None,
        required_capabilities=capabilities,
        existing_sap_applications=["SAP S/4HANA"] if "s/4" in lowered or "s4hana" in lowered or "s4 hana" in lowered else [],
        s4hana_integration_requirements="Integration with SAP S/4HANA is requested." if "s/4" in lowered or "s4hana" in lowered or "s4 hana" in lowered else None,
        runtime_preference=runtime,
        database_requirements=["relational persistence"] if "database" in capabilities else [],
        identity_authentication_needs=["application authentication"] if "identity-security" in capabilities else [],
        messaging_event_requirements=["asynchronous events"] if "messaging-events" in capabilities else [],
        workflow_requirements=["business workflow or automation"] if "automation-workflow" in capabilities else [],
        monitoring_requirements=["operational observability"] if "monitoring-observability" in capabilities else [],
        region=region,
        security_compliance_constraints=["secure access"] if "identity-security" in capabilities else [],
        expected_workload=workload,
        unknown_requirements=missing,
        explicit_assumptions=assumptions,
        excluded_services=exclusions,
    )
    return RequirementsAnalysis(
        requirements=requirements,
        extracted_capabilities=capabilities,
        missing_critical_information=missing,
        assumptions=assumptions,
        analysis_status="NEEDS_CLARIFICATION" if missing else "READY",
    )


def _explicit_exclusion(requirements: ServiceRequirements, entry: ServiceCatalogEntry) -> bool:
    text = " ".join(requirements.excluded_services).lower()
    return bool(text) and (entry.service_key in text or entry.service_name.lower() in text)


def _runtime_status(requirements: ServiceRequirements, entry: ServiceCatalogEntry) -> str:
    preference = requirements.runtime_preference
    if preference == "unknown":
        return "NOT_REQUESTED"
    if preference == "cloudfoundry_or_kyma":
        return "SUPPORTED" if entry.runtime_compatibility else "UNKNOWN"
    if preference in entry.runtime_compatibility:
        return "SUPPORTED" if entry.runtime_compatibility[preference] == "DOCUMENTED" else "UNKNOWN"
    return "UNSUPPORTED" if entry.capability_category == "application-development" else "UNKNOWN"


def _capability_fit(entry: ServiceCatalogEntry, capabilities: set[str]) -> list[str]:
    return [capability for capability, keys in _CAPABILITY_KEYS.items() if entry.service_key in keys and capability in capabilities]


class ServiceRecommendationService:
    def __init__(self, *, live_service: object | None = None) -> None:
        self.live_service = live_service

    @staticmethod
    def analyze(text: str) -> RequirementsAnalysis:
        return analyze_requirements(text)

    async def _live_catalog(
        self,
        request: RecommendationRequest,
        session: AsyncSession | None,
        principal: Principal | None,
    ) -> _LiveEvidence:
        if not request.include_live_evidence:
            return _LiveEvidence(LiveCatalogStatus(retrieval_status="NOT_REQUESTED"), frozenset())
        if principal is None or not principal.has_permission(Permission.BTP_READ) or session is None or self.live_service is None:
            return _LiveEvidence(
                LiveCatalogStatus(retrieval_status="UNAUTHORIZED", notes=["An authorized BTP read scope and active environment are required for live account evidence."]),
                frozenset(),
            )
        try:
            authorized, offerings, more = await self.live_service.service_offerings(session, principal)
        except AppError as error:
            if error.code in {"btp_no_active_environment", "btp_integration_unavailable", "btp_credentials_unavailable"}:
                return _LiveEvidence(LiveCatalogStatus(retrieval_status="NOT_CONFIGURED", notes=["No active live Cloud Foundry catalog was available."]), frozenset())
            if error.status_code in (401, 403):
                return _LiveEvidence(LiveCatalogStatus(retrieval_status="UNAUTHORIZED", notes=["The live catalog was not authorized."]), frozenset())
            return _LiveEvidence(LiveCatalogStatus(retrieval_status="PARTIAL", notes=["The live provider catalog could not be read."]), frozenset())
        observed_environment = authorized.context.btp_environment_id
        if request.environment_id is not None and request.environment_id != observed_environment:
            return _LiveEvidence(LiveCatalogStatus(retrieval_status="UNAUTHORIZED", notes=["The requested environment is not the server-selected authorized environment."]), frozenset())
        names = frozenset(_clean(item.name) for item in offerings)
        return _LiveEvidence(
            LiveCatalogStatus(
                retrieval_status="PARTIAL" if more else "AVAILABLE",
                environment_id=observed_environment,
                observed_at=datetime.now(UTC),
                provider="cloud_foundry_v3",
                notes=["Provider catalog visibility is not entitlement evidence."]
                + (["Provider pagination was truncated."] if more else []),
            ),
            names,
        )

    async def recommend(
        self,
        request: RecommendationRequest,
        *,
        session: AsyncSession | None = None,
        principal: Principal | None = None,
    ) -> RecommendationResponse:
        analysis = (
            RequirementsAnalysis(
                requirements=request.requirements,
                extracted_capabilities=request.requirements.required_capabilities,
                missing_critical_information=request.requirements.unknown_requirements,
                assumptions=request.requirements.explicit_assumptions,
                analysis_status="NEEDS_CLARIFICATION" if request.requirements.unknown_requirements else "READY",
            )
            if request.requirements is not None
            else analyze_requirements(request.request_text or "")
        )
        live = await self._live_catalog(request, session, principal)
        requirements = analysis.requirements
        capabilities = set(analysis.extracted_capabilities)
        if not capabilities:
            capabilities = {"application-development"}
        candidates: list[tuple[int, ServiceCatalogEntry, list[str], str]] = []
        for entry in REFERENCE_CATALOG:
            fit = _capability_fit(entry, capabilities)
            if not fit:
                continue
            runtime = _runtime_status(requirements, entry)
            if runtime == "UNSUPPORTED" or _explicit_exclusion(requirements, entry):
                continue
            region = "UNKNOWN" if requirements.region else "NOT_REQUESTED"
            eligibility = "UNKNOWN" if runtime == "UNKNOWN" or region == "UNKNOWN" else "SUPPORTED"
            if requirements.region and entry.availability_verification_status != "DOCUMENTED_REFERENCE":
                eligibility = "UNKNOWN"
            existing = any(entry.service_name.lower() in item.lower() or entry.service_key in item.lower() for item in requirements.existing_services)
            score = len(fit) * 100 + _PRIORITY.get(entry.service_key, 0) + (20 if existing else 0)
            reason = "It directly covers the requested " + ", ".join(fit) + " capability."
            if existing:
                reason += " An existing service mention suggests reuse should be evaluated before adding another dependency."
            candidates.append((score, entry, fit, reason))
        candidates.sort(key=lambda item: (-item[0], item[1].service_key))
        recommendations: list[ServiceRecommendation] = []
        candidate_keys = {item[1].service_key for item in candidates}
        now = datetime.now(UTC)
        for _, entry, fit, reason in candidates[:20]:
            runtime = _runtime_status(requirements, entry)
            region = "UNKNOWN" if requirements.region else "NOT_REQUESTED"
            provider_visible = _clean(entry.service_name) in live.names or _clean(entry.technical_identifier or "") in live.names
            entitlement = "AVAILABLE_IN_PROVIDER_CATALOG" if provider_visible else (
                "EXISTING_SERVICE_NOT_VERIFIED" if any(entry.service_name.lower() in item.lower() for item in requirements.existing_services) else "NOT_VERIFIED"
            )
            evidence = [RecommendationEvidence(source_type="DOCUMENTATION", reference=str(entry.official_documentation_url), observed_at=entry.source_verification_timestamp, verification_status="VERIFIED")]
            if provider_visible:
                evidence.append(RecommendationEvidence(source_type="LIVE_PROVIDER", reference=f"cloud_foundry:service_offering:{entry.service_key}", observed_at=live.status.observed_at, verification_status="VERIFIED"))
            alternatives = [key for key in entry.alternative_service_keys if key in candidate_keys][:4]
            recommendations.append(ServiceRecommendation(
                recommendation_id=f"rec-{entry.service_key}", service_name=entry.service_name, verified_identifier=entry.technical_identifier,
                related_user_requirements=fit, recommended_capability=entry.capability_category,
                suitability_explanation=reason, eligibility_status=eligibility, runtime_status=runtime, region_status=region,
                entitlement_status=entitlement, quota_status="NOT_CHECKED", prerequisites=entry.prerequisites,
                relevant_alternatives=[CATALOG_BY_KEY[key].service_name for key in alternatives],
                tradeoffs=entry.limitations, evidence_references=evidence,
                suitability_confidence="HIGH" if len(fit) >= 2 or entry.service_key in {"hana-cloud", "integration-suite"} else "MEDIUM",
                availability_confidence=("LOW" if live.status.retrieval_status in {"AVAILABLE", "PARTIAL"} else "NOT_ASSESSED"),
                assumptions=analysis.assumptions, missing_information=analysis.missing_critical_information,
                source_verification_time=entry.source_verification_timestamp,
            ))
        return RecommendationResponse(
            analysis=analysis, recommendations=recommendations, no_suitable_options=not recommendations,
            partial_evidence=live.status.retrieval_status in {"PARTIAL", "UNAUTHORIZED"}, catalog_version=CATALOG_VERSION,
            live_catalog=live.status, generated_at=now,
        )

    def compare(self, request: ServiceComparisonRequest) -> ServiceComparisonResponse:
        analysis = (
            analyze_requirements(request.request_text or "") if request.requirements is None else RequirementsAnalysis(
                requirements=request.requirements, extracted_capabilities=request.requirements.required_capabilities,
                missing_critical_information=request.requirements.unknown_requirements, assumptions=request.requirements.explicit_assumptions,
                analysis_status="READY",
            )
        )
        rows: list[ServiceComparisonRow] = []
        for key in request.service_keys:
            entry = CATALOG_BY_KEY.get(key.strip().lower())
            if entry is None:
                raise BadRequestError("The requested service is not in the verified reference catalog.", code="invalid_catalog_service")
            fit = _capability_fit(entry, set(analysis.extracted_capabilities))
            rows.append(ServiceComparisonRow(
                service_key=entry.service_key, service_name=entry.service_name,
                capability_fit=("Fits " + ", ".join(fit) if fit else "No explicit capability match in the request"),
                meaningful_difference=(entry.limitations[0] if entry.limitations else "The documented use case differs; verify the target workload before choosing."),
                runtime_status=_runtime_status(analysis.requirements, entry),
                region_status="UNKNOWN" if analysis.requirements.region else "NOT_REQUESTED",
                entitlement_status="NOT_VERIFIED", prerequisites=entry.prerequisites,
                evidence_references=[RecommendationEvidence(source_type="DOCUMENTATION", reference=str(entry.official_documentation_url), observed_at=entry.source_verification_timestamp, verification_status="VERIFIED")],
            ))
        return ServiceComparisonResponse(rows=rows, criteria=["documented capability fit", "runtime compatibility", "prerequisites and limitations", "account-specific availability remains unverified"], missing_information=analysis.missing_critical_information, generated_at=datetime.now(UTC))

    def prerequisites(self, service_key: str) -> ServicePrerequisitesResponse:
        entry = CATALOG_BY_KEY.get(service_key.strip().lower())
        if entry is None:
            raise BadRequestError("The requested service is not in the verified reference catalog.", code="invalid_catalog_service")
        alternatives = [CATALOG_BY_KEY[key].service_name for key in entry.alternative_service_keys if key in CATALOG_BY_KEY]
        return ServicePrerequisitesResponse(
            service=entry, prerequisites=entry.prerequisites, limitations=entry.limitations, alternatives=alternatives,
            evidence_references=[RecommendationEvidence(source_type="DOCUMENTATION", reference=str(entry.official_documentation_url), observed_at=entry.source_verification_timestamp, verification_status="VERIFIED")],
        )
