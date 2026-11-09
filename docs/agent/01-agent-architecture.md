# Agent architecture

This document describes the single SAP BTP Operations and Architecture Agent after the
architecture-boundary refactor.  The refactor preserves the existing conversation API,
reply metadata, tool names, authorization behavior and DeepSeek-only provider policy.

## Current request dependency map

```text
POST /api/v1/conversations/{conversation_id}/messages/{message_id}/reply
  |
  +-- ChatUser dependency
  |     +-- JWT/session authentication
  |     +-- fresh active grants from PostgreSQL
  |     +-- chat:use permission
  |
  +-- conversation_service
  |     +-- owner lookup (other users are 404)
  |     +-- BTP context reauthorization
  |     +-- durable reply claim / retry state
  |
  +-- AgentOrchestrator.handle(AgentRequest, AgentContext)
        |
        +-- sanitize + normalize user text
        +-- router.route_request
        |     +-- deterministic route rules first
        |     +-- otherwise AgentPromptBuilder -> DeepSeekClient
        |     +-- strict RouteClassification validation
        |
        +-- fixed route behavior
        |     +-- operation request -> refusal, no tool/model call
        |     +-- catalog -> ToolGateway -> frozen ToolRegistry -> btp_catalog_read
        |     +-- applications/diagnosis -> ToolGateway -> scoped BTP tools
        |     +-- knowledge -> ToolGateway -> scope-filtered retrieval
        |     +-- general/documentation/architecture/sizing -> AgentPromptBuilder -> DeepSeekClient
        |
        +-- AgentResponseBuilder
              +-- backend-owned source/evidence metadata
              +-- stable AgentResponse compatibility shape
              +-- source footer written by application code

  +-- AgentReplyMetadata.from_response
        +-- safe metadata stored with the assistant message
        +-- conversation_service persists the reply atomically
```

The model never grants authorization, chooses an arbitrary callable, selects a BTP target,
or receives credentials.  Every BTP tool rechecks the resource against the authenticated
principal's current coverage.  The conversation endpoint rechecks the conversation's BTP
context before the agent starts.

## Architecture and responsibilities

| Responsibility | Current boundary | Responsibility retained |
|---|---|---|
| Orchestration | `app.agents.orchestrator.AgentOrchestrator` with the compatibility implementation in `single_agent.py` | One agent, route budgets, fixed route behavior |
| LLM adapter | `app.integrations.deepseek_client.DeepSeekClient` | DeepSeek only; bounded input/output, timeout, retry, rate and concurrency limits |
| Agent state | `app.agents.contracts` and `app.agents.state` | Frozen request/response contracts and serializable identifier-only run state |
| Prompt builder | `app.agents.prompt_builder.AgentPromptBuilder` delegating to `app.safety.input_guard` | Untrusted-content blocks, redaction, content bounds and injection signals |
| Route/tool selection | `app.agents.router` | Rules first; model classification is a validated label only |
| Tool gateway | `app.agents.tool_gateway.ToolGateway` | Per-run call budget, bounded JSON arguments/results, registry-only execution |
| Tool registry/executor | `app.tools.registry.ToolRegistry` | Frozen registration, permission checks, input/output validation, timeouts, redaction and denial audit |
| BTP tools | `app.tools.btp` plus Phase 12/13 services | Current-scope reauthorization and read-only provider boundaries |
| Response/evidence | `app.agents.response_builder` and `app.agents.evidence` | Backend-owned source labels, live-check claims and compatibility response fields |
| Memory | `app.services.conversation_service` | Owner checks, durable reply state, idempotent replies and reauthorization on reads |
| Authorization context | `app.agents.contracts.AgentContext` | Server-derived principal, session/provider dependencies and selected BTP context |
| Audit/diagnostics | `app.services.audit_service`, safety modules and structured logging | Stable event codes, no secret/input text in logs |

