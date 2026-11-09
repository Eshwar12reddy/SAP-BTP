# LangGraph orchestration and persistent state

Prompt 2 introduces one LangGraph workflow for the existing single SAP BTP Operations and
Architecture Agent.  `SingleAgent` remains the compatibility execution adapter; the public
application entry point is now `AgentOrchestrator`.

## StateGraph

The compiled graph in `backend/app/agents/workflow.py` is:

```text
START
  -> validate_request
  -> resolve_authorized_context
  -> classify_intent
  -> build_investigation_plan
  -> select_authorized_tools
  -> execute_read_only_tools
  -> evaluate_results
  -> build_response
  -> persist_completion
  -> END
```

Validation and authorization failures conditionally route directly to
`persist_completion`.  The graph has no action node and no dynamic model-to-callable
dispatch.  A general question has an empty SAP tool plan and is answered through the
existing DeepSeek adapter.  BTP, knowledge and diagnosis routes use the existing
`SingleAgent.handle_decision` path, which calls the existing `ToolGateway` and frozen
`ToolRegistry`.  The graph classifies once and passes the validated decision to that adapter;
it does not re-run the classifier.

## Node responsibilities

| Node | Responsibility |
|---|---|
| `validate_request` | Validate the bounded message and ownership identifiers |
| `resolve_authorized_context` | Refresh active grants and recheck chat/BTP context on every new or resumed run |
| `classify_intent` | Deterministic rules first, otherwise strict DeepSeek route classification |
| `build_investigation_plan` | Produce an application-owned route-to-tool allowlist |
| `select_authorized_tools` | Validate the bounded allowlist; model output never supplies tool names |
| `execute_read_only_tools` | Invoke the existing compatibility adapter and secure tool gateway |
| `evaluate_results` | Validate the typed agent result before checkpointing it |
| `build_response` | Advance the response lifecycle; content/evidence were already backend-built |
| `persist_completion` | Persist completed, failed or canceled lifecycle state in the checkpointer |

## Persisted state

`AgentGraphState` in `backend/app/agents/state.py` is the single graph-state contract.  It
contains only:

- sanitized, redacted user text;
- authenticated user and conversation identifiers;
- optional BTP account/environment identifiers;
- status, current step, completed steps and pending steps;
- validated route and routing method;
- bounded application-owned plan;
- safe tool-result summaries without tool payload data;
- backend-owned evidence references;
- stable failure metadata;
- bounded response and a response reference;
- bounded iteration, LLM-call and tool-call counters.

Sessions, principals, SQLAlchemy objects, BTP clients, bearer tokens, credentials and raw
provider responses are runtime context only.  They are not fields in `AgentGraphState` and
cannot be serialized into a checkpoint.  State validation rejects invalid identifiers,
unknown fields, oversized responses, oversized tool summaries and checkpoints larger than
256 KiB.

## PostgreSQL checkpoints

The dependency is `langgraph-checkpoint-postgres>=3.2,<4.0`, currently verified with
`langgraph-checkpoint-postgres 3.2.0` and `langgraph 1.2.19`.  The application uses
`AsyncPostgresSaver` when `LANGGRAPH_CHECKPOINT_ENABLED=true` and
`LANGGRAPH_CHECKPOINT_DATABASE_URL` points to a dedicated checkpoint database.

Normal startup does not call `setup()` and does not fall back to the main application
database.  The controlled setup command is:

```text
python -m app.cli setup-langgraph-checkpoints --yes
```

It refuses non-`*_test` databases unless the operator explicitly adds
`--allow-non-test`.  The command initializes the checkpointer's own tables and migrations;
they are not part of the application's Alembic migration chain.  Tests use a separate
`*_test` database and verify that a checkpoint can be read after closing and reopening the
PostgreSQL saver.

When checkpointing is disabled, the application uses `InMemorySaver` for local development
and tests.  This preserves API behavior without silently mutating a database.  A deployment
that needs restart recovery must provision the dedicated PostgreSQL checkpoint database,
run the explicit setup command, and then enable the backend URL.

