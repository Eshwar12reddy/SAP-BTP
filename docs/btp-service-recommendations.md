# SAP BTP service recommendations

Phase 21 adds a deterministic, read-only recommendation boundary to the existing single
agent. It answers architecture questions such as which documented BTP services fit an
employee application integrated with S/4HANA, while keeping suitability separate from
account-specific availability.

## Architecture

The flow is:

1. `ServiceRequirements` is populated from explicit user text or a typed request.
2. A versioned reference catalog maps capability categories to documented SAP BTP service
   families.
3. Hard checks evaluate capability fit, runtime compatibility, region evidence, exclusions,
   and live provider visibility.
4. Candidates are ranked deterministically by capability fit, documented priority and
   existing-service reuse.
5. The API and agent render typed explanations, prerequisites, alternatives, limitations and
   evidence references.

The engine does not create plans, prices, entitlements or quota values. The existing
DeepSeek/LangGraph agent remains the single orchestration layer; recommendation facts are
rendered from verified contracts rather than allowing model text to change eligibility.

## Catalog and evidence

`backend/app/services/service_reference_catalog.py` contains catalog version `v1.0`. It is
small, inspectable and intentionally separate from live Cloud Foundry observations. Entries
include a service name, technical identifier where known, capability category, use cases,
prerequisites, runtime notes, official SAP Help URL, verification timestamp, limitations
and alternatives.

The primary source is the [SAP BTP available technology guide](https://help.sap.com/docs/btp/btp-developers-guide/understanding-available-technology).
Connectivity and Destination prerequisites follow [SAP Connectivity documentation](https://help.sap.com/docs/connectivity/sap-btp-connectivity-cf/consuming-destination-service).
The catalog is not a claim that every service is available in every region or account.

## Eligibility and ranking

Each candidate exposes independent statuses:

- `eligibility_status`: documented suitability after hard checks.
- `runtime_status`: `SUPPORTED`, `UNSUPPORTED`, or `UNKNOWN`.
- `region_status`: unknown unless verified; the catalog does not infer regional coverage.
- `entitlement_status`: provider visibility is never treated as entitlement.
- `quota_status`: `NOT_CHECKED` unless a future authorized evidence source can prove it.

An unknown status is not converted to supported. Unsupported runtime candidates are filtered
from the result. When evidence is incomplete, the response carries missing information and
confidence classifications instead of a fabricated numeric score.

## API and tools

Authorized endpoints:

- `POST /api/v1/btp/service-recommendations/requirements/analyze`
- `POST /api/v1/btp/service-recommendations/recommend`
- `POST /api/v1/btp/service-recommendations/compare`
- `POST /api/v1/btp/service-recommendations/prerequisites`

The frozen ToolRegistry contains:

- `btp_requirements_analyze`
- `btp_service_recommend`
- `btp_service_compare`
- `btp_service_prerequisites_read`

All are read-only, bounded and require the existing BTP read permission. General reference
guidance works with chat authorization; live enrichment requires an authorized active
environment and is limited to the existing Cloud Foundry service-offering read path.

## Security and limitations

No recommendation tool provisions, binds, deletes or changes infrastructure. User text is
treated as untrusted input. Account and environment IDs are server-controlled. A live
provider plan, when visible through the existing inventory connector, is not treated as an
entitlement and is not offered as a suggested plan. Pricing is intentionally omitted unless
verified comparable commercial evidence is added later.

The current catalog has no region matrix, pricing adapter, SAP entitlement adapter or quota
allocation adapter. Those gaps are surfaced as `UNKNOWN`, `NOT_VERIFIED` or `NOT_CHECKED`.
