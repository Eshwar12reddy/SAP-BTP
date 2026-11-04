# Authentication, RBAC and BTP Access Control (Phase 2)

This phase adds sign-in, server-side sessions, four fixed roles, BTP scope authorization, administration, and a security audit trail. It also protects the Phase 1 chat with these controls. There is still no live SAP BTP integration and no AI.

## Authentication

| Item | Implementation |
|---|---|
| Accounts | Created by administrators only (`POST /api/v1/admin/users`). The first platform administrator is created once with the CLI. There is no self-registration. |
| Passwords | Argon2id (m=64 MiB, t=3, p=4), rehashed on login if parameters change. New passwords must be 12–128 characters and must not equal the email. Hashing runs in a worker thread. |
| Login | `POST /api/v1/auth/login`. Unknown email, wrong password and disabled account all return the same 401 `invalid_credentials`, with equalized timing (a dummy verify for unknown users). |
| Rate limiting | Database-backed, with no Redis: 5 failures per email (since its last success) or 20 per IP within 15 minutes, then 429 with `Retry-After`. Emails are stored only as SHA-256 fingerprints. Wrong current passwords on password change count too. |
| Access token | HS256 JWT, 15 minutes. `iss`, `aud`, `sub`, `sid`, `jti`, `iat`, `nbf`, `exp` and `typ=access` are all required and validated; the algorithm is pinned. Every request also checks the server-side session and user status, so logout, revocation and disabling take effect immediately. |
| Refresh token | 256-bit random value in an **HttpOnly, Secure, SameSite=Strict** cookie scoped to `/api/v1/auth`. Only its SHA-256 is stored. |
| Sessions | PostgreSQL rows with a 7-day idle and a 30-day absolute lifetime. Each refresh extends idle expiry, but never past the absolute limit. |
| Rotation | Every refresh issues a new refresh token and marks the old one used, inside row locks (`SELECT … FOR UPDATE`). |
| Replay detection | A used token presented again revokes the whole session (`auth.refresh.reuse_detected`). Within a 10-second grace window it instead returns 409 `refresh_in_progress` and issues nothing. This is the concurrent-refresh case, not theft. |
| CSRF | Cookie-authenticated endpoints (refresh, logout) require all of: SameSite=Strict cookies, a trusted `Origin`, and a double-submit `X-CSRF-Token` header that matches the readable `btp_csrf` cookie *and* the hash stored on the session. Bearer-token endpoints are not CSRF-prone. |
| Logout | `logout` ends the current session (via the access token, or via the refresh cookie plus CSRF when the access token has expired). `logout-all` ends every session of the user. A password change ends all *other* sessions. |
| Browser storage | The access token lives in JavaScript memory only. Nothing sensitive is written to localStorage; it holds only the chosen chat context. |

## Roles and permissions

Definitions live in [backend/app/auth/permissions.py](../backend/app/auth/permissions.py). Code checks permissions, never role names. The `roles`, `permissions` and `role_permissions` tables are a seeded mirror, and a test fails if they drift.

| Role | Permissions | Allowed scopes |
|---|---|---|
| Viewer | `chat:use`, `btp:read` | global account, subaccount, environment |
| Operator | Viewer + `operations:request`, `operations:execute_low_risk` | same |
| Approver | Viewer + `approvals:read`, `approvals:decide` | same |
| Administrator | `users:read/create/update/disable`, `grants:manage`, `sessions:revoke`, `audit:read` | platform, global account, subaccount |

Administrators get **no** BTP data and no chat unless they are also granted a BTP role. Operations and approvals have permissions defined, but their workflows (including "never approve your own request") belong to a later phase. Everything is denied by default: no grant, no access.

## BTP scope model

The configured hierarchy is *global account → subaccount → environment*. It is registered with the CLI and stored in `btp_accounts` / `btp_environments`. Names such as "Development" are **labels on configured environments**, not native BTP objects. Database constraints guarantee that subaccounts have a global-account parent and that environments live in subaccounts.

A request needs the permission **and** an active grant whose scope covers the resource:

- A grant covers exactly the account or environment it names.
- `include_descendants = true` (opt-in per grant) also covers child subaccounts and their environments.
- A grant never covers a parent. Environment-only users cannot see their subaccount, and its ID is not disclosed.
- Revoked or expired grants count for nothing, and grants are re-read on every request.
- Resources outside scope return **404**, identical to a nonexistent ID, and the denial is audited.
- List endpoints filter by scope in SQL **before** `LIMIT/OFFSET`.

Grant integrity is enforced by the database. Scopes are real foreign keys, never free-form IDs. A composite FK makes `scope_type` match the account's actual type, and the role/scope combination is checked. A partial unique index allows only one active grant per user, role and scope.

## Administration and delegation

| Action | Platform admin | Delegated admin (account scope) |
|---|---|---|
| List / view users | all | only users holding a grant inside the delegation |
| Create, update, disable / enable, revoke sessions | ✔ | ✘ (these affect the user everywhere) |
| Assign / revoke Viewer, Operator, Approver | any scope | scopes inside the delegation only |
| Assign / revoke Administrator | ✔ | ✘ |
| Audit log | all events | events whose scope is inside the delegation |

