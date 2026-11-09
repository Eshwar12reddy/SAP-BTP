# Phase 22 Architecture Advisor Audit

## Scope

Phase 22 implements a complete SAP BTP AI Architecture Advisor over the Phase 21 service recommendation foundation. It covers typed requirements, deterministic proposal generation, official evidence, safe diagrams, validation, persistence, revisions, comparison, agent routing, tools and the React page.

## Safety and authorization

- No deployment, provisioning, entitlement assignment, account mutation or infrastructure change is performed.
- No price, plan, capacity, quota, region or entitlement is invented.
- Account/environment context is re-authorized through existing BTP read coverage before account-specific work.
- Proposal retrieval and revision are owner-scoped; revisions are immutable rows.
- Diagram input is server generated and rejects script, click-action, initialization and HTML constructs.
- Components, dependencies, connections and data flows must reference valid component IDs.
- Assumptions and unknowns remain explicitly labeled and are not confirmed memory.

## Delivered surfaces

- Backend schemas: `app/schemas/architecture.py`
- Backend service: `app/services/architecture_advisor_service.py`
- API router: `app/api/v1/architecture_advisor.py`
- Tool gateway entries in `app/tools/btp.py`
- `BTP_ARCHITECTURE` route and single-agent rendering
- `architecture_proposals` model and Alembic migration
- React route `/architecture` and `ArchitectureAdvisorPage`
- User and agent audit events for proposal generation
- This design document and audit record

## Verification matrix

| Area | Verification |
|---|---|
| Requirement extraction | Explicit, assumption and unknown categories plus clarification questions |
| Service identity | Components are selected from the frozen Phase 21 catalog |
| Proposal integrity | Duplicate IDs and dangling references are rejected |
| Diagram safety | Only server-generated Mermaid flowcharts are accepted |
| Commercial safety | Cost considerations contain no invented price figures |
| Authorization | Account/environment access is rechecked at generation and retrieval |
| Persistence | Proposal revisions are append-only and owner scoped |
| Agent safety | Architecture tools are fixed read-only registry entries |
| Frontend | Requirements, diagram, services, environments, security, risks, evidence, revise, compare, copy and export are exposed |

## Non-goals

Phase 22 does not calculate production sizing, quote pricing, enable entitlements, prove service availability in a region, deploy changes, or replace SAP architecture governance review.
