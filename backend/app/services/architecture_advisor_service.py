"""Structured SAP BTP architecture advisor built on Phase 21 recommendations.

The advisor generates a reviewable proposal from typed requirements. It does not execute
deployments, create accounts, assign entitlements or accept model-generated component
graphs. Components, connections and Mermaid are produced and validated server-side.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Principal, coverage_for
from app.auth.permissions import Permission
from app.core.exceptions import BadRequestError, NotFoundError, PermissionDeniedError
from app.models import ArchitectureProposalRecord, BTPAccount, BTPEnvironment
from app.schemas.architecture import (
    ArchitectureAlternative,
    ArchitectureAssumption,
    ArchitectureComparisonRequest,
    ArchitectureComparisonResponse,
    ArchitectureComponent,
    ArchitectureConnection,
    ArchitectureDataFlow,
    ArchitectureDiagram,
    ArchitectureEnvironment,
    ArchitectureEvidence,
    ArchitectureGenerationRequest,
    ArchitectureGraphResponse,
    ArchitectureProposal,
    ArchitectureRequirements,
    ArchitectureRequirementsAnalysis,
    ArchitectureRevisionRequest,
    ArchitectureRisk,
    ArchitectureRuntimeOption,
    ArchitectureSecurityControl,
    ArchitectureValidation,
    ArchitectureValidationFinding,
    RequirementSource,
)
from app.schemas.recommendations import RecommendationRequest, ServiceRequirements
from app.services.service_recommendation_service import (
    ServiceRecommendationService,
    analyze_requirements,
)
from app.services.service_reference_catalog import CATALOG_BY_KEY

if TYPE_CHECKING:
    from app.core.config import Settings


_NUMBER = re.compile(r"\b(?:around|about|for|of)?\s*([0-9][0-9,]*)\s*(?:employees|users|people|customers)\b", re.IGNORECASE)
_PROJECT = re.compile(r"\b(?:build|create|design|develop)\s+(?:an?|the\s+)?(.{2,100}?)\s+application\b", re.IGNORECASE)
_EXTERNAL = re.compile(r"\b(?:integrat(?:e|ed|ion) with|connect(?:ed)? to)\s+([^.;]+)", re.IGNORECASE)

_DOCS = {
    "guidance": "https://help.sap.com/docs/sap-btp-guidance-framework",
    "account-model": "https://help.sap.com/docs/btp/sap-business-technology-platform/account-model",
    "environments": "https://help.sap.com/docs/btp/sap-business-technology-platform/environments",
    "integration-guide": "https://help.sap.com/docs/sap-btp-guidance-framework/integration-architecture-guide",
}


def _source(classification: str, value: str, *references: str) -> RequirementSource:
    return RequirementSource(classification=classification, value=value, evidence_references=list(references))


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _.-]", "", value)[:80] or "Component"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _catalog_entry(identifier: str):
    if identifier in CATALOG_BY_KEY:
        return CATALOG_BY_KEY[identifier]
    return next((item for item in CATALOG_BY_KEY.values() if item.technical_identifier == identifier), None)


class ArchitectureAdvisorService:
    def __init__(self, *, recommendation_service: ServiceRecommendationService, settings: Settings | None = None) -> None:
        self.recommendation_service = recommendation_service
        self.settings = settings

    def analyze(self, text: str) -> ArchitectureRequirementsAnalysis:
        cleaned = re.sub(r"\s+", " ", text.strip())
        if not cleaned:
            raise BadRequestError("Architecture requirements must not be blank.", code="architecture_requirements_blank")
        recommendation_analysis = analyze_requirements(cleaned)
        lowered = _normal(cleaned)
        user_match = _NUMBER.search(cleaned)
        project_match = _PROJECT.search(cleaned)
        external_match = _EXTERNAL.search(cleaned)
        runtime = recommendation_analysis.requirements.runtime_preference
        user_count = int(user_match.group(1).replace(",", "")) if user_match else None
        existing_sap = recommendation_analysis.requirements.existing_sap_applications
        external = [external_match.group(1).strip()[:180]] if external_match else []
        integration_requirements = []
        if existing_sap:
            integration_requirements.append("SAP S/4HANA integration")
        if "event" in lowered or "asynchronous" in lowered:
            integration_requirements.append("event-driven or asynchronous integration")
        if "on-premise" in lowered or "on premise" in lowered or "cloud connector" in lowered:
            integration_requirements.append("cloud-to-on-premise connectivity")
        if not integration_requirements and "integration" in lowered:
            integration_requirements.append("API-based integration")
        auth = []
        authorization = []
        if any(word in lowered for word in ("login", "authentication", "identity", "secure")):
            auth.append("authenticated application access")
        if any(word in lowered for word in ("role", "authorization", "approval", "least privilege")):
            authorization.append("role-based authorization and least privilege")
        missing = list(recommendation_analysis.missing_critical_information)
        if not user_count:
            missing.append("user population and user types")
        if not recommendation_analysis.requirements.region:
            missing.append("preferred BTP region")
        if "availability" not in lowered and "recovery" not in lowered and "rto" not in lowered and "rpo" not in lowered:
            missing.append("availability and recovery objectives")
        missing = _dedupe(missing)
        assumptions = [
            _source("ASSUMPTION", "The proposal is a logical target architecture, not a statement of registered accounts or entitlements."),
        ]
        if runtime == "unknown":
            assumptions.append(_source("UNKNOWN", "Runtime is not specified; Cloud Foundry is used as the preliminary primary option because it is the simplest documented application-runtime starting point."))
        if existing_sap:
            assumptions.append(_source("ASSUMPTION", "The named SAP system exposes supported APIs, events or connectivity paths; its edition and interface inventory were not verified."))
        explicit = [_source("EXPLICIT_REQUIREMENT", cleaned)]
        if user_count:
            explicit.append(_source("EXPLICIT_REQUIREMENT", f"Approximately {user_count} users or employees."))
        requirements = ArchitectureRequirements(
            project_name=project_match.group(1).strip()[:200] if project_match else None,
            business_objective=cleaned[:2_000],
            application_type=recommendation_analysis.requirements.application_type or "business application",
            user_count=user_count,
            user_types=["employees"] if "employee" in lowered else [],
            existing_sap_systems=existing_sap,
            external_systems=external,
            integration_requirements=_dedupe(integration_requirements),
            database_requirements=recommendation_analysis.requirements.database_requirements,
            runtime_preference=runtime,
            authentication_requirements=auth,
            authorization_requirements=authorization,
            data_sensitivity="sensitive business or personal data" if any(word in lowered for word in ("employee", "personal", "sensitive", "pii")) else None,
            compliance_constraints=[word for word in ("GDPR", "SOX", "HIPAA") if word.lower() in lowered],
            preferred_region=recommendation_analysis.requirements.region,
            availability_objectives=cleaned[:500] if "availability" in lowered else None,
            recovery_objectives=cleaned[:500] if "recovery" in lowered or "rto" in lowered or "rpo" in lowered else None,
            expected_traffic=recommendation_analysis.requirements.expected_workload,
            workload_assumptions=["Traffic volume and peak profile require confirmation."] if not recommendation_analysis.requirements.expected_workload else [],
            existing_services=recommendation_analysis.requirements.existing_services,
            budget_constraints=recommendation_analysis.requirements.budget_constraints,
            missing_information=missing,
            explicit_requirements=explicit,
            assumptions=assumptions,
            unknown_requirements=[_source("UNKNOWN", item) for item in missing],
        )
        capability_keys = _dedupe(recommendation_analysis.extracted_capabilities or ["application-development"])
        questions = ["What is the preferred runtime: Cloud Foundry, Kyma, or ABAP environment?" if runtime == "unknown" else ""]
        questions.extend([
            "Which BTP region and account model should be used?" if not requirements.preferred_region else "",
            "What availability, RTO and RPO objectives must the design meet?" if not requirements.availability_objectives and not requirements.recovery_objectives else "",
            "What are the peak traffic, payload and integration-volume characteristics?" if not requirements.expected_traffic else "",
        ])
        questions = _dedupe(questions)
        return ArchitectureRequirementsAnalysis(
            requirements=requirements,
            capability_keys=capability_keys,
            missing_critical_information=missing,
            clarification_questions=questions[:8],
            analysis_status="NEEDS_CLARIFICATION" if missing else "READY",
        )

    def analyze_typed(self, requirements: ArchitectureRequirements) -> ArchitectureRequirementsAnalysis:
        capability_keys = _dedupe([
            *requirements.database_requirements,
            *requirements.integration_requirements,
            "identity-security" if requirements.authentication_requirements or requirements.authorization_requirements else "",
            "application-development",
        ])
        return ArchitectureRequirementsAnalysis(
            requirements=requirements,
            capability_keys=capability_keys,
            missing_critical_information=requirements.missing_information,
            clarification_questions=[],
            analysis_status="NEEDS_CLARIFICATION" if requirements.missing_information else "READY",
        )

    async def _scope(self, session: AsyncSession | None, principal: Principal | None, account_id: uuid.UUID | None, environment_id: uuid.UUID | None) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        if account_id is None and environment_id is None:
            return None, None
        if session is None or principal is None:
            raise PermissionDeniedError("An authorized BTP context is required for account-specific architecture analysis.")
        coverage = await coverage_for(session, principal, Permission.BTP_READ)
        if environment_id is not None:
            if not coverage.covers_environment(environment_id):
                raise PermissionDeniedError("You do not have access to that BTP environment.", code="btp_scope_revoked")
            environment = await session.get(BTPEnvironment, environment_id)
            if environment is None:
                raise NotFoundError("BTP environment not found.")
            return environment.btp_account_id, environment.id
        if not coverage.covers_account(account_id):
            raise PermissionDeniedError("You do not have access to that BTP account.", code="btp_scope_revoked")
        if await session.get(BTPAccount, account_id) is None:
            raise NotFoundError("BTP account not found.")
        return account_id, None

    def _service_requirements(self, requirements: ArchitectureRequirements) -> ServiceRequirements:
        capabilities = _dedupe([*requirements.database_requirements, *requirements.integration_requirements])
        capabilities = [
            "database" if "database" in item or "persistence" in item or "relational" in item else
            "integration" if "integration" in item or "s/4" in item.lower() or "api" in item.lower() else item
            for item in capabilities
        ]
        if requirements.authentication_requirements or requirements.authorization_requirements:
            capabilities.append("identity-security")
        if any("event" in item.lower() or "asynchronous" in item.lower() for item in requirements.integration_requirements):
            capabilities.append("messaging-events")
        if not capabilities:
            capabilities.append("application-development")
        if requirements.existing_sap_systems:
            capabilities.append("integration")
        return ServiceRequirements(
            business_objective=requirements.business_objective,
            application_type=requirements.application_type,
            required_capabilities=_dedupe(capabilities),
            existing_sap_applications=requirements.existing_sap_systems,
            s4hana_integration_requirements="; ".join(requirements.integration_requirements) if requirements.existing_sap_systems else None,
            runtime_preference=requirements.runtime_preference,
            database_requirements=requirements.database_requirements,
            identity_authentication_needs=requirements.authentication_requirements,
            security_compliance_constraints=requirements.compliance_constraints,
            region=requirements.preferred_region,
            expected_workload=requirements.expected_traffic,
            budget_constraints=requirements.budget_constraints,
            existing_services=requirements.existing_services,
            unknown_requirements=requirements.missing_information,
            explicit_assumptions=[item.value for item in requirements.assumptions],
        )

    async def generate(self, request: ArchitectureGenerationRequest, *, session: AsyncSession | None = None, principal: Principal | None = None, proposal_id: uuid.UUID | None = None, revision: int = 1, scope_override: tuple[uuid.UUID | None, uuid.UUID | None] | None = None) -> ArchitectureProposal:
        analysis = self.analyze_typed(request.requirements) if request.requirements is not None else self.analyze(request.request_text or "")
        account_id, environment_id = scope_override or await self._scope(session, principal, request.account_id, request.environment_id)
        service_request = RecommendationRequest(
            requirements=self._service_requirements(analysis.requirements),
            include_live_evidence=request.include_live_evidence,
            environment_id=environment_id,
        )
        service_response = await self.recommendation_service.recommend(service_request, session=session if request.include_live_evidence else None, principal=principal if request.include_live_evidence else None)
        selected = self._select_services(analysis.requirements, service_response.recommendations)
        proposal_id = proposal_id or uuid.uuid4()
        generated_at = datetime.now(UTC)
        runtime_options = self._runtime_options(analysis.requirements)
        environments = self._environments(analysis.requirements, runtime_options[0].runtime_id, environment_id)
        components = self._components(analysis.requirements, selected, environments)
        connections, flows = self._connections(analysis.requirements, components)
        evidence = self._evidence(selected, service_response)
        security = self._security(analysis.requirements, components, evidence)
        risks = self._risks(analysis.requirements, service_response)
        assumptions = self._assumptions(analysis.requirements)
        alternatives = self._alternatives(analysis.requirements, runtime_options, selected, evidence)
        cost = self._cost_considerations(analysis.requirements, selected)
        diagram = self._diagram(components, connections)
        provisional = ArchitectureProposal(
            proposal_id=proposal_id, revision=revision,
            title=f"SAP BTP architecture for {analysis.requirements.project_name or analysis.requirements.application_type or 'business application'}",
            overview=self._overview(analysis.requirements, selected, runtime_options), requirements=analysis.requirements,
            runtime_options=runtime_options, components=components, connections=connections, data_flows=flows,
            environments=environments, security_controls=security, risks=risks, assumptions=assumptions,
            alternatives=alternatives, cost_considerations=cost, evidence=evidence, diagram=diagram,
            validation=ArchitectureValidation(valid=True, findings=[], validated_at=generated_at), generated_at=generated_at,
            account_id=account_id, environment_id=environment_id,
        )
        validation = self.validate(provisional)
        return provisional.model_copy(update={"validation": validation})

    def _select_services(self, requirements: ArchitectureRequirements, recommendations: list) -> list:
        wanted: list[str] = []
        runtime = requirements.runtime_preference
        wanted.append("kyma" if runtime == "kyma" else "abap" if runtime == "abap" else "cloudfoundry")
        caps = " ".join(requirements.integration_requirements).lower()
        if requirements.database_requirements:
            wanted.append("hana-cloud")
        if requirements.existing_sap_systems or requirements.integration_requirements:
            wanted.append("integration-suite" if "api" not in caps or "complex" in caps or requirements.existing_sap_systems else "destination")
        if requirements.authentication_requirements or requirements.authorization_requirements:
            wanted.append("xsuaa")
        if any("event" in item.lower() or "asynchronous" in item.lower() for item in requirements.integration_requirements):
            wanted.append("event-mesh")
        if any("workflow" in item.lower() or "approval" in item.lower() for item in requirements.integration_requirements):
            wanted.append("build-process-automation")
        if requirements.application_type:
            wanted.append("cloud-logging")
        by_identifier = {item.verified_identifier: item for item in recommendations if item.verified_identifier}
        selected = [by_identifier[key] for key in _dedupe(wanted) if key in by_identifier]
        return selected[:12]

    def _runtime_options(self, requirements: ArchitectureRequirements) -> list[ArchitectureRuntimeOption]:
        selected = "kyma" if requirements.runtime_preference == "kyma" else "abap" if requirements.runtime_preference == "abap" else "cloudfoundry"
        options = [
            ArchitectureRuntimeOption(runtime_id="cloudfoundry", name="Cloud Foundry runtime", suitability="SELECTED" if selected == "cloudfoundry" else "ALTERNATIVE", rationale="Documented fit for managed business applications and the default preliminary option when no runtime is specified.", prerequisites=["Choose a supported region and verify entitlement and quota."], operational_considerations=["Use application health checks, logs and bounded capacity review."], restrictions=["Do not infer region, quota or entitlement."], evidence_references=[_DOCS["environments"]], verification_status="DOCUMENTED"),
            ArchitectureRuntimeOption(runtime_id="kyma", name="Kyma runtime", suitability="SELECTED" if selected == "kyma" else "ALTERNATIVE", rationale="Suitable when Kubernetes-native packaging, container control or Kyma-specific capabilities are explicit.", prerequisites=["Confirm Kubernetes operational ownership and supported region."], operational_considerations=["Plan cluster workload, observability and upgrade responsibilities."], restrictions=["Not interchangeable with Cloud Foundry application lifecycle assumptions."], evidence_references=[_DOCS["environments"]], verification_status="DOCUMENTED"),
            ArchitectureRuntimeOption(runtime_id="abap", name="SAP BTP, ABAP environment", suitability="SELECTED" if selected == "abap" else "NOT_SUITABLE", rationale="Relevant for ABAP cloud extensions and ABAP-based workloads; it is not a generic substitute for every application runtime.", prerequisites=["Confirm ABAP development model and target SAP landscape."], operational_considerations=["Use ABAP-specific lifecycle and transport practices."], restrictions=["Do not select it for a non-ABAP workload without explicit requirements."], evidence_references=["https://help.sap.com/docs/btp/sap-btp-abap-environment"], verification_status="DOCUMENTED"),
        ]
        return options

    def _environments(self, requirements: ArchitectureRequirements, runtime_id: str, verified_environment_id: uuid.UUID | None) -> list[ArchitectureEnvironment]:
        status = "VERIFIED" if verified_environment_id else "PROPOSED"
        suffix = " using the authorized environment context" if verified_environment_id else " as a proposed target structure"
        return [ArchitectureEnvironment(environment_id=stage.lower(), name=stage.title(), stage=stage, runtime_id=runtime_id, purpose=f"{stage.title()} lifecycle boundary{suffix}.", account_structure="Separate subaccount or equivalent governed boundary; shared services only where justified.", status=status, evidence_references=[_DOCS["account-model"], _DOCS["environments"]]) for stage in ("DEVELOPMENT", "TEST", "PRODUCTION")]

    def _components(self, requirements: ArchitectureRequirements, selected: list, environments: list[ArchitectureEnvironment]) -> list[ArchitectureComponent]:
        components = [
            ArchitectureComponent(component_id="users", name="Users", component_type="ACTOR", purpose="Human users of the business application.", verification_status="PROPOSED"),
            ArchitectureComponent(component_id="application-frontend", name="Application frontend", component_type="APPLICATION", purpose="User-facing application experience.", environment_id="production", dependencies=["application-backend"], verification_status="PROPOSED"),
            ArchitectureComponent(component_id="application-backend", name="Application backend", component_type="APPLICATION", purpose="Business logic, authorization enforcement and orchestration.", environment_id="production", verification_status="PROPOSED"),
        ]
        for item in selected:
            entry = _catalog_entry(item.verified_identifier or "")
            if entry is None:
                continue
            component_type = "DATA_STORE" if entry.capability_category == "database-persistence" else "SERVICE"
            components.append(ArchitectureComponent(component_id=entry.service_key, name=entry.service_name, component_type=component_type, purpose=item.suitability_explanation, related_service_key=entry.service_key, environment_id="production", dependencies=["application-backend"], evidence_references=[str(entry.official_documentation_url)], verification_status="DOCUMENTED"))
        if requirements.existing_sap_systems:
            components.append(ArchitectureComponent(component_id="sap-s4hana", name="SAP S/4HANA", component_type="SAP_SYSTEM", purpose="Existing SAP system of record named in the requirements.", verification_status="USER_PROVIDED"))
        for index, external in enumerate(requirements.external_systems[:5], start=1):
            components.append(ArchitectureComponent(component_id=f"external-system-{index}", name=_safe_label(external), component_type="EXTERNAL_SYSTEM", purpose="External system named in the requirements; interface details are not verified.", verification_status="USER_PROVIDED"))
        return components

    def _connections(self, requirements: ArchitectureRequirements, components: list[ArchitectureComponent]) -> tuple[list[ArchitectureConnection], list[ArchitectureDataFlow]]:
        ids = {item.component_id for item in components}
        connections = [ArchitectureConnection(connection_id="users-to-frontend", source_component_id="users", target_component_id="application-frontend", purpose="User access", communication_pattern="HTTPS_API", security_consideration="Use authenticated TLS-protected access and enforce session policy.", verification_status="PROPOSED"), ArchitectureConnection(connection_id="frontend-to-backend", source_component_id="application-frontend", target_component_id="application-backend", purpose="Application requests", communication_pattern="HTTPS_API", security_consideration="Validate tokens and enforce authorization at the backend.", verification_status="PROPOSED")]
        flows = [ArchitectureDataFlow(flow_id="business-request-flow", source_component_id="application-frontend", target_component_id="application-backend", data_description="Business application requests and responses", direction="REQUEST_RESPONSE", consistency="SYNCHRONOUS", data_protection="Apply TLS, authorization and data minimization.")]
        for key in ("xsuaa", "hana-cloud", "integration-suite", "destination-service", "connectivity-service", "event-mesh", "build-process-automation", "cloud-logging"):
            if key not in ids:
                continue
            pattern = "DESTINATION_BASED" if key in {"destination-service", "integration-suite"} else "CLOUD_CONNECTOR" if key == "connectivity-service" else "EVENT" if key == "event-mesh" else "WORKFLOW" if key == "build-process-automation" else "INTERNAL"
            purpose = {"xsuaa": "Authentication and authorization", "hana-cloud": "Transactional persistence", "integration-suite": "Mediated SAP and external integration", "destination-service": "Externalized target connection properties", "connectivity-service": "On-premise connectivity path", "event-mesh": "Asynchronous event delivery", "build-process-automation": "Business approvals and workflow", "cloud-logging": "Operational logs and observability"}[key]
            connections.append(ArchitectureConnection(connection_id=f"backend-to-{key}", source_component_id="application-backend", target_component_id=key, purpose=purpose, communication_pattern=pattern, security_consideration="Use least-privilege service bindings or documented service authentication; credentials are not included.", verification_status="PROPOSED"))
        if "sap-s4hana" in ids:
            target = "integration-suite" if "integration-suite" in ids else "application-backend"
            connections.append(ArchitectureConnection(connection_id="integration-to-s4hana", source_component_id=target, target_component_id="sap-s4hana", purpose="SAP S/4HANA business integration", communication_pattern="CLOUD_CONNECTOR" if any("on-premise" in item.lower() for item in requirements.integration_requirements) else "HTTPS_API", security_consideration="Use documented SAP APIs, destination-based credentials and Cloud Connector where applicable; exact interface setup is not verified.", verification_status="PROPOSED"))
            flows.append(ArchitectureDataFlow(flow_id="s4hana-business-data", source_component_id="sap-s4hana", target_component_id=target, data_description="Business data or events required by the use case", direction="REQUEST_RESPONSE", consistency="SYNCHRONOUS", data_protection="Classify fields, minimize data and apply authorization at both system boundaries."))
        for index, component in enumerate((item for item in components if item.component_type == "EXTERNAL_SYSTEM"), start=1):
            connections.append(ArchitectureConnection(connection_id=f"backend-to-external-{index}", source_component_id="application-backend", target_component_id=component.component_id, purpose="External system integration", communication_pattern="HTTPS_API", security_consideration="Use an approved destination and least-privilege credentials; external API contract is unverified.", verification_status="PROPOSED"))
        return connections, flows

    def _evidence(self, selected: list, service_response) -> list[ArchitectureEvidence]:
        evidence: list[ArchitectureEvidence] = [ArchitectureEvidence(evidence_id="sap-guidance-framework", title="SAP BTP Guidance Framework", url=_DOCS["guidance"], evidence_type="OFFICIAL_DOCUMENTATION", verification_status="VERIFIED", supports=["architecture guidance"]), ArchitectureEvidence(evidence_id="sap-account-model", title="SAP BTP account model", url=_DOCS["account-model"], evidence_type="OFFICIAL_DOCUMENTATION", verification_status="VERIFIED", supports=["account and environment structure"]), ArchitectureEvidence(evidence_id="sap-environments", title="SAP BTP environments", url=_DOCS["environments"], evidence_type="OFFICIAL_DOCUMENTATION", verification_status="VERIFIED", supports=["runtime options", "environment structure"]), ArchitectureEvidence(evidence_id="sap-integration-guide", title="SAP integration architecture guide", url=_DOCS["integration-guide"], evidence_type="OFFICIAL_DOCUMENTATION", verification_status="VERIFIED", supports=["integration patterns"])]
        for item in selected:
            entry = _catalog_entry(item.verified_identifier or "")
            if entry is not None:
                key = entry.service_key
                evidence.append(ArchitectureEvidence(evidence_id=f"service-{key}", title=entry.service_name, url=entry.official_documentation_url, evidence_type="OFFICIAL_DOCUMENTATION", verification_status="VERIFIED", supports=[entry.capability_category]))
        if service_response.live_catalog.retrieval_status in {"AVAILABLE", "PARTIAL"}:
            evidence.append(ArchitectureEvidence(evidence_id="live-catalog", title="Authorized Cloud Foundry provider catalog", url="https://v3-apidocs.cloudfoundry.org/version/3.150.0/index.html", evidence_type="LIVE_PROVIDER", verification_status="PARTIAL" if service_response.partial_evidence else "VERIFIED", observed_at=service_response.live_catalog.observed_at, supports=["provider visibility only; entitlement not proven"]))
        return evidence[:30]

    def _security(self, requirements: ArchitectureRequirements, components: list[ArchitectureComponent], evidence: list[ArchitectureEvidence]) -> list[ArchitectureSecurityControl]:
        backend = ["application-backend"]
        source = next((item.url for item in evidence if item.evidence_id == "service-xsuaa"), "https://help.sap.com/docs/btp/sap-btp-security")
        controls = [
            ArchitectureSecurityControl(control_id="identity-authentication", control="User authentication", applies_to_component_ids=backend, recommendation="Use a documented BTP identity pattern and validate tokens at the application boundary.", evidence_references=[str(source)]),
            ArchitectureSecurityControl(control_id="role-authorization", control="Role-based authorization", applies_to_component_ids=backend, recommendation="Define roles and scopes for least privilege; do not rely on frontend checks alone."),
            ArchitectureSecurityControl(control_id="service-authentication", control="Service-to-service authentication", applies_to_component_ids=backend, recommendation="Use service bindings or documented destination authentication. Keep secrets out of proposal text."),
            ArchitectureSecurityControl(control_id="secret-management", control="Secret management", applies_to_component_ids=backend, recommendation="Store credentials in approved service bindings or secret-management mechanisms; never embed them in code or architecture documents."),
            ArchitectureSecurityControl(control_id="encryption-and-data-protection", control="Encryption and data protection", applies_to_component_ids=backend, recommendation="Use TLS in transit, provider encryption at rest where documented, data minimization and retention review."),
            ArchitectureSecurityControl(control_id="audit-logging", control="Audit logging", applies_to_component_ids=backend, recommendation="Record security-relevant actions and protect audit access with least privilege."),
            ArchitectureSecurityControl(control_id="tenant-isolation", control="Tenant isolation", applies_to_component_ids=backend, recommendation="Define tenant boundaries and authorization context before supporting multi-tenant data."),
        ]
        if requirements.data_sensitivity or requirements.compliance_constraints:
            controls.append(ArchitectureSecurityControl(control_id="privacy-compliance-review", control="Privacy and compliance review", applies_to_component_ids=backend, recommendation="Confirm data classification, residency, retention and regulatory controls with the security and privacy owners."))
        return controls

    def _risks(self, requirements: ArchitectureRequirements, service_response) -> list[ArchitectureRisk]:
        risks = [ArchitectureRisk(risk_id="region-availability", risk="Region and account availability are not verified.", severity="HIGH", mitigation="Verify region, service availability, entitlement and quota before implementation.", evidence_references=[_DOCS["environments"]]), ArchitectureRisk(risk_id="workload-unknown", risk="Peak traffic, payload size and integration volume are incomplete.", severity="MEDIUM", mitigation="Collect workload evidence before sizing or committing to capacity objectives."), ArchitectureRisk(risk_id="interface-contract", risk="SAP and external interface contracts were not inspected.", severity="HIGH" if requirements.existing_sap_systems else "MEDIUM", mitigation="Confirm supported APIs, events, authentication and error semantics with system owners."), ArchitectureRisk(risk_id="recovery-objectives", risk="Availability and recovery objectives are incomplete.", severity="MEDIUM", mitigation="Define RTO, RPO, backup, restore testing and operational ownership."), ArchitectureRisk(risk_id="entitlement", risk="Catalog documentation or provider visibility does not prove entitlement.", severity="HIGH", mitigation="Perform an authorized account entitlement review before provisioning." if service_response.recommendations else "Verify entitlement before provisioning.")]
        return risks

    def _assumptions(self, requirements: ArchitectureRequirements) -> list[ArchitectureAssumption]:
        return [ArchitectureAssumption(assumption_id=f"assumption-{index}", statement=item.value, impact="HIGH" if item.classification == "UNKNOWN" else "MEDIUM", requires_confirmation=item.classification != "EXPLICIT_REQUIREMENT") for index, item in enumerate([*requirements.assumptions, *requirements.unknown_requirements][:20], start=1)]

    def _alternatives(self, requirements: ArchitectureRequirements, runtime_options: list[ArchitectureRuntimeOption], selected: list, evidence: list[ArchitectureEvidence]) -> list[ArchitectureAlternative]:
        alternatives = []
        if requirements.runtime_preference == "unknown":
            alternatives.append(ArchitectureAlternative(alternative_id="alternative-kyma", name="Kyma-based application runtime", change_from_primary="Use Kyma instead of Cloud Foundry for the application runtime.", benefits=["Kubernetes-native packaging and workload model"], drawbacks=["Higher platform and workload operational complexity"], prerequisites=["Explicit Kubernetes requirement and operational ownership"], reliability_security_implications=["Reassess failure isolation, observability and cluster access controls"], evidence_references=[_DOCS["environments"]]))
        if requirements.existing_sap_systems:
            alternatives.append(ArchitectureAlternative(alternative_id="alternative-direct-api", name="Direct documented API integration", change_from_primary="Use a narrowly scoped direct API and destination path instead of broader mediated integration.", benefits=["Fewer integration components for a simple flow"], drawbacks=["Less mediation, transformation and centralized policy capability"], prerequisites=["Stable supported API and clear authentication contract"], reliability_security_implications=["Application owns retry, timeout and error handling"], evidence_references=[_DOCS["integration-guide"]]))
        if any("event" in item.lower() for item in requirements.integration_requirements):
            alternatives.append(ArchitectureAlternative(alternative_id="alternative-synchronous", name="Synchronous API integration", change_from_primary="Use request-response integration for flows that need immediate completion.", benefits=["Immediate response semantics"], drawbacks=["Tighter availability coupling and less buffering during outages"], prerequisites=["Idempotent API and timeout/retry design"], reliability_security_implications=["Define backoff and duplicate-request handling"], evidence_references=[_DOCS["integration-guide"]]))
        return alternatives[:8]

    def _cost_considerations(self, requirements: ArchitectureRequirements, selected: list) -> list[str]:
        services = ", ".join(item.service_name for item in selected) or "the selected runtime and services"
        return [f"Potential recurring commercial drivers include {services}; no price or plan was inferred.", "Runtime consumption, database storage and backup, integration message/API volume, event volume, workflow usage and log ingestion/retention may affect cost.", "Region, commercial model, entitlement and negotiated terms must be verified from an approved SAP commercial source.", "Missing cost inputs include peak workload, storage growth, integration volume, retention and required environments."]

    def _overview(self, requirements: ArchitectureRequirements, selected: list, runtime_options: list[ArchitectureRuntimeOption]) -> str:
        services = ", ".join(item.service_name for item in selected[:8]) or "the documented runtime and supporting services"
        return f"Preliminary, reviewable SAP BTP target architecture for {requirements.application_type or 'the requested application'} using {runtime_options[0].name}. Selected service families: {services}. This is a proposal based on explicit requirements and documented guidance; account facts, entitlement, region, quota and pricing remain unverified unless shown as evidence."

    def _diagram(self, components: list[ArchitectureComponent], connections: list[ArchitectureConnection]) -> ArchitectureDiagram:
        lines = ["flowchart LR"]
        for item in components:
            lines.append(f"    {item.component_id}[\"{_safe_label(item.name)}\"]")
        for connection in connections:
            lines.append(f"    {connection.source_component_id} -->|{_safe_label(connection.purpose)}| {connection.target_component_id}")
        return ArchitectureDiagram(mermaid="\n".join(lines))

    def validate(self, proposal: ArchitectureProposal) -> ArchitectureValidation:
        findings: list[ArchitectureValidationFinding] = []
        component_ids = {item.component_id for item in proposal.components}
        for component in proposal.components:
            if component.related_service_key and component.related_service_key not in CATALOG_BY_KEY:
                findings.append(ArchitectureValidationFinding(rule_id="service_identity_known", severity="ERROR", message="Component references a service absent from the verified catalog.", related_ids=[component.component_id]))
        for connection in proposal.connections:
            if connection.source_component_id not in component_ids or connection.target_component_id not in component_ids:
                findings.append(ArchitectureValidationFinding(rule_id="connection_references", severity="ERROR", message="Connection references a component that does not exist.", related_ids=[connection.connection_id]))
        for flow in proposal.data_flows:
            if flow.source_component_id not in component_ids or flow.target_component_id not in component_ids:
                findings.append(ArchitectureValidationFinding(rule_id="data_flow_references", severity="ERROR", message="Data flow references a component that does not exist.", related_ids=[flow.flow_id]))
        if proposal.requirements.authentication_requirements and not any(item.control_id == "identity-authentication" for item in proposal.security_controls):
            findings.append(ArchitectureValidationFinding(rule_id="authentication_control", severity="ERROR", message="Authentication requirements have no corresponding security control."))
        if proposal.requirements.existing_sap_systems and not any(item.component_id == "sap-s4hana" for item in proposal.components):
            findings.append(ArchitectureValidationFinding(rule_id="sap_system_component", severity="ERROR", message="Named SAP system is not represented as a component."))
        if any(re.search(r"(?:[$€]\s*\d|\b\d[\d,.]*\s*(?:usd|eur|dollars?|euros?)\b|\b(?:price|cost)\s*[:=]\s*\d)", item, re.IGNORECASE) for item in proposal.cost_considerations):
            findings.append(ArchitectureValidationFinding(rule_id="no_invented_pricing", severity="ERROR", message="Architecture cost considerations must not contain invented price figures."))
        findings.append(ArchitectureValidationFinding(rule_id="account_facts_separate", severity="INFO", message="Account, region, entitlement and quota facts remain separate from documented suitability."))
        return ArchitectureValidation(valid=not any(item.severity == "ERROR" for item in findings), findings=findings, validated_at=datetime.now(UTC))

    def compare(self, request: ArchitectureComparisonRequest) -> ArchitectureComparisonResponse:
        alternatives = request.proposal.alternatives
        if request.alternative_ids:
            alternatives = [item for item in alternatives if item.alternative_id in request.alternative_ids]
        return ArchitectureComparisonResponse(primary_proposal_id=request.proposal.proposal_id, compared_alternatives=alternatives, criteria=["capability fit", "operational complexity", "reliability", "security implications", "prerequisites", "missing evidence"], missing_information=request.proposal.requirements.missing_information, generated_at=datetime.now(UTC))

    async def create(self, session: AsyncSession, principal: Principal, request: ArchitectureGenerationRequest) -> ArchitectureProposal:
        proposal = await self.generate(request, session=session, principal=principal)
        session.add(ArchitectureProposalRecord(proposal_id=proposal.proposal_id, revision=proposal.revision, owner_user_id=principal.user_id, btp_account_id=proposal.account_id, btp_environment_id=proposal.environment_id, title=proposal.title, proposal_json=proposal.model_dump(mode="json"), requirements_json=proposal.requirements.model_dump(mode="json")))
        await session.commit()
        return proposal

    async def _record(self, session: AsyncSession, principal: Principal, proposal_id: uuid.UUID) -> ArchitectureProposalRecord:
        record = await session.scalar(select(ArchitectureProposalRecord).where(ArchitectureProposalRecord.proposal_id == proposal_id, ArchitectureProposalRecord.owner_user_id == principal.user_id).order_by(ArchitectureProposalRecord.revision.desc()))
        if record is None:
            raise NotFoundError("Architecture proposal not found.")
        await self._scope(session, principal, record.btp_account_id, record.btp_environment_id)
        return record

    async def get(self, session: AsyncSession, principal: Principal, proposal_id: uuid.UUID) -> ArchitectureProposal:
        record = await self._record(session, principal, proposal_id)
        return ArchitectureProposal.model_validate(record.proposal_json)

    async def revise(self, session: AsyncSession, principal: Principal, proposal_id: uuid.UUID, request: ArchitectureRevisionRequest) -> ArchitectureProposal:
        record = await self._record(session, principal, proposal_id)
        generation = ArchitectureGenerationRequest(request_text=request.request_text, requirements=request.requirements, include_live_evidence=request.include_live_evidence, account_id=record.btp_account_id, environment_id=record.btp_environment_id)
        proposal = await self.generate(generation, session=session, principal=principal, proposal_id=proposal_id, revision=record.revision + 1, scope_override=(record.btp_account_id, record.btp_environment_id))
        session.add(ArchitectureProposalRecord(proposal_id=proposal.proposal_id, revision=proposal.revision, owner_user_id=principal.user_id, btp_account_id=proposal.account_id, btp_environment_id=proposal.environment_id, title=proposal.title, proposal_json=proposal.model_dump(mode="json"), requirements_json=proposal.requirements.model_dump(mode="json")))
        await session.commit()
        return proposal

    async def graph(self, session: AsyncSession, principal: Principal, proposal_id: uuid.UUID) -> ArchitectureGraphResponse:
        proposal = await self.get(session, principal, proposal_id)
        return ArchitectureGraphResponse(proposal_id=proposal.proposal_id, revision=proposal.revision, diagram=proposal.diagram, components=proposal.components, connections=proposal.connections)

    async def list(self, session: AsyncSession, principal: Principal, limit: int = 20, offset: int = 0) -> tuple[list[ArchitectureProposal], bool]:
        rows = list(await session.scalars(select(ArchitectureProposalRecord).where(ArchitectureProposalRecord.owner_user_id == principal.user_id).order_by(ArchitectureProposalRecord.updated_at.desc()).offset(offset).limit(limit + 1)))
        proposals = [ArchitectureProposal.model_validate(item.proposal_json) for item in rows[:limit]]
        return proposals, len(rows) > limit