## Threads, ownership and concurrency

The thread key is derived server-side as:

```text
agent:{authenticated_user_id}:{conversation_id}
```

Callers never choose an arbitrary thread.  Conversation ownership is checked by the existing
conversation endpoint, and the graph validates the owner in its persisted state before
resuming.  The application also serializes same-thread calls with an in-process lock and
returns a completed state for an identical already-finished message instead of invoking
DeepSeek or tools again.  Existing durable reply claiming remains the cross-worker guard for
the public conversation reply endpoint.

If a worker is interrupted after a node checkpoint but before the graph reaches `END`, a
retry with the same conversation and message resumes the durable `RUNNING` thread from
LangGraph's last saved boundary. A terminal failed run is retried as a new bounded graph
input; completed identical work is returned from its checkpoint without a second call.

The status endpoint is:

```text
GET  /api/v1/conversations/{conversation_id}/investigation
POST /api/v1/conversations/{conversation_id}/investigation/cancel
```

Both routes require the caller's chat permission and conversation ownership.  They return
only `InvestigationStatusRead`, never raw checkpoint contents.  A reply request for a failed
message reruns the same authorized graph thread; every resume refreshes grants first.

## Failure, resume and cancellation behavior

- DeepSeek failures are stored as stable failure metadata, then re-raised through the
  existing API error mapping so the reply endpoint remains compatible.
- Tool failures continue to use the existing typed `ToolResult` statuses and safe response
  rendering.  No raw exception text reaches the checkpoint or client.
- A revoked BTP scope is detected during graph context resolution and immediately before
  each tool call through refreshed active grants.
- Missing environments remain a clarification or unavailable-tool result; the graph never
  substitutes another authorized environment.
- Cancellation sets a runtime cancellation event and a persisted canceled state.  The
  current read-only call is allowed to return, then the next graph checkpoint finishes the
  run as canceled.  Cancellation never enables or performs infrastructure changes.
- `LANGGRAPH_MAX_ITERATIONS` bounds graph progress.  State and response/tool limits bound
  checkpoint size; existing DeepSeek and ToolGateway limits bound model/tool work.
- PostgreSQL pruning is explicit through `prune_checkpoint_threads(..., strategy="keep_latest")`.
  The application does not silently delete checkpoints.  Operators can combine that helper
  with their approved age-based thread inventory and retention process.

## Compatibility with `SingleAgent`

The existing `SingleAgent` constructor, `handle()` method, route behavior, tool names,
authorization checks, evidence footers, conversation persistence and `AgentReplyMetadata`
shape are preserved.  `handle_decision()` is the narrow compatibility seam used by the graph
after `classify_intent`; it prevents a second classification call without duplicating route
execution logic.

The React chat continues to call the same conversation endpoints.  No frontend redesign or
new LLM provider was introduced.

## Prompt 3 memory contract

Prompt 3 may add memory outside this short-term checkpoint state.  It should:

1. keep cross-thread memory in a separate application-owned store, not in checkpoints;
2. retrieve only records authorized for the current principal and current scope;
3. pass memory into prompts through `AgentPromptBuilder` as labeled untrusted data;
4. never place credentials, sessions, principals or raw provider payloads in memory;
5. preserve the current single-agent and `ToolGateway` boundaries.

The current graph deliberately stores no long-term preferences, facts or unrestricted chat
history.

## Prompt 4 approvals contract

Prompt 4 may add an interrupt/pending-action state around a future approval node.  The safe
extension point is after planning and before any future action tool.  It must:

- use LangGraph `interrupt()` with a bounded JSON-serializable pending-action contract;
- reauthorize the user and target scope when resuming;
- validate the four approval options server-side;
- keep all infrastructure-changing tools disabled until that phase explicitly enables them;
- never treat a resume token or thread ID as approval or authorization.

The current graph has no terminal command, write tool or approval bypass.
