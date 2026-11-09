# Phase 19 inventory capability matrix

| Capability | Status | Evidence / boundary |
|---|---|---|
| Registered global account, subaccount, and runtime records | IMPLEMENTED | Existing Phase 11 account/environment tables and catalog tool. Labeled `REGISTERED_CONFIGURATION`; no live SAP hierarchy claim. |
| Optional nested BTP directories | UNSUPPORTED | No directory model or authorized SAP account-discovery contract is configured. The product does not fabricate directory relationships. |
| Cloud Foundry organizations, spaces, applications, processes, stats, service instances, bindings, and usage events | IMPLEMENTED | Existing bounded, fixed-origin Phase 12 V3 connector and authorization mapping. |
| Cloud Foundry service offerings and plans | IMPLEMENTED | Read-only V3 list endpoints; safe metadata only. Results are `UNKNOWN_ENTITLEMENT` because provider catalog visibility is not SAP entitlement assignment. |
| Cloud Foundry organization and space quotas | IMPLEMENTED | Read-only V3 quota endpoints with explicit `value`, `unlimited`, and `unknown` semantics. |
| Organization and space usage summaries | IMPLEMENTED | Read-only usage-summary endpoints; only provider-reported values are exposed. No utilization is inferred from quota limits. |
| SAP Destination Service inventory | UNSUPPORTED / NOT_CONFIGURED | Dedicated adapter boundary and typed status endpoint are present. No live list contract and dedicated credential mapping are configured in this deployment, so no destination data is returned. |
| SAP BTP entitlements | UNSUPPORTED | Separate typed status boundary is present. No supported official contract and dedicated credential mechanism are configured; CF tokens are not reused. |
| Unified live evidence | IMPLEMENTED | API/tool outputs carry provider, environment/connection evidence, observation time, source type, retrieval status, and safe metadata only. |
| Agent routing and tools | IMPLEMENTED | Inventory nouns route to read-only inventory tools; catalog, offerings, plans, quotas, and unavailable SAP-service states are rendered without model-generated resource claims. |
| Frontend inventory view | IMPLEMENTED | Existing BTP connections inventory view now shows offerings, plans, quotas, and explicit Destination/Entitlement statuses. |

## Safety invariants

- Provider GUIDs come only from server-controlled scope mappings; client names do not authorize resources.
- The connector exposes fixed GET paths only and rejects cross-origin pagination.
- Service credentials, binding credentials, application environment variables, destination URLs, headers, custom properties, and entitlement write operations are outside the response contract.
- A visible Cloud Foundry service plan is never presented as an SAP BTP entitlement.
- Missing, null, zero, and unlimited quota values are not collapsed into one number.
