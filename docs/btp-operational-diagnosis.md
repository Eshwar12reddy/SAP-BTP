# BTP operational diagnosis

Phase 13 adds a bounded diagnosis workflow on top of the Phase 12 read-only Cloud
Foundry V3 connector. It does not create a second agent or add infrastructure
operations.

## Architecture

`GET /api/v1/btp/runtime/applications/{application_id}/diagnosis` and the
`btp_application_diagnose` agent tool use the same backend flow:

1. Recheck the caller's `btp:read` permission and active, enabled Phase 11 connection.
2. Reuse the Phase 12 Cloud Foundry scope mapping and fixed-origin GET client.
3. Collect the target application, processes, bounded process statistics, recent usage
   events, service-instance metadata, and service-binding metadata.
4. Keep partial provider failures and missing evidence explicit.
5. Apply deterministic rules to the typed evidence.
6. Optionally ask DeepSeek to explain the resulting findings only; DeepSeek never
   selects resources, changes classifications, selects tools, or performs actions.

The collection has a total timeout and separate bounds for processes, events and
expected service relationships. Binding credentials and raw provider payloads are
never included in the diagnosis contract.

## Evidence and findings

Each finding contains a stable finding ID, resource ID, rule ID, severity, observation,
timestamp, confidence, evidence-reference IDs, possible causes and safe next steps.
Evidence references identify only the Cloud Foundry source, provider resource ID,
observation timestamp and a bounded summary.

Confidence is intentionally separate from severity:

- `CONFIRMED`: directly observed in the typed provider response;
- `LIKELY`: strongly correlated observations, explicitly marked as non-causal;
- `POSSIBLE`: a hypothesis that needs verification;
- `UNKNOWN`: the available evidence is insufficient.

Implemented rules cover stopped applications, zero running instances, requested/running
instance mismatches, recent and repeated crash events, explicitly exposed health-check
failure events, abnormal process states, deployment/staging/build failure events,
explicitly expected-but-missing service relationships, partial provider failures and
insufficient evidence. A normal bounded observation is reported as informational only;
it is not a claim that the application is universally healthy.

Logs are not assumed to exist. The result explicitly says that application logs are
outside the supported connector and recommends an approved platform workflow when
logs or deployment history are required.

No finding establishes a definitive root cause in this phase. Correlated deployment
and stopped-state evidence is labeled `LIKELY`, while the result says that causation
still requires verification.

## Agent behavior

Diagnosis questions are rule-routed into the existing single agent. The agent requires
an explicit Cloud Foundry application ID/GUID; it never guesses an application from a
name or substitutes an environment. It renders deterministic findings, evidence
references, missing information and safe next steps. If the optional AI explanation
setting is enabled and DeepSeek is configured, the model receives only the already
typed, redacted diagnosis. If the model is unavailable, the deterministic diagnosis
is returned unchanged.

The tool is registered as a frozen, read-only `SourceType.LIVE_BTP` tool with
`Permission.BTP_READ`. No shell, SQL, arbitrary HTTP, deployment, restart, stop,
scale, delete or binding-change tool is available.

## Authorization and audit

The active selection and BTP scope are rechecked before evidence collection. Provider
GUID mappings remain server-controlled and names never authorize access. Cross-tenant
or revoked access therefore fails before provider evidence is returned. Diagnosis
requests and bounded outcomes are recorded in the existing audit stream with resource
IDs, outcome, error category or finding count only; evidence payloads and credentials
are not logged.

## API and UI

The diagnosis endpoint supports repeated `expected_service_instance_id` query values
when an operator has an explicit service expectation. The BTP connections page offers
a small Diagnose action beside discovered applications and shows state, findings,
severity, confidence, evidence references, possible causes, safe next steps, missing
information and the optional AI explanation.

## Limitations

- Cloud Foundry usage events are bounded and provider retention is outside this agent's
  control.
- The current connector does not retrieve application logs, deployment output, build
  artifacts, routes, health-check configuration or raw service credentials.
- A process statistic is only counted as running when the provider supplies a recognized
  running state; unknown health state remains unknown.
- Diagnosis is observational. Infrastructure-changing actions require a future,
  separately authorized approval workflow.
