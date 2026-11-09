"""Numerical and provenance contracts for Phase 23 sizing."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.agents.contracts import Route
from app.agents.router import route_by_rules
from app.schemas.sizing import SizingComparisonRequest, SizingRequirements, SizingScenario
from app.services.sizing_service import FORMULA_VERSION, SizingService


def test_memory_and_multi_process_aggregation_are_configured_allocation() -> None:
    service = SizingService()
    result = service.calculate_sync(SizingRequirements(process_types=[
        {"process_type": "web", "instance_count": 3, "memory_per_instance": "512", "memory_unit": "MB"},
        {"process_type": "worker", "instance_count": 2, "memory_per_instance": "256", "memory_unit": "MiB"},
    ]))
    memory = next(item for item in result.calculations if item.calculation_type == "CONFIGURED_MEMORY")
    assert memory.final_value == Decimal(1536) + Decimal(512) * Decimal(1_048_576) / Decimal(1_000_000)
    assert "not observed" in memory.assumptions[0].lower()


def test_throughput_uses_ceiling_and_explicit_redundancy_and_margin() -> None:
    result = SizingService().calculate_sync(SizingRequirements(
        peak_requests_per_second=Decimal(100), sustainable_requests_per_second_per_instance=Decimal(30),
        minimum_redundancy_instances=4, safety_margin_percent=Decimal(25),
    ))
    instances = next(item for item in result.calculations if item.calculation_type == "INSTANCE_REQUIREMENT")
    assert instances.final_value == Decimal(5)
    assert instances.additional_capacity == Decimal(1)


def test_user_count_alone_does_not_invent_capacity() -> None:
    result = SizingService().calculate_sync(SizingRequirements(user_count=10_000))
    assert result.status == "NEEDS_INPUT"
    assert not any(item.calculation_type == "INSTANCE_REQUIREMENT" for item in result.calculations)
    assert "concurrent users" in result.missing_inputs


def test_disk_and_storage_projection_show_distinct_dimensions() -> None:
    result = SizingService().calculate_sync(SizingRequirements(
        instance_count=3, configured_disk_per_instance=Decimal(512), configured_disk_unit="MB",
        data_volume=Decimal(10), data_volume_unit="GB", expected_growth_per_period=Decimal(5),
        growth_unit="GB", growth_period_days=30, projection_period_days=365,
        index_overhead_factor=Decimal("1.2"), backup_factor=Decimal(2), replication_factor=Decimal(3),
    ))
    assert next(item for item in result.calculations if item.calculation_type == "CONFIGURED_DISK").final_value == Decimal(1536)
    assert {item.calculation_type for item in result.calculations} >= {"LOGICAL_STORAGE", "PROVISIONED_STORAGE", "BACKUP_STORAGE", "REPLICATED_CAPACITY"}


def test_record_growth_and_retention_are_explicit_inputs() -> None:
    result = SizingService().calculate_sync(SizingRequirements(expected_records=1000, average_stored_bytes_per_record=Decimal(100), retention_period_days=365))
    logical = next(item for item in result.calculations if item.calculation_type == "LOGICAL_STORAGE")
    assert logical.final_value == Decimal(100000)
    assert logical.input_provenance["expected_records"] == "USER_PROVIDED"


def test_quota_finite_unlimited_zero_and_unknown_semantics() -> None:
    service = SizingService()
    finite = service.calculate_sync(SizingRequirements(instance_count=3, configured_memory_per_instance=512, quota_memory_limit=10_000, quota_memory_usage=6_000, quota_memory_state="FINITE", quota_memory_unit="MB"))
    impact = next(item for item in finite.quota_impacts if item.dimension == "MEMORY")
    assert impact.projected_usage == Decimal(7536)
    assert impact.remaining_capacity == Decimal(2464)
    assert impact.projected_utilization_percent == Decimal("75.36")
    unlimited = service.calculate_sync(SizingRequirements(instance_count=1, configured_memory_per_instance=512, quota_memory_state="UNLIMITED"))
    assert next(item for item in unlimited.quota_impacts if item.dimension == "MEMORY").projected_utilization_percent is None
    zero = service.calculate_sync(SizingRequirements(instance_count=1, configured_memory_per_instance=512, quota_memory_state="ZERO"))
    assert next(item for item in zero.quota_impacts if item.dimension == "MEMORY").compatible is False
    unknown = service.calculate_sync(SizingRequirements(instance_count=1, configured_memory_per_instance=512))
    assert next(item for item in unknown.quota_impacts if item.dimension == "MEMORY").quota_state == "UNKNOWN"


def test_resize_does_not_double_count_existing_allocation() -> None:
    result = SizingService().calculate_sync(SizingRequirements(instance_count=3, configured_memory_per_instance=1000, existing_memory_allocation=800, resize_existing=True, quota_memory_limit=5000, quota_memory_usage=2000, quota_memory_state="FINITE"))
    impact = next(item for item in result.quota_impacts if item.dimension == "MEMORY")
    assert impact.additional_allocation == Decimal(2200)
    assert impact.projected_usage == Decimal(4200)


def test_units_precision_and_no_implicit_margin() -> None:
    service = SizingService()
    result = service.calculate_sync(SizingRequirements(instance_count=1, configured_memory_per_instance=Decimal("1.5"), configured_memory_unit="GiB"))
    memory = next(item for item in result.calculations if item.calculation_type == "CONFIGURED_MEMORY")
    assert memory.final_value == Decimal("1610.612736")
    assert memory.safety_margin_percent == Decimal(0)
    assert memory.additional_capacity == Decimal(0)


def test_invalid_negative_or_zero_throughput_inputs_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SizingRequirements(instance_count=-1)
    with pytest.raises(ValidationError):
        SizingRequirements(sustainable_requests_per_second_per_instance=0)
    with pytest.raises(ValidationError):
        SizingRequirements(expected_records=10)


def test_specialized_runtime_limit_and_formula_version() -> None:
    result = SizingService().calculate_sync(SizingRequirements(runtime_type="abap", user_count=1000))
    assert result.status == "UNSUPPORTED"
    assert any("ABAP" in warning for warning in result.warnings)
    assert result.formula_version == FORMULA_VERSION


def test_scenarios_use_the_same_reproducible_engine() -> None:
    request = SizingComparisonRequest(scenarios=[
        SizingScenario(scenario_id="baseline", name="Baseline", requirements=SizingRequirements(instance_count=2, configured_memory_per_instance=512)),
        SizingScenario(scenario_id="growth", name="Growth", requirements=SizingRequirements(instance_count=4, configured_memory_per_instance=512)),
    ])
    response = SizingService().compare(request)
    assert [item.scenario_id for item in response.results] == ["baseline", "growth"]
    assert response.results[0].calculations[0].formula.formula_version == FORMULA_VERSION
    assert response.results[1].calculations[0].final_value == Decimal(2048)


def test_text_analysis_keeps_missing_workload_evidence() -> None:
    analysis = SizingService().analyze("Size a Cloud Foundry application for 10,000 users.")
    assert analysis.requirements.user_count == 10_000
    assert analysis.analysis_status == "NEEDS_CLARIFICATION"
    assert "peak requests per second" in analysis.missing_inputs


def test_sizing_route_is_dedicated_only_when_context_is_explicit() -> None:
    assert route_by_rules("Calculate SAP BTP Cloud Foundry memory and instance sizing.") is Route.BTP_SIZING
    assert route_by_rules("Estimate memory requirements.") is Route.SIZING
