"""Deterministic, reproducible SAP BTP resource-sizing calculations.

This module is deliberately independent of DeepSeek.  It accepts typed inputs, applies a
small server-controlled formula registry, and returns every intermediate value needed to
reproduce the result.  It never turns user text into Python code or infrastructure actions.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation, getcontext
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Principal, coverage_for
from app.auth.permissions import Permission
from app.core.exceptions import AppError, BadRequestError, NotFoundError, PermissionDeniedError
from app.models import BTPEnvironment, SizingResultRecord
from app.schemas.sizing import (
    InputProvenance,
    SizingAssumption,
    SizingCalculation,
    SizingCalculationRequest,
    SizingComparisonRequest,
    SizingComparisonResponse,
    SizingFormula,
    SizingIntermediate,
    SizingQuotaImpact,
    SizingRequirements,
    SizingRequirementsAnalysis,
    SizingResult,
    SizingResultPage,
)

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.architecture_advisor_service import ArchitectureAdvisorService

getcontext().prec = 40

FORMULA_VERSION = "phase23-v1"
_ZERO = Decimal(0)
_ONE = Decimal(1)
_HUNDRED = Decimal(100)
_MAX_CALCULATION = Decimal(1000000000000000)
_NUMBER = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
_MEMORY_UNITS = {"BYTES", "MB", "MiB", "GB", "GiB"}
_UNIT_BYTES = {
    "BYTES": Decimal(1),
    "MB": Decimal(1_000_000),
    "MiB": Decimal(1_048_576),
    "GB": Decimal(1_000_000_000),
    "GiB": Decimal(1_073_741_824),
}

FORMULAS: dict[str, SizingFormula] = {
    "cf.configured_memory": SizingFormula(formula_id="cf.configured_memory", formula_version=FORMULA_VERSION, expression="sum(memory_per_instance × instance_count)", description="Configured process memory allocation; not observed consumption."),
    "cf.observed_memory": SizingFormula(formula_id="cf.observed_memory", formula_version=FORMULA_VERSION, expression="measured_memory_per_instance × instance_count", description="Measured memory scenario when a comparable measurement is supplied."),
    "capacity.instances": SizingFormula(formula_id="capacity.instances", formula_version=FORMULA_VERSION, expression="ceil(peak_requests_per_second ÷ sustainable_requests_per_second_per_instance)", description="Throughput-derived base instance requirement."),
    "cf.configured_disk": SizingFormula(formula_id="cf.configured_disk", formula_version=FORMULA_VERSION, expression="sum(disk_per_instance × instance_count)", description="Configured per-process disk allocation; not persistent database storage."),
    "storage.logical": SizingFormula(formula_id="storage.logical", formula_version=FORMULA_VERSION, expression="initial_logical_volume + explicit_growth", description="Logical data volume projected over the selected period."),
    "storage.records": SizingFormula(formula_id="storage.records", formula_version=FORMULA_VERSION, expression="expected_records × average_stored_bytes_per_record", description="Record-based logical growth calculation."),
    "storage.provisioned": SizingFormula(formula_id="storage.provisioned", formula_version=FORMULA_VERSION, expression="logical_storage × index_overhead_factor", description="Provisioned planning scenario when an explicit index factor is provided."),
    "storage.backup": SizingFormula(formula_id="storage.backup", formula_version=FORMULA_VERSION, expression="logical_storage × backup_factor", description="Backup storage planning scenario when an explicit backup factor is provided."),
    "storage.replication": SizingFormula(formula_id="storage.replication", formula_version=FORMULA_VERSION, expression="logical_storage × replication_factor", description="Replicated capacity planning scenario when an explicit replication factor is provided."),
    "quota.impact": SizingFormula(formula_id="quota.impact", formula_version=FORMULA_VERSION, expression="projected_usage = existing_usage + net_additional_allocation", description="Comparable quota impact calculation with unlimited and unknown semantics."),
}


def _decimal(value: Decimal | float | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise BadRequestError("Sizing values must be finite decimal numbers.", code="sizing_invalid_number") from error
    if not parsed.is_finite() or abs(parsed) > _MAX_CALCULATION:
        raise BadRequestError("Sizing values exceed the supported finite range.", code="sizing_value_too_large")
    return parsed


def _ceil(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_CEILING)


def _convert(value: Decimal, source: str, target: str) -> Decimal:
    if source not in _MEMORY_UNITS or target not in _MEMORY_UNITS:
        raise BadRequestError("Unsupported sizing unit.", code="sizing_invalid_unit")
    converted = value * _UNIT_BYTES[source] / _UNIT_BYTES[target]
    if abs(converted) > _MAX_CALCULATION:
        raise BadRequestError("Converted sizing value exceeds the supported range.", code="sizing_value_too_large")
    return converted


def _str(value: Decimal | float | None) -> str | int | float | None:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


def _input(value: Decimal | float | None, unit: str, provenance: InputProvenance = "USER_PROVIDED") -> tuple[str | int | float | None, str, InputProvenance]:
    return _str(value), unit, provenance


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _assumption(statement: str, provenance: InputProvenance = "ASSUMED", assumption_id: str = "assumption-sizing") -> SizingAssumption:
    return SizingAssumption(assumption_id=assumption_id, statement=statement, provenance=provenance, requires_confirmation=provenance != "USER_PROVIDED")


def _calculation(
    *, calculation_id: str, resource_reference: str, calculation_type: str, formula_id: str,
    inputs: dict[str, tuple[Decimal | int | float | None, str, InputProvenance]],
    intermediates: list[SizingIntermediate], final_value: Decimal | None, result_unit: str,
    scenario_id: str, assumptions: list[str] | None = None, margin: Decimal = _ZERO,
    additional_capacity: Decimal | None = None, warnings: list[str] | None = None,
    missing: list[str] | None = None, status: str = "CALCULATED", observed_at: datetime | None = None,
) -> SizingCalculation:
    return SizingCalculation(
        calculation_id=calculation_id, resource_reference=resource_reference, calculation_type=calculation_type,
        input_values={name: _str(value) for name, (value, _, _) in inputs.items()},
        input_units={name: unit for name, (_, unit, _) in inputs.items()},
        input_provenance={name: provenance for name, (_, _, provenance) in inputs.items()},
        formula=FORMULAS[formula_id], intermediate_calculations=intermediates, final_value=final_value,
        result_unit=result_unit, assumptions=assumptions or [], safety_margin_percent=margin,
        additional_capacity=additional_capacity, evidence_references=[], observation_time=observed_at,
        verification_status=status, warnings=warnings or [], missing_inputs=missing or [], scenario_id=scenario_id,
    )


class SizingService:
    def __init__(self, *, settings: Settings | None = None, architecture_service: ArchitectureAdvisorService | None = None, cloud_foundry_service: object | None = None) -> None:
        self.settings = settings
        self.architecture_service = architecture_service
        self.cloud_foundry_service = cloud_foundry_service

    def analyze(self, text: str) -> SizingRequirementsAnalysis:
        cleaned = re.sub(r"\s+", " ", text.strip())
        if not cleaned:
            raise BadRequestError("Sizing requirements must not be blank.", code="sizing_requirements_blank")
        lowered = cleaned.lower()
        instances = re.search(rf"{_NUMBER}\s+instances?", lowered)
        memory = re.search(rf"{_NUMBER}\s*(bytes|mb|mib|gb|gib)\s*(?:each|per\s+instance)", lowered)
        users = re.search(rf"{_NUMBER}\s+(?:users?|employees?|customers?)", lowered)
        peak = re.search(rf"{_NUMBER}\s*(?:rps|requests?\s+per\s+second)", lowered)
        runtime = "cloudfoundry" if "cloud foundry" in lowered or "cloudfoundry" in lowered else "kyma" if "kyma" in lowered else "abap" if "abap" in lowered else "hana-cloud" if "hana" in lowered else "unknown"
        parsed_instances = int(instances.group(1).replace(",", "")) if instances else None
        parsed_memory = Decimal(memory.group(1).replace(",", "")) if memory else None
        parsed_users = int(users.group(1).replace(",", "")) if users else None
        parsed_peak = Decimal(peak.group(1).replace(",", "")) if peak else None
        memory_unit = (memory.group(2).upper() if memory else "MB")
        if memory_unit == "MIB": memory_unit = "MiB"
        if memory_unit == "GIB": memory_unit = "GiB"
        requirements = SizingRequirements(
            runtime_type=runtime, user_count=parsed_users, peak_requests_per_second=parsed_peak,
            configured_memory_per_instance=parsed_memory, configured_memory_unit=memory_unit,
            instance_count=parsed_instances, application_name="parsed application",
            user_assumptions=[_assumption("The sizing request was parsed from user text; unmentioned workload and quota values remain unknown.", "USER_PROVIDED", "assumption-parsed-input")],
        )
        missing: list[str] = []
        if parsed_users is not None and parsed_instances is None and parsed_memory is None:
            missing.extend(["concurrent users", "peak requests per second", "measured sustainable requests per second per instance", "availability and recovery objectives"])
        if parsed_instances is not None and parsed_memory is None:
            missing.append("configured memory per instance")
        if parsed_peak is not None and requirements.sustainable_requests_per_second_per_instance is None:
            missing.append("measured sustainable requests per second per instance")
        if runtime in {"abap", "hana-cloud", "integration"}:
            missing.append(f"official product-specific {runtime} sizing method or benchmark")
        questions = [
            "What is the expected concurrent-user and peak request profile?" if parsed_users is not None and parsed_peak is None else "",
            "What sustainable requests-per-second benchmark was measured per instance?" if parsed_peak is not None and requirements.sustainable_requests_per_second_per_instance is None else "",
            "What availability, RTO and RPO objectives must the design meet?" if parsed_users is not None else "",
        ]
        return SizingRequirementsAnalysis(requirements=requirements, missing_inputs=_dedupe(missing), clarification_questions=_dedupe(questions), explicit_inputs=[cleaned], analysis_status="NEEDS_CLARIFICATION" if missing else "READY")

    def _assess_processes(self, requirements: SizingRequirements):
        if requirements.process_types:
            return requirements.process_types
        if requirements.instance_count is not None or requirements.configured_memory_per_instance is not None or requirements.configured_disk_per_instance is not None:
            return [
                type("Process", (), {
                    "process_type": "web", "instance_count": requirements.instance_count or 0,
                    "memory_per_instance": requirements.configured_memory_per_instance,
                    "memory_unit": requirements.configured_memory_unit,
                    "disk_per_instance": requirements.configured_disk_per_instance,
                    "disk_unit": requirements.configured_disk_unit,
                    "provenance": "USER_PROVIDED", "replace_existing": requirements.resize_existing,
                })()
            ]
        return []

    def _quota_impact(self, *, dimension: str, state: str, limit: Decimal | None, usage: Decimal | None, unit: str, proposed: Decimal | None, observed_at: datetime | None, source: str, resize_existing: bool, existing: Decimal | None) -> SizingQuotaImpact:
        warnings: list[str] = []
        if state == "UNLIMITED":
            return SizingQuotaImpact(dimension=dimension, quota_state="UNLIMITED", quota_limit=None, quota_unit=unit, existing_usage=usage, additional_allocation=proposed, projected_usage=None, remaining_capacity=None, projected_utilization_percent=None, compatible=None, evidence_references=[source] if source != "UNKNOWN" else [], observation_time=observed_at, warnings=["Provider-reported unlimited quota; finite utilization was not calculated."])
        if state == "ZERO":
            return SizingQuotaImpact(dimension=dimension, quota_state="ZERO", quota_limit=_ZERO, quota_unit=unit, existing_usage=usage, additional_allocation=proposed, projected_usage=usage, remaining_capacity=_ZERO, projected_utilization_percent=None, compatible=proposed in {None, _ZERO}, evidence_references=[source] if source != "UNKNOWN" else [], observation_time=observed_at, warnings=["Zero quota provides no positive capacity."])
        if state in {"UNKNOWN"} or limit is None:
            return SizingQuotaImpact(dimension=dimension, quota_state="UNKNOWN", quota_limit=None, quota_unit=unit, existing_usage=usage, additional_allocation=proposed, projected_usage=None, remaining_capacity=None, projected_utilization_percent=None, compatible=None, evidence_references=[source] if source != "UNKNOWN" else [], observation_time=observed_at, warnings=["Comparable quota evidence is unavailable; quota fit is unverified."])
        if usage is None or proposed is None:
            return SizingQuotaImpact(dimension=dimension, quota_state="FINITE", quota_limit=limit, quota_unit=unit, existing_usage=usage, additional_allocation=proposed, projected_usage=None, remaining_capacity=None, projected_utilization_percent=None, compatible=None, evidence_references=[source] if source != "UNKNOWN" else [], observation_time=observed_at, warnings=["Finite quota was supplied without comparable usage or proposed allocation."])
        net = proposed
        if resize_existing and existing is not None:
            net = max(proposed - existing, _ZERO)
            warnings.append("Existing allocation was treated as a replacement; only the positive resize delta was added.")
        projected = usage + net
        remaining = limit - projected
        percent = projected / limit * _HUNDRED if limit else None
        compatible = projected <= limit
        if not compatible:
            warnings.append("Projected usage exceeds the finite quota.")
        return SizingQuotaImpact(dimension=dimension, quota_state="FINITE", quota_limit=limit, quota_unit=unit, existing_usage=usage, additional_allocation=net, projected_usage=projected, remaining_capacity=remaining, projected_utilization_percent=percent, compatible=compatible, evidence_references=[source] if source != "UNKNOWN" else [], observation_time=observed_at, warnings=warnings)

    def calculate_sync(self, requirements: SizingRequirements, *, scenario_id: str = "baseline", proposal_revision_mismatch: str | None = None) -> SizingResult:
        now = datetime.now(UTC)
        assumptions = list(requirements.user_assumptions)
        warnings: list[str] = []
        missing: list[str] = []
        if requirements.safety_margin_percent == _ZERO:
            assumptions.append(_assumption("No safety margin was applied because none was explicitly supplied.", "ASSUMED", "assumption-no-margin"))
        if requirements.retention_period_days is not None:
            assumptions.append(_assumption(f"The requested retention period is {requirements.retention_period_days} days; deletion and archival behavior were not modeled.", "USER_PROVIDED", "assumption-retention"))
        if proposal_revision_mismatch:
            warnings.append(proposal_revision_mismatch)
        if requirements.runtime_type == "abap":
            warnings.append("ABAP environment sizing is unsupported here; use the official product-specific sizing method.")
        if requirements.runtime_type in {"hana-cloud", "integration"}:
            warnings.append("Specialized SAP service sizing is limited to explicit logical inputs; complete performance, memory, plan or commercial sizing is not calculated.")
        calculations: list[SizingCalculation] = []
        processes = self._assess_processes(requirements)
        memory_total = _ZERO
        memory_known = False
        disk_total = _ZERO
        disk_known = False
        instance_total = _ZERO
        instance_known = False
        for process in processes:
            count = Decimal(process.instance_count)
            instance_total += count
            instance_known = True
            if process.memory_per_instance is not None:
                value = _decimal(process.memory_per_instance) or _ZERO
                memory_total += _convert(value, process.memory_unit, "MB") * count
                memory_known = True
            if process.disk_per_instance is not None:
                value = _decimal(process.disk_per_instance) or _ZERO
                disk_total += _convert(value, process.disk_unit, "MB") * count
                disk_known = True
        margin = _decimal(requirements.safety_margin_percent) or _ZERO
        if memory_known:
            additional = memory_total * margin / _HUNDRED
            calculations.append(_calculation(calculation_id="configured-memory", resource_reference="cloud-foundry-processes", calculation_type="CONFIGURED_MEMORY", formula_id="cf.configured_memory", inputs={"memory_total_before_margin": (memory_total, "MB", "DERIVED"), "safety_margin_percent": (margin, "%", "USER_PROVIDED")}, intermediates=[SizingIntermediate(name="base_configured_memory", value=memory_total, unit="MB", provenance="DERIVED"), SizingIntermediate(name="additional_capacity", value=additional, unit="MB", provenance="DERIVED")], final_value=memory_total + additional, result_unit="MB", scenario_id=scenario_id, assumptions=["Configured process memory is not observed memory utilization."], margin=margin, additional_capacity=additional))
        else:
            missing.append("configured memory per instance and instance count")
        if requirements.measured_memory_per_instance is not None and (requirements.instance_count is not None or instance_known):
            count = Decimal(requirements.instance_count if requirements.instance_count is not None else int(instance_total))
            measured = _convert(requirements.measured_memory_per_instance, requirements.measured_memory_unit, "MB") * count
            calculations.append(_calculation(calculation_id="observed-memory", resource_reference="cloud-foundry-processes", calculation_type="OBSERVED_MEMORY", formula_id="cf.observed_memory", inputs={"measured_memory_per_instance": _input(requirements.measured_memory_per_instance, requirements.measured_memory_unit), "instance_count": (count, "INSTANCES", "USER_PROVIDED")}, intermediates=[SizingIntermediate(name="measured_total", value=measured, unit="MB", provenance="DERIVED")], final_value=measured, result_unit="MB", scenario_id=scenario_id, assumptions=["The measurement is assumed comparable to the requested workload."], status="OBSERVED", observed_at=requirements.measurement_timestamp))
        if disk_known:
            additional = disk_total * margin / _HUNDRED
            calculations.append(_calculation(calculation_id="configured-disk", resource_reference="cloud-foundry-processes", calculation_type="CONFIGURED_DISK", formula_id="cf.configured_disk", inputs={"disk_total_before_margin": (disk_total, "MB", "DERIVED"), "safety_margin_percent": (margin, "%", "USER_PROVIDED")}, intermediates=[SizingIntermediate(name="base_configured_disk", value=disk_total, unit="MB", provenance="DERIVED"), SizingIntermediate(name="additional_capacity", value=additional, unit="MB", provenance="DERIVED")], final_value=disk_total + additional, result_unit="MB", scenario_id=scenario_id, assumptions=["Configured ephemeral process disk is not persistent database storage."], margin=margin, additional_capacity=additional))
        elif requirements.runtime_type == "cloudfoundry":
            missing.append("configured disk per instance if process disk planning is required")
        base_instances: Decimal | None = instance_total if instance_known else None
        instance_warnings: list[str] = []
        if requirements.peak_requests_per_second is not None and requirements.sustainable_requests_per_second_per_instance is not None:
            peak = _decimal(requirements.peak_requests_per_second) or _ZERO
            sustainable = _decimal(requirements.sustainable_requests_per_second_per_instance)
            if sustainable is None or sustainable <= _ZERO:
                raise BadRequestError("Sustainable throughput must be greater than zero.", code="sizing_zero_throughput")
            base_instances = max(_ceil(peak / sustainable), Decimal(requirements.minimum_redundancy_instances or 0))
            instance_warnings.append("Throughput-derived instances require a reliable sustainable benchmark per instance.")
        elif requirements.user_count is not None and not instance_known:
            missing.extend(["concurrent users", "peak requests per second", "measured sustainable requests per second per instance"])
            instance_warnings.append("User count alone does not determine an instance count.")
        if base_instances is not None:
            proposed = _ceil(base_instances * (_ONE + margin / _HUNDRED))
            calculations.append(_calculation(calculation_id="instance-requirement", resource_reference="application-processes", calculation_type="INSTANCE_REQUIREMENT", formula_id="capacity.instances", inputs={"base_instances": (base_instances, "INSTANCES", "DERIVED"), "safety_margin_percent": (margin, "%", "USER_PROVIDED")}, intermediates=[SizingIntermediate(name="base_instance_requirement", value=base_instances, unit="INSTANCES", provenance="DERIVED"), SizingIntermediate(name="additional_capacity", value=proposed - base_instances, unit="INSTANCES", provenance="DERIVED")], final_value=proposed, result_unit="INSTANCES", scenario_id=scenario_id, assumptions=["Minimum redundancy is treated as a minimum total instance count."], margin=margin, additional_capacity=proposed - base_instances, warnings=instance_warnings))
        else:
            missing.append("instance count or measured throughput benchmark")
        logical_bytes: Decimal | None = None
        logical_inputs: dict[str, tuple[Decimal | int | float | None, str, InputProvenance]] = {}
        if requirements.data_volume is not None:
            logical_bytes = _convert(requirements.data_volume, requirements.data_volume_unit, "BYTES")
            logical_inputs["initial_logical_volume"] = _input(requirements.data_volume, requirements.data_volume_unit)
        if requirements.expected_growth_per_period is not None and requirements.growth_period_days and requirements.projection_period_days:
            growth = _convert(requirements.expected_growth_per_period, requirements.growth_unit, "BYTES") * Decimal(requirements.projection_period_days) / Decimal(requirements.growth_period_days)
            logical_bytes = (logical_bytes or _ZERO) + growth
            logical_inputs["expected_growth_per_period"] = _input(requirements.expected_growth_per_period, requirements.growth_unit)
        elif requirements.expected_records is not None and requirements.average_stored_bytes_per_record is not None:
            growth = Decimal(requirements.expected_records) * requirements.average_stored_bytes_per_record
            logical_bytes = (logical_bytes or _ZERO) + growth
            logical_inputs["expected_records"] = (requirements.expected_records, "RECORDS", "USER_PROVIDED")
            logical_inputs["average_stored_bytes_per_record"] = _input(requirements.average_stored_bytes_per_record, "BYTES_PER_RECORD")
        if logical_bytes is not None:
            storage_additional = logical_bytes * margin / _HUNDRED
            proposed_logical = logical_bytes + storage_additional
            calculations.append(_calculation(calculation_id="logical-storage", resource_reference="logical-data", calculation_type="LOGICAL_STORAGE", formula_id="storage.logical" if requirements.expected_records is None else "storage.records", inputs=logical_inputs, intermediates=[SizingIntermediate(name="logical_storage_bytes", value=logical_bytes, unit="BYTES", provenance="DERIVED"), SizingIntermediate(name="additional_capacity", value=storage_additional, unit="BYTES", provenance="DERIVED")], final_value=proposed_logical, result_unit="BYTES", scenario_id=scenario_id, assumptions=[f"Projection period is {requirements.projection_period_days} days." if requirements.projection_period_days else "Only explicitly supplied logical volume was used."], margin=margin, additional_capacity=storage_additional))
            if requirements.index_overhead_factor is not None:
                calculations.append(_calculation(calculation_id="provisioned-storage", resource_reference="logical-data", calculation_type="PROVISIONED_STORAGE", formula_id="storage.provisioned", inputs={"logical_storage": (proposed_logical, "BYTES", "DERIVED"), "index_overhead_factor": (requirements.index_overhead_factor, "FACTOR", "USER_PROVIDED")}, intermediates=[], final_value=proposed_logical * requirements.index_overhead_factor, result_unit="BYTES", scenario_id=scenario_id))
            if requirements.backup_factor is not None:
                calculations.append(_calculation(calculation_id="backup-storage", resource_reference="logical-data", calculation_type="BACKUP_STORAGE", formula_id="storage.backup", inputs={"logical_storage": (proposed_logical, "BYTES", "DERIVED"), "backup_factor": (requirements.backup_factor, "FACTOR", "USER_PROVIDED")}, intermediates=[], final_value=proposed_logical * requirements.backup_factor, result_unit="BYTES", scenario_id=scenario_id))
            if requirements.replication_factor is not None:
                calculations.append(_calculation(calculation_id="replicated-capacity", resource_reference="logical-data", calculation_type="REPLICATED_CAPACITY", formula_id="storage.replication", inputs={"logical_storage": (proposed_logical, "BYTES", "DERIVED"), "replication_factor": (requirements.replication_factor, "FACTOR", "USER_PROVIDED")}, intermediates=[], final_value=proposed_logical * requirements.replication_factor, result_unit="BYTES", scenario_id=scenario_id))
        elif requirements.runtime_type in {"hana-cloud", "integration"}:
            missing.append("initial data volume or explicit record-growth inputs")
        quota_memory = calculations[0].final_value if calculations and calculations[0].calculation_type == "CONFIGURED_MEMORY" else None
        quota_instances = next((item.final_value for item in calculations if item.calculation_type == "INSTANCE_REQUIREMENT"), None)
        quota_disk = next((item.final_value for item in calculations if item.calculation_type == "CONFIGURED_DISK"), None)
        quota_impacts = [
            self._quota_impact(dimension="MEMORY", state=requirements.quota_memory_state, limit=_convert(requirements.quota_memory_limit, requirements.quota_memory_unit, "MB") if requirements.quota_memory_limit is not None else None, usage=_convert(requirements.quota_memory_usage, requirements.quota_memory_unit, "MB") if requirements.quota_memory_usage is not None else None, unit="MB", proposed=quota_memory, observed_at=requirements.quota_observed_at, source=requirements.quota_source, resize_existing=requirements.resize_existing, existing=_convert(requirements.existing_memory_allocation, requirements.existing_memory_unit, "MB") if requirements.existing_memory_allocation is not None else None),
            self._quota_impact(dimension="INSTANCES", state=requirements.quota_instance_state, limit=Decimal(requirements.quota_instance_limit) if requirements.quota_instance_limit is not None else None, usage=Decimal(requirements.quota_instance_usage) if requirements.quota_instance_usage is not None else None, unit="INSTANCES", proposed=quota_instances, observed_at=requirements.quota_observed_at, source=requirements.quota_source, resize_existing=requirements.resize_existing, existing=Decimal(requirements.existing_instance_count) if requirements.existing_instance_count is not None else None),
            self._quota_impact(dimension="DISK", state=requirements.quota_disk_state, limit=_convert(requirements.quota_disk_limit, requirements.quota_disk_unit, "MB") if requirements.quota_disk_limit is not None else None, usage=_convert(requirements.quota_disk_usage, requirements.quota_disk_unit, "MB") if requirements.quota_disk_usage is not None else None, unit="MB", proposed=quota_disk, observed_at=requirements.quota_observed_at, source=requirements.quota_source, resize_existing=False, existing=None),
        ]
        if requirements.quota_observed_at and (now - requirements.quota_observed_at).total_seconds() > 86_400:
            warnings.append("Quota evidence is older than 24 hours and may be stale.")
        warnings.extend(item for impact in quota_impacts for item in impact.warnings)
        missing = _dedupe(missing)
        unsupported = requirements.runtime_type == "abap"
        status = "UNSUPPORTED" if unsupported else "NEEDS_INPUT" if not calculations else "PARTIAL" if missing else "COMPLETE"
        return SizingResult(result_id=uuid.uuid4(), scenario_id=scenario_id, formula_version=FORMULA_VERSION, runtime_type=requirements.runtime_type, application_name=requirements.application_name, architecture_proposal_id=requirements.architecture_proposal_id, architecture_proposal_revision=requirements.architecture_proposal_revision, calculations=calculations, quota_impacts=quota_impacts, assumptions=assumptions, warnings=_dedupe(warnings), missing_inputs=missing, status=status, generated_at=now)

    async def calculate(self, session: AsyncSession | None, principal: Principal | None, request: SizingCalculationRequest) -> SizingResult:
        requirements = request.requirements or self.analyze(request.request_text or "").requirements
        mismatch = None
        if requirements.target_environment_id is not None:
            if session is None or principal is None:
                raise PermissionDeniedError("An authorized BTP environment is required for environment-linked sizing.")
            coverage = await coverage_for(session, principal, Permission.BTP_READ)
            if not coverage.covers_environment(requirements.target_environment_id):
                raise PermissionDeniedError("You do not have access to the requested sizing environment.", code="btp_scope_revoked")
            if await session.get(BTPEnvironment, requirements.target_environment_id) is None:
                raise NotFoundError("BTP environment not found.")
        if requirements.quota_environment_id is not None and requirements.target_environment_id is not None and requirements.quota_environment_id != requirements.target_environment_id:
            raise PermissionDeniedError("Quota evidence belongs to a different environment.", code="sizing_environment_mismatch")
        if requirements.architecture_proposal_id is not None:
            if self.architecture_service is None or session is None or principal is None:
                raise PermissionDeniedError("An authorized architecture proposal context is required for proposal-linked sizing.")
            proposal = await self.architecture_service.get(session, principal, requirements.architecture_proposal_id)
            if requirements.architecture_proposal_revision is not None and requirements.architecture_proposal_revision != proposal.revision:
                mismatch = f"Sizing was requested for architecture revision {requirements.architecture_proposal_revision}, but the latest authorized revision is {proposal.revision}; no proposal was modified."
            if requirements.target_environment_id is not None and proposal.environment_id is not None and requirements.target_environment_id != proposal.environment_id:
                raise PermissionDeniedError("The sizing environment does not match the authorized architecture proposal.")
        if requirements.include_live_quota:
            requirements = await self._live_quota_requirements(requirements, session, principal)
        result = self.calculate_sync(requirements, scenario_id=request.scenario_id, proposal_revision_mismatch=mismatch)
        if request.persist:
            if session is None or principal is None:
                raise PermissionDeniedError("A signed-in user is required to persist sizing history.")
            session.add(SizingResultRecord(result_id=result.result_id, owner_user_id=principal.user_id, btp_account_id=None, btp_environment_id=requirements.target_environment_id, architecture_proposal_id=result.architecture_proposal_id, architecture_proposal_revision=result.architecture_proposal_revision, scenario_id=result.scenario_id, formula_version=result.formula_version, result_json=result.model_dump(mode="json"), requirements_json=requirements.model_dump(mode="json")))
            await session.commit()
        return result

    async def _live_quota_requirements(self, requirements: SizingRequirements, session: AsyncSession | None, principal: Principal | None) -> SizingRequirements:
        if session is None or principal is None or self.cloud_foundry_service is None:
            raise PermissionDeniedError("An authorized provider read context is required for live quota evidence.", code="sizing_live_quota_context_required")
        authorized, quotas, _ = await self.cloud_foundry_service.organization_quotas(session, principal)
        if requirements.target_environment_id is not None and authorized.context.btp_environment_id != requirements.target_environment_id:
            raise PermissionDeniedError("The provider quota observation is from a different environment.", code="sizing_environment_mismatch")
        if not quotas:
            return requirements.model_copy(update={"quota_memory_state": "UNKNOWN", "quota_instance_state": "UNKNOWN", "quota_disk_state": "UNKNOWN", "quota_source": "LIVE_PROVIDER", "quota_observed_at": datetime.now(UTC), "quota_environment_id": authorized.context.btp_environment_id})
        quota = quotas[0]
        usage = None
        if quota.organization_ids:
            try:
                _, usage = await self.cloud_foundry_service.organization_usage_summary(session, principal, quota.organization_ids[0])
            except AppError:
                usage = None
        def state(value) -> str:
            return "ZERO" if value.state == "value" and value.value == 0 else "FINITE" if value.state == "value" else "UNLIMITED" if value.state == "unlimited" else "UNKNOWN"
        observed = datetime.now(UTC)
        return requirements.model_copy(update={
            "quota_memory_limit": quota.total_memory_mb.value, "quota_memory_usage": usage.memory_in_mb if usage else None, "quota_memory_unit": "MB", "quota_memory_state": state(quota.total_memory_mb),
            "quota_instance_limit": quota.total_instances.value, "quota_instance_usage": usage.started_instances if usage else None, "quota_instance_state": state(quota.total_instances),
            "quota_disk_state": "UNKNOWN", "quota_source": "LIVE_PROVIDER", "quota_observed_at": observed, "quota_environment_id": authorized.context.btp_environment_id,
        })

    def compare(self, request: SizingComparisonRequest) -> SizingComparisonResponse:
        results = [self.calculate_sync(item.requirements, scenario_id=item.scenario_id) for item in request.scenarios]
        return SizingComparisonResponse(comparison_id=uuid.uuid4(), formula_version=FORMULA_VERSION, results=results, compared_dimensions=["instance count", "configured memory", "disk allocation", "logical storage", "quota impact", "warnings and missing evidence"], generated_at=datetime.now(UTC))

    async def get(self, session: AsyncSession, principal: Principal, result_id: uuid.UUID) -> SizingResult:
        record = await session.scalar(select(SizingResultRecord).where(SizingResultRecord.result_id == result_id, SizingResultRecord.owner_user_id == principal.user_id))
        if record is None:
            raise NotFoundError("Sizing result not found.")
        return SizingResult.model_validate(record.result_json)

    async def list(self, session: AsyncSession, principal: Principal, limit: int = 20, offset: int = 0) -> SizingResultPage:
        rows = list(await session.scalars(select(SizingResultRecord).where(SizingResultRecord.owner_user_id == principal.user_id).order_by(SizingResultRecord.updated_at.desc()).offset(offset).limit(limit + 1)))
        return SizingResultPage(items=[SizingResult.model_validate(row.result_json) for row in rows[:limit]], has_more=len(rows) > limit)
