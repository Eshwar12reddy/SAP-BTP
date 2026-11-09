# Final Agent Intelligence and Hardening Audit

Audit date: 4 November 2026 (Asia/Calcutta)

## Executive result

The integrated backend and frontend regression paths are green in the isolated test setup. The
agent remains intentionally read-only for SAP BTP and infrastructure operations. Browser E2E,
live SAP calls and unmocked DeepSeek quality are not verified and are not represented as passed.

## Workflow audit

The implemented path is:

```text
authenticated request
 -> owned conversation and BTP scope
 -> LangGraph thread derived from user + conversation
 -> bounded history/memory assembly
 -> deterministic route or strict DeepSeek fallback
 -> application-owned plan and frozen tool allowlist
 -> server scope check and read-only connector
 -> typed evidence + deterministic diagnosis
 -> optional DeepSeek explanation
 -> response validation and safe persistence
 -> frontend status/history display
```

Sensitive requests pause at a durable owner-bound approval interrupt. Explain and Other Approach
are non-terminal and preserve the pending decision. Cancel is terminal and marks the graph
canceled. Execute is truthful: it records approval but reports `execution_unavailable` because
no executor exists. Terminal replay is rejected.

## Test and evaluation counts

| Measure | Count/result | Interpretation |
|---|---:|---|
| Existing backend tests | 400 passed | Regression baseline against isolated PostgreSQL |
| Prompt 5 deterministic scenarios | 38 passed | Contract, security, routing and diagnosis fixtures |
| Approval integration tests | 4 passed | All four choices plus expiry, malformed and replay coverage |
| Frontend tests | 78 passed | UI/API state behavior with test backend |
| TypeScript | passed | No type errors |
| Frontend build | passed | Production bundle generated |
| Ruff | passed | Python lint clean |
| DeepSeek paid calls | 0 | All evaluation model calls mocked |
| Live SAP calls | 0 | Intentionally not performed |
| Browser E2E | not verified | Browser access host unavailable |

The combined local test inventory after the additions is 440 backend/evaluation tests (400
regression + 38 evaluation + 2 additional approval cases; the approval file has four tests in
the final run). Counts are reported separately to avoid double-counting the approval file in the
regression baseline.

## Quality measurements

The 38-scenario suite is a deterministic pass-rate measure:

- routing and classification contracts: 16/16 passed, 100%
- diagnosis and evidence rules: 9/9 passed, 100%
- state, input and prompt safety: 9/9 passed, 100%
- approval fingerprint behavior: 2/2 passed, 100%
- mocked DeepSeek request contract: 1/1 passed, 100%
- sanitizer boundary: 1/1 passed, 100%

These rates measure implementation contracts and deterministic rules. They do not measure model
reasoning quality in production. No real-world labeled set was available, so real DeepSeek
classification accuracy, recommendation quality and latency are unverified.

## Four-option approval audit

| Choice | Verified behavior |
|---|---|
| Execute | Exact proposal only; ownership/thread/expiry checks; no executor; truthful non-execution result |
| Cancel | No operation; audit record; approval canceled; graph canceled; replay rejected |
| Other Approach | No execution; audit record; original proposal remains pending; separate authorization required for any new proposal |
| Explain Command | No execution; audit record; pending proposal is preserved |

Adversarial coverage includes cross-user access, malformed decisions, expired approval, terminal
replay, forged model classification, prompt injection, bounded tool arguments, revoked scopes in
the existing regression suite, and deterministic fingerprint changes for modified canonical
arguments.

## Evidence and memory audit

Observations are generated from typed connector responses and carry bounded evidence references.
The diagnosis layer distinguishes observations from causes and uses `CONFIRMED`, `LIKELY`,
`POSSIBLE` and `UNKNOWN` conservatively. Long-term memory is PostgreSQL data, not checkpoint
state or RAG; retrieval filters by owner and BTP scope, applies expiry/deletion, and marks
retrieved material untrusted. The existing memory integration suite covers owner isolation,
revocation, expiry, redaction, source references and deletion.