`AgentContext` is deliberately an execution-only dataclass.  `AgentRunContext` is the safe
serializable form and contains only user/conversation/BTP identifiers.  Database sessions,
principals, providers, HTTP clients, settings and secrets are never serialized into agent
state.

## Contracts

The following contracts are now explicit:

- `AgentRequest`: bounded user message and optional conversation identifier.
- `AgentResult`: represented by the existing public-compatible `AgentResponse`; the
  backend-owned `evidence` field is excluded from the legacy API/storage dump.
- `AgentRunContext`: bounded identifier-only run state.
- `AgentError`: bounded stable diagnostic code/message with a retryable flag.
- `ToolCallRequest`: validated tool name and bounded JSON arguments.
- `ToolCallResult`: registry-produced result after validation/redaction.
- `EvidenceReference`: bounded source/tool evidence derived by backend code.
- `InvestigationContext`: bounded environment/application/evidence target context.

Existing callers may continue importing `AgentRequest`, `AgentContext`, `AgentResponse`,
route enums and result types from `app.agents.contracts`.  Existing reply JSON remains
unchanged; internal evidence is available to orchestration and tracing without being added
to the persisted `AgentReplyMetadata` contract.

## DeepSeek behavior

`DeepSeekClient` remains the only hosted chat provider.  It owns the official-host validation,
API-key handling, model selection, message validation, context/input limits, output-token
limit, timeout deadline, retry policy, rate limit and concurrency semaphore.  DeepSeek output
is parsed into `DeepSeekResult`; route JSON is separately validated by `RouteClassification`,
and free text is passed through `check_answer` before it is stored.  Provider response bodies,
credentials, reasoning content and user data are not copied into exception messages or logs.

The orchestrator makes at most two model calls and two tool calls for one request.  The tool
gateway adds a bounded JSON request/result boundary before the frozen registry.  Cancellation
and request deadlines are still owned by the existing FastAPI reply timeout and DeepSeek
client deadline.

## Existing code reused

- `app.agents.router` for deterministic routing and strict classification.
- `app.integrations.deepseek_client` for all DeepSeek HTTP behavior.
- `app.safety.input_guard` and `app.safety.output_validation` for prompt/output safety.
- `app.tools.registry` and existing BTP/knowledge tools for execution and authorization.
- `app.services.conversation_service` for conversation ownership and durable memory.
- `app.schemas.agent` for the existing stored reply metadata and API response.
- `app.services.audit_service` for access-denial and operational audit records.

No new provider, agent, database table, migration, or deployment behavior was introduced.

## Known limitations

- The agent remains deliberately single-turn: prior conversation messages are not sent to
  DeepSeek.  This prevents revoked or newly unauthorized historical content from entering a
  later prompt, but follow-up questions need their relevant context repeated.
- Live SAP BTP connectivity remains read-only and dependent on the configured Phase 12
  connector.  No real SAP calls are made by the test suite.
- Knowledge retrieval currently returns backend-verified extractive evidence rather than
  sending document text to DeepSeek for synthesis.
- LangGraph is a dependency and a future orchestration option, not a runtime dependency of
  the current request path.

## LangGraph integration points

If a later prompt adopts LangGraph, the safe graph boundary is:

1. State: `AgentRunContext`, `InvestigationContext`, routing decision and bounded evidence
   references only.
2. Nodes: input normalization, route selection, tool gateway invocation, response/evidence
   building and persistence.
3. Checkpoints: conversation ownership and current authorization must be revalidated before
   each tool node; a checkpoint must not contain sessions, principals, credentials or raw
   provider payloads.
4. Provider node: call the existing `DeepSeekClient`; do not add another chat provider.
5. Tool node: call `ToolGateway`, never a dynamically resolved function or model-provided
   import path.
6. Terminal node: call `AgentResponseBuilder` and `conversation_service`; preserve the
   existing idempotent reply claim and API metadata.

This keeps LangGraph as an orchestration implementation detail while the authorization,
tool and persistence boundaries remain application-owned.
