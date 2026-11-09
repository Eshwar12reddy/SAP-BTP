# SAP BTP read-only connectors

Phase 12 adds a server-side, read-only Cloud Foundry V3 connector. It is deliberately
bounded: the backend owns the endpoint, credential lookup, provider scope mapping,
pagination limits, retries, and response shaping. The browser and the agent receive
typed observations only.

## Supported surface

The connector currently supports these GET-only observations:

- organizations and organization details;
- spaces and space details;
- applications and application details;
- application processes and process statistics;
- service instances;
- service bindings without credentials or arbitrary binding data;
- bounded application usage events.

The public API is under `/api/v1/btp/runtime`. Every response carries evidence with
the authorized BTP environment ID, connection ID, observation timestamp, and the
`cloud_foundry_v3` origin. There are no connector endpoints for deployments,
scaling, restaging, service creation, binding changes, deletion, or credential
retrieval.

## Authorization and configuration

Every request rechecks the caller's `btp:read` coverage and the caller's active,
enabled Phase 11 connection. The connection must target the Cloud Foundry runtime,
use a bearer token, and resolve its opaque credential reference in the backend.
The active connection must also pass the configured HTTPS host allowlist.

Provider GUIDs are mapped explicitly by environment UUID in
`BTP_CLOUD_FOUNDRY_SCOPE_MAP`:

```json
{
  "<environment-uuid>": {
    "organization_ids": ["<provider-organization-guid>"],
    "space_ids": ["<provider-space-guid>"]
  }
}
```

Names are display data only and never establish authorization. A missing or invalid
mapping fails closed. Unsupported runtimes, including Kyma and Neo, return a safe
unsupported response; no provider call is attempted.

Recommended production limits are controlled by settings and are intentionally
bounded: maximum pages, maximum results, maximum response bytes, maximum concurrent
connections, and a capped retry delay. Provider responses are not logged or returned
raw. 401, 403, 404, 429, timeout, transport, malformed-response, and 5xx outcomes
are converted into safe application errors. Retry-After is honored only within the
configured cap.

## Provider contract references

SAP BTP supports the Cloud Foundry API V3 surface for Cloud Foundry environments:

- [Cloud Foundry CAPI V3 API reference](https://v3-apidocs.cloudfoundry.org/version/3.133.0/)
- [Organizations](https://v3-apidocs.cloudfoundry.org/version/3.133.0/index.html#organizations)
- [Spaces](https://v3-apidocs.cloudfoundry.org/version/3.133.0/index.html#spaces)
- [Apps and processes](https://v3-apidocs.cloudfoundry.org/version/3.133.0/index.html#apps)
- [Process stats](https://v3-apidocs.cloudfoundry.org/version/3.133.0/index.html#process-stats)
- [Service instances](https://v3-apidocs.cloudfoundry.org/version/3.133.0/index.html#service-instances)
- [Service bindings](https://v3-apidocs.cloudfoundry.org/version/3.133.0/index.html#service-bindings)
- [App usage events](https://v3-apidocs.cloudfoundry.org/version/3.133.0/index.html#app-usage-events)
- [SAP Help: Cloud Foundry environment APIs](https://help.sap.com/docs/btp/sap-business-technology-platform/cloud-foundry-environment)

The implementation follows the provider's pagination links only when they remain on
the same HTTPS origin and under `/v3/`. It never follows redirects.

## Agent and UI behavior

The agent tools are typed, read-only tools with `Permission.BTP_READ` and
`SourceType.LIVE_BTP`. They use the active authorized environment rather than an
environment name supplied by the model. If live data is unavailable, the tool
returns a safe unavailable or denied result and does not fall back to guessed live
state.

The BTP connections page shows a small inventory summary when an active connection
is selected. It displays counts and application names only; no endpoint, token,
service-binding credentials, raw provider payloads, or secrets are rendered.

## Verification and rollout

Mocked transport tests cover pagination, origin rejection, authentication and
permission failures, not-found responses, retry/rate-limit behavior, timeout
mapping, credential resolution, and fail-closed scope mapping. They do not contact a
real provider. Before any live rollout, an operator must configure the exact
provider host allowlist, backend credential reference, and explicit GUID mapping,
then exercise only the read endpoints with a least-privilege Cloud Foundry identity.

Phase 12 does not run Alembic upgrades, modify the main database, deploy to SAP BTP,
or make real provider calls.
