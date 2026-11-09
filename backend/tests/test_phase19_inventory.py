"""Pure contract tests for the Phase 19 inventory boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.agents.contracts import Route
from app.agents.router import route_by_rules
from app.integrations.sap_btp.cloud_foundry import (
    CFAppEvent,
    CFApplication,
    CFProcess,
    CFProcessStat,
    CFQuotaValue,
    _parse_quota,
)
from app.schemas.cloud_foundry import InventoryStatusRead
from app.services.diagnosis_service import _evaluate
from app.services.health_diagnosis_service import _quota_state


def test_inventory_queries_route_to_read_only_inventory() -> None:
    assert route_by_rules("List service offerings") is Route.BTP_INVENTORY
    assert route_by_rules("Show the organization quotas") is Route.BTP_INVENTORY
    assert route_by_rules("List Cloud Foundry spaces") is Route.BTP_INVENTORY
    assert route_by_rules("Explain what a destination is") is Route.DOCUMENTATION


def test_quota_parser_preserves_zero_null_and_unlimited_semantics() -> None:
    quota = _parse_quota(
        {
            "guid": "quota-1",
            "name": "safe-quota",
            "apps": {
                "total_memory_in_mb": 0,
                "total_instances": -1,
                "per_app_tasks": None,
            },
            "services": {"total_service_instances": 3},
            "routes": {"total_routes": 0},
            "relationships": {"organizations": {"data": [{"guid": "org-1"}]}, "spaces": {"data": []}},
        },
        "organization",
    )
    assert (quota.total_memory_mb.value, quota.total_memory_mb.state) == (0, "value")
    assert (quota.total_instances.value, quota.total_instances.state) == (None, "unlimited")
    assert (quota.per_app_tasks.value, quota.per_app_tasks.state) == (None, "unknown")
    assert (quota.total_routes.value, quota.total_routes.state) == (0, "value")


def test_enterprise_inventory_unavailability_is_typed() -> None:
    status = InventoryStatusRead(
        resource_type="destination",
        source_type="LIVE_PROVIDER",
        retrieval_status="NOT_CONFIGURED",
        observed_at="2026-11-04T00:00:00Z",
        environment_id=None,
        reason="dedicated adapter missing",
    )
    assert status.retrieval_status == "NOT_CONFIGURED"
    assert status.environment_id is None


def _diagnose(application: CFApplication, stat: CFProcessStat, *, expected_running: bool | None = None, events=None):
    return _evaluate(
        application,
        [CFProcess("p-1", "web", "RUNNING", 1, application.id, None, None)],
        {"p-1": [stat]},
        events or [],
        [],
        [],
        f"application:{application.id}",
        {"p-1": "process:p-1"},
        {"p-1": ["process-stat:p-1:0"]},
        {event.id: f"event:{event.id}" for event in events or []},
        {},
        {},
        [],
        [],
        datetime(2026, 11, 4, tzinfo=UTC),
        expected_running=expected_running,
        crash_window_seconds=3600,
    )


def test_health_rules_calculate_reported_memory_and_preserve_expected_stop_intent() -> None:
    now = datetime(2026, 11, 4, tzinfo=UTC)
    stat = CFProcessStat(
        process_type="web", index=0, state="RUNNING", observed_at=now,
        cpu=None, memory_bytes=90, disk_bytes=None, uptime_seconds=10,
        memory_quota_bytes=100, disk_quota_bytes=None,
    )
    findings = _diagnose(CFApplication("app-1", "Orders", "STOPPED", None, "space", None, None), stat, expected_running=True)
    stop = next(item for item in findings if item.rule_id == "unexpectedly_stopped")
    memory = next(item for item in findings if item.rule_id == "reported_memory_utilization")
    assert stop.confidence == "CONFIRMED"
    assert memory.calculation_details["utilization_percent"] == 90.0
    assert memory.severity == "HIGH"


def test_health_rules_do_not_invent_missing_metrics_or_old_crash_counts() -> None:
    now = datetime(2026, 11, 4, tzinfo=UTC)
    old = now - timedelta(hours=2)
    events = [
        CFAppEvent("e-1", "app_usage", "app-1", "Orders", "web", "CRASHED", "RUNNING", old, 0),
        CFAppEvent("e-2", "app_usage", "app-1", "Orders", "web", "CRASHED", "RUNNING", old, 0),
    ]
    stat = CFProcessStat("web", 0, "RUNNING", now, None, None, None, 10)
    findings = _diagnose(CFApplication("app-1", "Orders", "STARTED", None, "space", None, None), stat, events=events)
    assert not any(item.rule_id == "reported_memory_utilization" for item in findings)
    assert not any(item.rule_id == "cpu_measurement_observed" for item in findings)
    assert not any(item.rule_id == "repeated_crash_events" for item in findings)
    assert _quota_state(CFQuotaValue(None, "unlimited")) == "UNLIMITED"
    assert _quota_state(CFQuotaValue(0, "value")) == "ZERO"
