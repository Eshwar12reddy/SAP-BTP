"""Pure deterministic diagnosis rules; no provider or AI calls are made."""

from __future__ import annotations

from datetime import UTC, datetime

from app.integrations.sap_btp.cloud_foundry import (
    CFAppEvent,
    CFApplication,
    CFProcess,
    CFProcessStat,
    CFServiceBinding,
)
from app.schemas.diagnosis import ProviderFailure
from app.services.diagnosis_service import _evaluate

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def app(state: str = "STARTED") -> CFApplication:
    return CFApplication("app-1", "Orders API", state, "buildpack", "space-1", NOW, NOW)


def process(instances: int = 2, state: str = "RUNNING") -> CFProcess:
    return CFProcess("process-1", "web", state, instances, "app-1", NOW, NOW)


def stat(state: str) -> CFProcessStat:
    return CFProcessStat("web", 0, state, NOW, 0.1, 128, 256, 30)


def event(event_id: str, *, event_type: str = "app_usage", current: str = "CRASHED") -> CFAppEvent:
    return CFAppEvent(event_id, event_type, "app-1", "Orders API", "web", current, "RUNNING", 0, NOW)


def evaluate(
    application: CFApplication,
    processes: list[CFProcess] | None,
    stats: dict[str, list[CFProcessStat] | None],
    events: list[CFAppEvent] | None = None,
    bindings: list[CFServiceBinding] | None = None,
    expected: list[str] | None = None,
    failures: list[ProviderFailure] | None = None,
    missing: list[str] | None = None,
):
    return _evaluate(
        application,
        processes,
        stats,
        events,
        bindings,
        expected or [],
        "application:app-1",
        {"process-1": "process:process-1"},
        {"process-1": ["process-stat:process-1:0", "process-stat:process-1:1"]},
        {item.id: f"event:{item.id}" for item in events or []},
        {item.id: f"binding:{item.id}" for item in bindings or []},
        {},
        failures or [],
        missing or [],
        NOW,
    )


def test_stopped_application_is_confirmed_high_severity() -> None:
    findings = evaluate(app("STOPPED"), [process(0, "STOPPED")], {"process-1": []})
    finding = next(item for item in findings if item.rule_id == "application_stopped")
    assert finding.severity == "HIGH"
    assert finding.confidence == "CONFIRMED"


def test_instance_mismatch_and_repeated_crashes_are_evidence_linked() -> None:
    findings = evaluate(
        app(),
        [process()],
        {"process-1": [stat("RUNNING"), stat("CRASHED")]},
        [event("event-1"), event("event-2")],
    )
    mismatch = next(item for item in findings if item.rule_id == "instance_count_mismatch")
    crashes = next(item for item in findings if item.rule_id == "repeated_crash_events")
    assert mismatch.severity == "HIGH"
    assert "process-stat:process-1:0" in mismatch.evidence_references
    assert crashes.confidence == "CONFIRMED"
    assert crashes.severity == "HIGH"
    assert set(crashes.evidence_references) >= {"event:event-1", "event:event-2"}


def test_deployment_correlation_is_likely_not_claimed_as_causation() -> None:
    findings = evaluate(
        app("STOPPED"),
        [process(0, "STOPPED")],
        {"process-1": []},
        [event("event-deploy", event_type="deployment_failed", current="FAILED")],
    )
    finding = next(item for item in findings if item.rule_id == "correlated_deployment_failure")
    assert finding.confidence == "LIKELY"
    assert "causation is not established" in finding.observation


def test_expected_service_and_partial_provider_failure_are_explicit() -> None:
    failure = ProviderFailure(
        source="cloud_foundry_v3",
        operation="application_events",
        error_code="provider_timeout",
        observed_at=NOW,
    )
    findings = evaluate(
        app(),
        [process()],
        {"process-1": [stat("RUNNING")]},
        events=None,
        bindings=[],
        expected=["service-expected"],
        failures=[failure],
        missing=["The application_events evidence source did not complete."],
    )
    rules = {item.rule_id for item in findings}
    assert "missing_expected_service_relationship" in rules
    assert "provider_partial_failure" in rules
    assert "insufficient_evidence" in rules
