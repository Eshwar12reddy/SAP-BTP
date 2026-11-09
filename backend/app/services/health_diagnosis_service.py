"""Bounded health and quota analysis built on the Phase 13/19 services."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Principal
from app.core.config import Settings
from app.core.exceptions import AppError
from app.integrations.sap_btp.cloud_foundry import CFQuota, CFQuotaValue, CloudFoundryProviderError
from app.schemas.diagnosis import (
    DiagnosisFinding,
    EvidenceReference,
    HealthOverviewRead,
    ProviderFailure,
    QuotaDiagnosisRead,
    UnusedResourceCandidate,
    UnusedResourcePage,
)
from app.services.cloud_foundry_service import AuthorizedCloudFoundryService, provider_error
from app.services.diagnosis_service import CloudFoundryDiagnosisService


def _quota_state(value: CFQuotaValue) -> str:
    if value.state == "value" and value.value == 0:
        return "ZERO"
    if value.state == "value":
        return "FINITE"
    if value.state == "unlimited":
        return "UNLIMITED"
    return "UNKNOWN"


def _effective_quota_value(first: CFQuotaValue, second: CFQuotaValue) -> CFQuotaValue:
    """Return the restrictive applicable limit without treating unknown as zero."""
    first_state = _quota_state(first)
    second_state = _quota_state(second)
    if "ZERO" in {first_state, second_state}:
        return CFQuotaValue(0, "value")
    if "UNKNOWN" in {first_state, second_state}:
        return CFQuotaValue(None, "unknown")
    if first_state == "UNLIMITED":
        return second
    if second_state == "UNLIMITED":
        return first
    return CFQuotaValue(min(first.value or 0, second.value or 0), "value")


def _quota_finding(
    *,
    quota: CFQuota,
    metric: str,
    value: CFQuotaValue,
    usage: int | None,
    threshold_warning: float,
    threshold_critical: float,
    timestamp: datetime,
    environment_id,
    evidence_ref: str,
) -> DiagnosisFinding | None:
    state = _quota_state(value)
    if state == "UNKNOWN":
        return DiagnosisFinding(
            finding_id=f"quota_unknown:{quota.id}:{metric}",
            rule_id="quota_unknown",
            resource_id=quota.id,
            resource_type="quota",
            environment_id=environment_id,
            severity="LOW",
            confidence="CONFIRMED",
            observation=f"The {metric} quota is unknown or missing; quota pressure was not calculated.",
            evidence_references=[evidence_ref],
            timestamp=timestamp,
            source_type="LIVE_PROVIDER",
            possible_causes=["The provider omitted the quota value or returned an unsupported semantic."],
            recommended_next_steps=["Retrieve the quota again through an authorized provider read."],
            missing_evidence=[f"Comparable {metric} quota and usage values."],
            data_freshness="observed_now",
            diagnostic_status="PARTIAL",
        )
    if state == "UNLIMITED":
        return DiagnosisFinding(
            finding_id=f"quota_unlimited:{quota.id}:{metric}",
            rule_id="quota_unlimited",
            resource_id=quota.id,
            resource_type="quota",
            environment_id=environment_id,
            severity="INFO",
            confidence="CONFIRMED",
            observation=f"The {metric} quota is provider-reported as unlimited; finite pressure was not calculated.",
            evidence_references=[evidence_ref],
            timestamp=timestamp,
            source_type="LIVE_PROVIDER",
            possible_causes=[],
            recommended_next_steps=["Continue monitoring provider-reported usage if capacity governance requires it."],
            calculation_details={"quota_state": "UNLIMITED"},
            data_freshness="observed_now",
            diagnostic_status="OBSERVED",
        )
    if value.value is None or usage is None:
        return DiagnosisFinding(
            finding_id=f"quota_usage_unknown:{quota.id}:{metric}",
            rule_id="quota_usage_unknown",
            resource_id=quota.id,
            resource_type="quota",
            environment_id=environment_id,
            severity="LOW",
            confidence="UNKNOWN",
            observation=f"The {metric} quota is finite, but comparable usage was not available; no percentage was inferred.",
            evidence_references=[evidence_ref],
            timestamp=timestamp,
            source_type="LIVE_PROVIDER",
            possible_causes=["The provider usage summary may be partial or unavailable."],
            recommended_next_steps=["Retrieve a supported usage summary before assessing remaining capacity."],
            missing_evidence=[f"Comparable {metric} usage."],
            data_freshness="observed_now",
            diagnostic_status="PARTIAL",
        )
    if value.value == 0:
        percent = 100.0 if usage > 0 else 0.0
    else:
        percent = usage / value.value * 100
    severity = "CRITICAL" if percent >= threshold_critical else "HIGH" if percent >= threshold_warning else "INFO"
    return DiagnosisFinding(
        finding_id=f"quota_pressure:{quota.id}:{metric}",
        rule_id="quota_pressure",
        resource_id=quota.id,
        resource_type="quota",
        environment_id=environment_id,
        severity=severity,
        confidence="CONFIRMED",
        observation=(
            f"Comparable {metric} usage is {usage} of finite quota {value.value} "
            f"({percent:.1f}%). Remaining capacity is {max(value.value - usage, 0)}."
        ),
        evidence_references=[evidence_ref],
        timestamp=timestamp,
        source_type="LIVE_PROVIDER",
        possible_causes=["The applicable quota may constrain future application or service growth."],
        recommended_next_steps=["Review workload demand and quota governance before requesting a change."],
        calculation_details={
            "usage": usage,
            "finite_quota": value.value,
            "utilization_percent": round(percent, 2),
            "remaining_capacity": value.value - usage,
            "derived_from": "compatible_provider_reported_units",
        },
        data_freshness="observed_now",
        diagnostic_status="OBSERVED",
    )


class HealthDiagnosisService:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self.inventory_service = AuthorizedCloudFoundryService(settings, transport=transport)
        self.application_service = CloudFoundryDiagnosisService(settings, transport=transport)

    async def application_health(
        self,
        session: AsyncSession,
        principal: Principal,
        application_id: str,
        *,
        expected_running: bool | None = None,
    ):
        return await self.application_service.diagnose(
            session,
            principal,
            application_id,
            expected_running=expected_running,
        )

    async def overview(self, session: AsyncSession, principal: Principal) -> HealthOverviewRead:
        inventory = await self.inventory_service.inventory(session, principal)
        try:
            applications, _ = await inventory.applications()
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        applications = applications[: self.settings.BTP_DIAGNOSIS_MAX_APPLICATIONS]
        findings: list[DiagnosisFinding] = []
        evidence: list[EvidenceReference] = []
        failures: list[ProviderFailure] = []
        missing: list[str] = []
        for application in applications:
            try:
                diagnosis = await self.application_service.diagnose(session, principal, application.id)
            except (AppError, TimeoutError) as error:
                failures.append(
                    ProviderFailure(
                        source="cloud_foundry_v3",
                        operation=f"application_health:{application.id}",
                        error_code=getattr(error, "code", "diagnosis_failed"),
                        observed_at=datetime.now(UTC),
                    )
                )
                missing.append(f"Health diagnosis for application {application.id} did not complete.")
                continue
            findings.extend(diagnosis.findings)
            evidence.extend(diagnosis.evidence)
            failures.extend(diagnosis.provider_failures)
            missing.extend(diagnosis.missing_information)
        return HealthOverviewRead(
            environment_id=inventory.authorized.context.btp_environment_id,
            generated_at=datetime.now(UTC),
            diagnostic_status="PARTIAL" if failures else "COMPLETE",
            applications_checked=len(applications),
            findings=findings[:200],
            evidence=evidence[:1000],
            provider_failures=failures[:100],
            missing_information=list(dict.fromkeys(missing))[:100],
            evidence_freshness="PARTIAL" if failures else "OBSERVED_NOW",
        )

    async def quota_diagnosis(
        self,
        session: AsyncSession,
        principal: Principal,
        resource_id: str,
        resource_type: str,
    ) -> QuotaDiagnosisRead:
        inventory = await self.inventory_service.inventory(session, principal)
        try:
            quotas, _ = (
                await inventory.organization_quotas()
                if resource_type == "organization"
                else await inventory.space_quotas()
            )
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        matches = [
            quota for quota in quotas
            if quota.id == resource_id
            or resource_id in quota.organization_ids
            or resource_id in quota.space_ids
        ]
        if not matches:
            raise provider_error(CloudFoundryProviderError("provider_not_found"))
        quota = matches[0]
        applicable_parent = None
        if resource_type == "space" and quota.organization_ids:
            try:
                organization_quotas, _ = await inventory.organization_quotas()
                applicable_parent = next(
                    (candidate for candidate in organization_quotas if candidate.id in quota.organization_ids or set(candidate.organization_ids) & set(quota.organization_ids)),
                    None,
                )
            except CloudFoundryProviderError:
                missing_parent = True
            else:
                missing_parent = applicable_parent is None
        else:
            missing_parent = False
        usage = None
        failures: list[ProviderFailure] = []
        missing: list[str] = []
        try:
            usage = (
                await inventory.organization_usage_summary(resource_id)
                if resource_type == "organization"
                else await inventory.space_usage_summary(resource_id)
            )
        except CloudFoundryProviderError as error:
            failures.append(ProviderFailure(source="cloud_foundry_v3", operation="usage_summary", error_code=error.code, observed_at=datetime.now(UTC)))
            missing.append("Comparable provider usage summary was unavailable.")
        now = datetime.now(UTC)
        ref = f"quota:{quota.id}"
        evidence = [EvidenceReference(reference_id=ref, source="cloud_foundry_v3", resource_id=quota.id, observed_at=now, summary=f"{quota.quota_type} quota {quota.name} observed.")]
        findings: list[DiagnosisFinding] = []
        metrics = [
            ("memory", quota.total_memory_mb, usage.memory_in_mb if usage else None),
            ("instances", quota.total_instances, usage.started_instances if usage else None),
            ("service_instances", quota.total_service_instances, None),
            ("routes", quota.total_routes, None),
        ]
        if applicable_parent is not None:
            metrics = [
                (
                    metric,
                    _effective_quota_value(
                        limit,
                        getattr(applicable_parent, {"memory": "total_memory_mb", "instances": "total_instances", "service_instances": "total_service_instances", "routes": "total_routes"}[metric]),
                    ),
                    observed,
                )
                for metric, limit, observed in metrics
            ]
        if missing_parent:
            missing.append("The parent organization quota could not be checked; space capacity may be further restricted.")
        for metric, limit, observed in metrics:
            finding = _quota_finding(
                quota=quota,
                metric=metric,
                value=limit,
                usage=observed,
                threshold_warning=self.settings.BTP_DIAGNOSIS_QUOTA_WARNING_PERCENT,
                threshold_critical=self.settings.BTP_DIAGNOSIS_QUOTA_CRITICAL_PERCENT,
                timestamp=now,
                environment_id=inventory.authorized.context.btp_environment_id,
                evidence_ref=ref,
            )
            if finding:
                findings.append(finding)
        if usage is None:
            missing.append("No usage values were treated as zero.")
        return QuotaDiagnosisRead(
            environment_id=inventory.authorized.context.btp_environment_id,
            generated_at=now,
            resource_id=resource_id,
            resource_type=resource_type,
            quota_name=quota.name,
            quota_type=quota.quota_type,
            diagnostic_status="PARTIAL" if failures or missing else "COMPLETE",
            findings=findings,
            evidence=evidence,
            provider_failures=failures,
            missing_information=list(dict.fromkeys(missing)),
        )

    async def unused_resources(self, session: AsyncSession, principal: Principal) -> UnusedResourcePage:
        inventory = await self.inventory_service.inventory(session, principal)
        try:
            services, _ = await inventory.service_instances()
            applications, _ = await inventory.applications()
        except CloudFoundryProviderError as error:
            raise provider_error(error) from None
        bindings_by_service: set[str] = set()
        missing = ["No historical utilization window is available from a single inventory snapshot."]
        for application in applications[: self.settings.BTP_DIAGNOSIS_MAX_APPLICATIONS]:
            try:
                bindings, _ = await inventory.service_bindings(application.id)
                bindings_by_service.update(binding.service_instance_id for binding in bindings if binding.service_instance_id)
            except CloudFoundryProviderError:
                missing.append(f"Binding visibility for application {application.id} was incomplete.")
        now = datetime.now(UTC)
        items = [
            UnusedResourceCandidate(
                resource_id=service.id,
                resource_type="service_instance",
                display_name=service.name,
                observed_at=now,
                evidence_references=[f"service:{service.id}"],
                limitation="No visible binding was found in the authorized scope; external access or hidden bindings may exist.",
                recommended_check="Verify consumers, shared-space bindings, and service ownership before any lifecycle action.",
            )
            for service in services if service.id not in bindings_by_service
        ]
        return UnusedResourcePage(
            environment_id=inventory.authorized.context.btp_environment_id,
            generated_at=now,
            items=items[:100],
            diagnostic_status="PARTIAL" if len(missing) > 1 else "COMPLETE",
            missing_information=list(dict.fromkeys(missing)),
        )
