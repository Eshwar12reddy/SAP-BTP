# Phase 20 health and quota diagnosis

Phase 20 extends the existing Phase 13 diagnosis service and Phase 19 inventory connector.
It does not introduce a second diagnosis engine or any write capability.

## Delivered capabilities

- Deterministic stopped, expected-running, instance mismatch, zero-running, process-state,
  crash-window, health-check, deployment-correlation, partial-evidence, and missing-evidence findings.
- Provider-reported process memory and CPU observations. Memory utilization is calculated only
  when both observed bytes and a positive provider-reported memory limit are present.
- Configurable memory and quota warning/critical thresholds; thresholds are risk signals, not
  confirmed root causes.
- Organization and space quota diagnosis using compatible provider usage units. Organization
  and space limits are combined conservatively for space analysis where both are visible.
- Explicit `FINITE`, `ZERO`, `UNLIMITED`, and `UNKNOWN` quota semantics.
- `POTENTIALLY_UNUSED_REQUIRES_VERIFICATION` service candidates. Missing visible bindings are
  never treated as proof that a service is unused or safe to delete.
- Health overview, application health, quota diagnosis, and unused-resource API endpoints.
- Frozen read-only tools: `btp_health_overview`, `btp_application_health`,
  `btp_quota_diagnose`, and `btp_resource_usage_analyze`.
- Health-aware agent routing and UI status display with partial/unavailable states.

## Evidence rules

Every finding carries a resource, evidence references, observation timestamp, source type,
confidence, severity, diagnostic status, freshness, missing evidence, and optional calculation
details. DeepSeek receives backend-validated findings only and may explain them, but cannot add
measurements, identities, or root causes.

The Cloud Foundry process stats contract reports instance state, current memory/disk usage,
CPU, and provider quotas through `GET /v3/processes/:guid/stats`; those values are used only
with their documented units ([Cloud Foundry CAPI reference](https://v3-apidocs.cloudfoundry.org/version/3.150.0/index.html)).
Cloud Foundry organization and space quota resources are read-only inputs to the analysis
([organization quotas](https://v3-apidocs.cloudfoundry.org/version/3.150.0/index.html),
[space quotas](https://v3-apidocs.cloudfoundry.org/version/3.150.0/index.html)).

## Verification

- Full backend suite: 445 passed, 1 existing deprecation warning.
- Phase 20/pure diagnosis and agent regression subset: 88 passed.
- Frontend suite: 78 passed.
- TypeScript, production build, Ruff, compilation, and diff checks passed.
- Database-backed verification used the project `btp-postgres` service and an isolated `btp_agent_test` database; the main application database was not reset or migrated.
