# BTP environment and connection management

Phase 11 adds a secure management layer for SAP BTP connection metadata and a
per-user active environment selection. It does not add SAP write operations or
perform live SAP calls by default.

## Data model

`btp_connections` stores the display name, authorized BTP account/environment,
HTTPS API endpoint, authentication method, enabled state, revision, and
validation status. It stores only an opaque `credential_reference`; it never
stores a token, client secret, certificate, or password. There is at most one
connection per BTP environment.

`btp_active_connections` stores the currently selected connection for each user.
The selection is checked against the user's current BTP grant on every read and
handoff, so revocation or expiry takes effect without waiting for a cache.

## Credential resolution

Clients may submit references such as `env://cf-local`. The backend resolves
that reference only through the server-controlled `BTP_CREDENTIAL_ENV_MAP`
settings mapping, for example:

```dotenv
BTP_CREDENTIAL_ENV_MAP={"cf-local":"BTP_CF_LOCAL_TOKEN"}
```

The secret is read from the process environment only when trusted backend code
needs it. It is never returned by an API response, written to PostgreSQL, or
included in audit details. Unregistered references and missing environment
variables produce safe categories rather than secret material.

## Authorization and endpoints

The existing grant-based authorization model scopes all connection and
environment operations to the user's authorized BTP environment. The new
permissions are:

- `btp:connections:read`
- `btp:connections:create`
- `btp:connections:update`
- `btp:connections:validate`
- `btp:environment:select`

The API is under `/api/v1/btp`:

- `GET/POST /connections`
- `GET/PATCH /connections/{id}`
- `POST /connections/{id}/disable`
- `POST /connections/{id}/validate`
- `GET /environments/available`
- `GET/PUT /environments/active`

Responses deliberately expose `credential_configured` rather than the opaque
reference itself. Updates use a revision value to prevent lost changes.

## Validation safety

Configuration validation is local. Live validation is opt-in through
`BTP_LIVE_VALIDATION_ENABLED=false` and is currently limited to mocked/tested
Cloud Foundry bearer-token validation at `/v3/info`. Requests use HTTPS,
certificate verification, bounded timeouts, bounded response consumption,
limited retries, no redirects, and safe error categories. Live validation
requires an explicit host allowlist through `BTP_VALIDATION_ALLOWED_HOSTS`;
obvious loopback, private,
link-local, metadata, reserved, multicast, and unspecified IP targets are
rejected.

Kyma and ABAP connections can be registered as metadata but are not live
validated by this phase. Neo is reported as unsupported. Phase 11 does not make
SAP provider calls during tests.

## Phase 12 handoff

Trusted backend code may call
`btp_connection_service.authorized_connection_context(...)` after request
authentication. It returns a context containing only the already-authorized
connection identity, runtime, endpoint, opaque reference, timeout, retry
limits, and a server-side resolver. Phase 12 can resolve credentials at the
last responsible moment for a specific allowlisted operation; the context is
not an API response and is not serialized.

## Migration and testing

Migration `20261104_d9f34c1b7a62_btp_connections.py` is additive and depends on
the Phase 10 head. Test fixtures apply it only to a database whose name ends in
`_test`; cleanup truncates the new tables before each test. The production
database and the older unattached PostgreSQL volume are not used by the Phase
11 test suite.
