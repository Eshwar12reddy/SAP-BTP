# Deterministic SAP BTP sizing

Phase 23 adds a server-controlled sizing engine for preliminary, reproducible resource calculations. It is a calculation aid, not SAP-certified sizing, a benchmark, a quotation, or a deployment plan.

## Architecture

`SizingRequirements` is the only numerical input contract. Inputs are bounded, unit-tagged and provenance-aware. `SizingService` applies the versioned `phase23-v1` formula registry and returns `SizingResult` with formulas, intermediate calculations, assumptions, warnings, missing inputs, observation timestamps and quota semantics. DeepSeek is not used as numerical truth.

Results may be persisted in `sizing_results` for owner-scoped history and proposal association. Persisted results retain the formula version and original requirements so later formula changes remain interpretable.

## Formula registry

| Formula | Calculation |
|---|---|
| `cf.configured_memory` | Sum of memory per process instance × process instance count |
| `cf.observed_memory` | Measured memory per instance × instance count; marked observed and kept separate from configured allocation |
| `capacity.instances` | Ceiling(peak RPS ÷ measured sustainable RPS per instance), then explicit redundancy and safety margin |
| `cf.configured_disk` | Sum of configured disk per process instance × process instance count |
| `storage.logical` | Initial logical volume + explicit growth over the projection period |
| `storage.records` | Expected records × average stored bytes per record |
| `storage.provisioned` | Logical storage × explicit index-overhead factor |
| `storage.backup` | Logical storage × explicit backup factor |
| `storage.replication` | Logical storage × explicit replication factor |
| `quota.impact` | Existing comparable usage + net proposed allocation; remaining capacity and finite utilization |

No safety margin is applied unless the user supplies one. Existing-resource resize mode adds only the positive delta and avoids double counting.

## Units and evidence

Memory and disk accept bytes, MB, MiB, GB and GiB. Conversion uses exact decimal factors: MB = 1,000,000 bytes; MiB = 1,048,576 bytes; GB = 1,000,000,000 bytes; GiB = 1,073,741,824 bytes. Quota comparisons convert both sides to a common unit before calculation.

Inputs are labeled `USER_PROVIDED`, `OBSERVED`, `DERIVED`, `ASSUMED` or `UNKNOWN`. Quotas are `FINITE`, `UNLIMITED`, `ZERO` or `UNKNOWN`; unlimited and unknown quotas never receive a misleading percentage. Provider observations retain environment, source and observation time. Stale quota evidence is warned about.

## Supported runtime boundaries

- Cloud Foundry: configured process memory, instances, disk allocation, throughput scenarios and compatible quota dimensions.
- Kyma: only explicit Kubernetes-style inputs supplied through the generic typed contract; Cloud Foundry quota semantics are not reused.
- ABAP environment: recognized but unsupported for invented capacity or commercial-unit calculations; official product-specific sizing is required.
- HANA Cloud: explicit logical storage scenarios are supported; complete HANA memory, compute, performance and licensing sizing is not claimed.
- Integration and messaging: only explicit request/message throughput or retention inputs are calculated; service plan capacities and pricing are not invented.

Official boundaries and concepts are informed by the [Cloud Foundry V3 API](https://v3-apidocs.cloudfoundry.org/), [SAP BTP documentation](https://help.sap.com/docs/btp), and SAP's [sizing overview](https://www.sap.com/about/benchmark/sizing.what-is-sizing.html).

## API and tools

API prefix: `/api/v1/btp/sizing`.

- `POST /requirements/analyze`
- `POST /calculate`
- `POST /compare`
- `GET /` and `GET /{result_id}`
- `POST /architecture/{proposal_id}/calculate`

Tool Gateway entries:

- `btp_sizing_requirements_analyze`
- `btp_resource_sizing_calculate`
- `btp_sizing_scenarios_compare`

The dedicated `BTP_SIZING` route runs requirements interpretation and deterministic calculation. The agent renders returned formulas and values; it cannot execute formulas, scaling, provisioning or infrastructure changes.

## Architecture Advisor integration

Sizing can be linked to an authorized Phase 22 proposal revision. The proposal is loaded through the existing owner-scoped service, never modified, and a revision mismatch is reported. Components without explicit sizing inputs produce missing-input findings instead of fabricated capacity.

## Frontend behavior

The Sizing Calculator exposes workload, instance, memory, disk, storage, growth, safety-margin and quota inputs. It displays formula breakdowns, provenance limitations, quota fit, missing evidence and freshness warnings. It supports deterministic double-traffic comparison, copying and report export. The Architecture Advisor links to the calculator with its proposal ID. No visible action changes infrastructure.

## Limitations

User count alone does not determine memory, CPU, instance count or database capacity. CPU entitlement is not inferred from memory. Cloud Foundry disk allocation is not treated as persistent database storage. No prices, plans, quotas, entitlements or provider capacity are invented. Live quota enrichment requires an authorized provider context and is separate from user-provided quota assumptions.
