# Phase 21 service recommendation audit

Date: 2026-11-04

## Capability inventory

| Capability | Status | Evidence |
| --- | --- | --- |
| Typed requirements contract | IMPLEMENTED | `app/schemas/recommendations.py` |
| Requirements extraction | IMPLEMENTED | Deterministic keyword extraction; missing workload, budget and runtime are explicit |
| Versioned reference catalog | IMPLEMENTED | `app/services/service_reference_catalog.py`, SAP Help URLs only |
| Live Cloud Foundry offerings | IMPLEMENTED | Reuses Phase 19 authorized read-only adapter |
| Entitlement verification | PARTIAL | Provider catalog visibility is shown; dedicated SAP entitlement adapter remains unavailable |
| Quota sufficiency for a service | PARTIAL | Phase 20 quota evidence exists, but service-specific allocation is `NOT_CHECKED` |
| Runtime and region hard checks | IMPLEMENTED / PARTIAL | Runtime compatibility is checked; catalog region matrix is not available, so region is `UNKNOWN` when requested |
| Deterministic ranking | IMPLEMENTED | Capability fit, documented priority, existing-service reuse and stable key tie-break |
| Alternatives and prerequisites | IMPLEMENTED | Catalog relationships and official source URLs |
| DeepSeek/LangGraph integration | IMPLEMENTED | New route, same single agent, same LangGraph plan and frozen ToolRegistry; model cannot mutate recommendation facts |
| API authorization and audit | IMPLEMENTED | Chat authorization, optional BTP live scope, recommendation requested/completed audit events |
| Frontend interface | IMPLEMENTED | Free text, optional live evidence, statuses, source links and comparison |
| Provisioning | UNSUPPORTED | No infrastructure writes were added |

## Safety decisions

- No Discovery Center undocumented endpoint or scraper was introduced.
- No price or fictitious plan is stored in the reference catalog.
- A visible Cloud Foundry offering is reported as `AVAILABLE_IN_PROVIDER_CATALOG`, never as
  account entitlement.
- Region and quota unknowns remain unknown; they do not pass hard eligibility checks.
- Recommendation tools are read-only and run only through the frozen ToolRegistry.
- Long-term memory is not written by recommendation requests, so unverified advice cannot
  become confirmed memory.

## Verification scope

Focused tests cover catalog validation, database and S/4HANA matching, authentication and
identity capability extraction, event and workflow capability extraction, runtime mismatch,
unknown region, entitlement not verified, existing-service reuse, multiple candidates,
invalid/fabricated catalog records, comparison source links, request validation and the
distinct recommendation route. Existing backend and frontend regression suites remain the
authoritative compatibility checks.

## Known limitations

The available live connector exposes Cloud Foundry service offerings and plans but not a
dedicated SAP entitlement or price source. The catalog therefore intentionally recommends
service families and documents prerequisites without selecting a plan. A future approved
provider adapter can enrich the same contracts without changing the deterministic matching
boundary.