Nobody can change their own grants, status or sessions through the admin API. Because of that, and because only platform admins can touch admin grants, the last platform administrator cannot be removed. Disabling a user revokes all of their sessions in the same transaction.

## Chat protection

- Conversation endpoints require `chat:use`. The owner is always the authenticated user; a client-supplied owner is rejected (422).
- Other users' conversations return 404.
- **Legacy conversations** (created before authentication, `owner_id IS NULL`) are preserved but invisible to everyone. Assign them explicitly with `python -m app.cli assign-legacy-conversations`. The migration does not give them to the first administrator.
- A conversation can have an optional BTP context: one account or environment the user can read. On every access that context is re-authorized. If the grant is revoked, the conversation disappears from the list and direct access returns 403 `btp_scope_revoked`. The record itself is kept.

## Audit events

Table `audit_events` records each event's actor, target, outcome (`success` / `failure` / `denied`), scope, request ID, IP and non-sensitive details. Admin changes are written in the same transaction as the change; failed logins and denials use their own transaction. Passwords, tokens and message contents are never recorded.

Events: `auth.login.success|failure|rate_limited`, `auth.logout`, `auth.logout_all`, `auth.refresh.reuse_detected`, `auth.password.changed|change_failed`, `admin.user.created|updated|disabled|enabled`, `admin.grant.assigned|revoked`, `admin.sessions.revoked`, `admin.bootstrap`, `admin.btp_mapping.changed`, `admin.legacy_conversations.assigned`, `authz.access_denied`.

## API

| Method | Path | Auth |
|---|---|---|
| POST | `/api/v1/auth/login` | — |
| POST | `/api/v1/auth/refresh` | refresh cookie + `X-CSRF-Token` |
| POST | `/api/v1/auth/logout` | bearer, or refresh cookie + CSRF |
| POST | `/api/v1/auth/logout-all` | bearer |
| GET | `/api/v1/auth/me` | bearer |
| POST | `/api/v1/auth/change-password` | bearer |
| GET | `/api/v1/users/me/permissions`, `/api/v1/users/me/scopes` | bearer |
| GET/POST/PATCH | `/api/v1/admin/users`, `/admin/users/{id}`, `/admin/users/{id}/disable` / `enable` / `sessions/revoke` / `grants`, `/admin/grants/{id}/revoke`, `/admin/scopes`, `/admin/audit-events` | bearer + administration permissions |
| GET | `/api/v1/btp/accounts`, `/btp/accounts/{id}`, `/btp/accounts/{id}/environments`, `/btp/environments/{id}/applications` | bearer + `btp:read` on the resource |
| * | `/api/v1/conversations…` (Phase 1 endpoints) | bearer + `chat:use` |

`/btp/environments/{id}/applications` goes through the adapter in [backend/app/integrations/sap_btp/provider.py](../backend/app/integrations/sap_btp/provider.py). With no live integration configured, an *authorized* call returns 503 `btp_integration_unavailable`. Nothing is fabricated.

## Operator commands

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m alembic upgrade head

# One-time: the first platform administrator (prompts for the password twice)
python -m app.cli create-admin --email admin@example.com --display-name "Platform Admin"

# Configured BTP mapping (ids are the GUIDs from the BTP cockpit)
python -m app.cli btp add-global-account --external-id <global-account-guid> --name "Acme Global" --region eu10
python -m app.cli btp add-subaccount --external-id <subaccount-guid> --name "Acme EU" --parent <global-account-guid>
python -m app.cli btp add-environment --account <subaccount-guid> --name Development --runtime cloudfoundry --external-runtime-id <cf-org-guid>
python -m app.cli btp list

# Give pre-authentication conversations an explicit owner
python -m app.cli assign-legacy-conversations --email someone@example.com --all
```

Then sign in at http://localhost:5173 as the administrator, create users under **Administration**, and assign roles and scopes.

## Testing

- Backend: `python -m pytest`. Tests run against the isolated `btp_agent_test` database and cover login, rate limiting, JWT validation, rotation, replay, concurrent refresh, CSRF, logout, the four roles, grant expiry and revocation, Development/Testing/Production scopes, cross-account access, inheritance, enumeration, database constraints, delegated administration, escalation, conversation isolation, the CLI and audit records.
- Frontend: `npm test` covers login, protected routes, renewal and single-flight refresh, no retry loops, logout, role-aware navigation, the context selector, profile and user management. Run `npm run build` too.

## Known limitations and remaining risks

- The client IP comes from the TCP peer. Behind a reverse proxy, configure trusted forwarded headers before relying on per-IP limits.
- The login lockout is per email, so an attacker can lock out a known account for 15 minutes (a deliberate trade-off against brute force).
- No MFA, SSO/OIDC, password reset by email, or forced password change after an admin sets the initial password.
- Delegated administrators cannot discover users who have no grant in their scope; a platform administrator has to make the first grant.
- Login-attempt and audit tables are not pruned yet.
- No live SAP BTP integration; the BTP hierarchy is configured manually.
