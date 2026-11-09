# Phase 23 deterministic sizing audit

## Delivered

- Typed sizing requirements, assumptions, scenarios, formulas, calculations, quota impacts and results.
- Decimal-based, bounded conversions for bytes, MB, MiB, GB and GiB.
- Cloud Foundry configured memory, observed memory, process instances and disk calculations.
- Throughput-derived instance counts requiring an explicit sustainable benchmark.
- Logical storage, record growth, retention context, index, backup and replication scenarios.
- Explicit safety margins with zero as the documented default.
- Finite, unlimited, zero and unknown quota handling with environment/source/timestamp metadata.
- Owner-scoped persisted result history and additive migration.
- Architecture Advisor proposal/revision linkage and mismatch warnings.
- Dedicated read-only sizing tools and `BTP_SIZING` agent route.
- React Sizing Calculator with comparison, copy, export and architecture linkage.

## Safety controls

- No user formula is evaluated as Python or executable code.
- Formula IDs come only from the server registry.
- DeepSeek cannot change numerical results or quota facts.
- User count never produces a fabricated capacity recommendation.
- ABAP and specialized SAP sizing boundaries are explicit.
- Quota comparisons reject incompatible environment evidence and preserve unknown/unlimited semantics.
- Persisted records are isolated by authenticated owner; proposal access is re-authorized by the existing Architecture Advisor service.
- No provisioning, scaling, deployment, account mutation or entitlement changes are available.

## Verification matrix

| Area | Evidence |
|---|---|
| Numerical engine | Deterministic Phase 23 tests cover memory, process aggregation, throughput, disk, storage, units, precision and margins |
| Quota semantics | Finite, unlimited, zero, unknown, stale, resize and overflow behavior are explicit |
| Runtime limits | Kyma generic-input boundary, ABAP unsupported response, specialized HANA/integration warnings |
| Agent | Dedicated route and fixed read-only ToolGateway entries |
| Persistence | Additive `sizing_results` migration, owner filter and isolated cleanup fixture |
| Frontend | Requirements, formulas, assumptions, quota impact, comparison, copy/export and architecture link |

## Non-goals

This phase does not provide SAP-certified product sizing, benchmark accuracy without measurements, pricing, plan capacity, CPU entitlement, deployment plans, scaling actions, or infrastructure execution. Those require approved product-specific methods and platform governance.
