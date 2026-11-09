# Investigations and human approvals

## Scope

Prompt 4 extends the existing single BTP Operations Agent. It does not add another
agent, a general command runner, or a write-capable SAP connector. The investigation
workflow is bounded, read-only, and uses the existing LangGraph graph, Phase 12
Cloud Foundry connector, Phase 13 deterministic diagnosis service, scoped RBAC, and
audit service.

## Investigation flow

```text
authenticated message
  -> grant refresh and BTP-context reauthorization
  -> deterministic/model intent classification
  -> application-owned investigation plan
  -> fixed read-only tool allowlist
  -> Phase 12 evidence collection
  -> Phase 13 deterministic rules
  -> bounded findings, evidence references, gaps and next steps
  -> response and checkpoint
```

The model does not choose tool names, resource scope, or rule outcomes. Diagnosis
findings are adapted from `ApplicationDiagnosisRead` into the serializable
`InvestigationFinding` contract. The rules currently cover stopped applications,
zero or mismatched instances, abnormal processes, repeated crashes, supported health
check failures, deployment-failure correlation only when the same evidence set
supports it, expected service relationships, provider partial failures, healthy
observations, and insufficient evidence.

The investigation status endpoint is:

`GET /api/v1/conversations/{conversation_id}/investigation`

It returns progress, the bounded plan, findings, evidence gaps, recommendations and
the latest approval record. It never starts an AI call or a provider call.

## Human approval lifecycle

An operation request creates one redacted `agent_approvals` row. The canonical
fingerprint includes the authenticated user, conversation, LangGraph thread,
operation type and bounded canonical arguments. The row is unique by fingerprint,
has a fifteen-minute expiry, and is reused on replay. Secrets, bearer tokens,
database sessions and executable shell strings are not stored.

The graph pauses with LangGraph's official `interrupt(value)` API. A terminal
decision resumes the same user-owned thread with `Command(resume=...)`; the server
re-checks ownership, conversation, thread and current approval state before applying
the decision. The decision endpoint is:

`POST /api/v1/conversations/{conversation_id}/investigation/approvals/{approval_id}/decision`

The body must contain exactly one of:

- `execute` — records approval, then returns `execution_unavailable`; no operation runs.
- `cancel` — records cancellation; no operation runs.
- `other_approach` — records the request and leaves the approval pending.
- `explain_command` — returns the safe proposal explanation and leaves it pending.

There is deliberately no executor in this phase. Future execution would require a
narrow, separately authorized action service with its own allowlist, scope checks,
idempotency key, audit record, timeout and postcondition verification. It must not be
implemented by passing arbitrary approval text to a shell or generic SAP client.

## Frontend behavior

Chat renders a human-approval panel only when the backend reports a pending approval.
It shows the proposal summary, risk state, the read-only/no-executor notice and the
four exact decisions. Findings show severity, confidence, explanation and evidence
references. A normal chat reply remains compatible with the existing message API.

## Authorization and memory

Every resumed run refreshes active grants and re-checks the conversation's BTP
context. Revoked scope is denied even if an old checkpoint exists. Approval records
are owner- and thread-bound; a guessed approval ID or another user's conversation is
not sufficient. Findings are checkpoint state and response metadata only. They are
not promoted to long-term memory automatically; the existing explicit memory API is
still required.

## Persistence and deployment

The `agent_approvals` table is added by migration
`20261104_8a1b2c3d4e5f_agent_approvals.py`. Normal application startup does not run
migrations or checkpoint setup. The isolated test database owns its migration and
cleanup. No deployment, real SAP call, or paid LLM call is part of this phase.

## Known limitations

- The existing bounded tool node can replay an in-flight external read after a worker
  interruption; the previously documented per-tool checkpoint limitation remains.
- `explain_command` is intentionally a safe response path that keeps the approval
  pending; it does not resume an executor because none exists.
- Evidence is limited to what Phase 12 exposes. Logs and unsupported platform
  signals remain explicit missing evidence.