## Security audit

No credentials were added to prompts, checkpoints or memory. The registry is frozen, tools are
selected by code, environment scope is server-derived, and no arbitrary terminal executor is
reachable from the API or model output. Reply and approval persistence are idempotency-bound.
The residual replay risk is upstream read-only HTTP repetition after an interrupted process;
exactly-once external execution is not claimed.

## Evidence-backed scores

Scores are out of 10. Scores marked “mocked/deterministic” are not live-system intelligence
scores.

| Dimension | Score | Basis |
|---|---:|---|
| Architecture | 8.5 | Single-agent boundaries and compatibility tests; mocked/deterministic |
| LangGraph state | 8.5 | State bounds, ownership, interrupt/resume and checkpoint tests |
| Short-term memory | 8.0 | Conversation assembly and compaction tests; deterministic |
| Long-term memory | 8.0 | PostgreSQL isolation/expiry/deletion tests; no production load test |
| DeepSeek integration | 7.0 | Client/error/strict-output tests; real reasoning unverified |
| Tool orchestration | 8.5 | Frozen registry, gateway, scope and budget tests |
| SAP BTP diagnosis | 8.5 | Deterministic connector fixtures and diagnosis rules |
| Evidence reliability | 8.0 | Backend-derived evidence and confidence validation |
| Security | 8.5 | RBAC, redaction, injection, checkpoint and executor-boundary tests |
| Human approval | 8.5 | Four choices, expiry, replay and ownership tests; no executor by design |
| Error recovery | 7.5 | Retry/claim/resume/failure tests; upstream idempotency remains limited |
| Frontend integration | 7.0 | 78 UI tests, API contracts and build; browser E2E not verified |

Overall agent readiness: **8.0/10 for the supported read-only, test-fixture-backed scope**.

This is not production-readiness certification. The score is reduced primarily by missing browser
E2E, absent live SAP verification, unmeasured real DeepSeek quality, and the documented
at-least-once possibility for interrupted read-only upstream calls.

## Remaining limitations and closure status

Completed: architecture integration, LangGraph workflow, bounded state, memory scoping,
deterministic diagnosis, frozen tools, truthful approvals, approval replay hardening, regression
tests, evaluation suite and documentation.

Not verified: browser workflow, live SAP infrastructure, real DeepSeek intelligence metrics, and
exactly-once semantics for an interrupted external request. These limitations are explicit and
should remain visible to operators.

## Manual browser verification procedure

Run this only against an isolated database whose name ends in `_test`; do not point it at the
main application database.

1. Start the repository PostgreSQL service and create/migrate an isolated test database using
   the existing test configuration. Start FastAPI with `DATABASE_URL` set to that test database
   and start the Vite dev server on `http://127.0.0.1:5173`.
2. Provision a test administrator through `python -m app.cli create-admin --password-stdin`, then
   use the existing admin UI to create a test viewer and grant `chat:use`. Add a test BTP account,
   subaccount/environment, and a `btp:read` grant if the catalog route is being checked.
3. Open `http://127.0.0.1:5173`, log in as the test viewer, create a conversation, and select the
   authorized context. Send `List my BTP accounts`; verify the response appears, the conversation
   remains after refresh, and the message/reply API calls return the same stored records on retry.
4. Send a follow-up such as `Which environments can I access?`; verify the selected conversation
   context remains correct. Use an environment inventory or diagnosis request only when a mocked
   read-only connector is configured for the test environment.
5. Send `Restart the orders application`; verify the approval panel says no infrastructure change
   was made. Exercise Explain Command and Other Approach and verify the approval remains pending;
   exercise Cancel and verify canceled status; exercise Execute and verify
   `execution_unavailable`, never a success claim.
6. Refresh while approval is pending, repeat a terminal decision, and verify the decision is
   rejected as already decided. Revoke the test viewer's grant and verify subsequent status or
   resume requests show a useful permission error.

Because the browser bridge was unavailable during this audit, these steps are reproducible
instructions, not evidence that the browser workflow passed.
