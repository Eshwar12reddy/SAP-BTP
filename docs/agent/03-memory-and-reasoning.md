# Agent memory and DeepSeek reasoning

Prompt 3 extends the existing single BTP operations agent without creating a second
history, agent, vector store or authorization system.

## Short-term conversation memory

Conversation messages remain the canonical short-term history.  Before each reply,
`app.agents.context.assemble_context` loads the caller's newest bounded messages in
sequence order, preserves `user` and `assistant` roles, removes the current message from
the prior-history block, and compacts older turns deterministically.  Existing message
idempotency (`conversation_id + client_message_id`) and reply claiming prevent duplicate
stored turns and duplicate persisted replies.

The assembled runtime bundle contains:

- recent conversation turns;
- safe summaries of prior tool outcomes from assistant metadata;
- authorized long-term memories;
- a generic indication that the conversation has a server-authorized BTP context.

Resource UUIDs remain in trusted `AgentContext`/`AgentGraphState` for authorization but are
not placed in DeepSeek prompts.  The graph stores only the selected message and memory IDs,
not a second copy of conversation text.  On refresh or graph resume, the service queries
the database again, so the persisted messages and LangGraph state cannot silently drift.

## Context construction and safety

`SingleAgent.classify` and model-answer generation now pass the assembled bundle through the
existing `AgentPromptBuilder` and `build_messages` safety boundary.  The current request is
the first data block; history, tool findings, memories and BTP-context metadata are later
`<untrusted_content>` blocks.  Retrieved content cannot change system instructions, choose a
tool, grant access or change a target.

Context is bounded by `AGENT_CONTEXT_MAX_MESSAGES`, `AGENT_CONTEXT_MAX_TOKENS` (using a
conservative four-character/token estimate), `AGENT_CONTEXT_MAX_CHARS`, the memory result
and memory-character limits, the existing prompt data ceiling, and DeepSeek input limits.
Compaction retains the newest turns in their original order and preserves their IDs in
runtime/graph metadata.  Tool payloads are not copied into the context summary.

## Long-term memory persistence

`agent_memories` is separate from `messages`, LangGraph checkpoints, knowledge documents and
raw SAP responses.  It is added by the explicit Alembic migration
`20261104_7f24e8a1c9d0_agent_memories.py`; normal startup does not create or migrate it.

Each record contains an owner, optional BTP account/environment scope, category, redacted
bounded content, content hash, source conversation/investigation reference, bounded evidence
references, verification status, schema version, revision, timestamps and expiry/deletion
markers.  A partial unique index makes active identical memories idempotent under retries and
concurrent inserts.

The supported categories are `preference`, `verified_finding`, `resource_reference` and
`incident_pattern`.  The public save operation is deliberately controlled:

- preferences become `user_confirmed` and cannot claim a BTP or investigation source;
- other categories require an owned source conversation and evidence references that are
  authorized at save time, and become `confirmed`;
- hypotheses, unsupported claims and raw assistant text are not automatically promoted;
- content, evidence references and source references are sanitized and redacted before save;
- default expiry is bounded and expired/deleted rows are excluded from retrieval.

The API is:

```text
GET    /api/v1/memories
POST   /api/v1/memories
GET    /api/v1/memories/{memory_id}
PATCH  /api/v1/memories/{memory_id}
DELETE /api/v1/memories/{memory_id}
```

These endpoints expose only the authenticated user's active, authorized memories.  A memory
ID is never sufficient for access.  Retrieval refreshes active grants, filters account and
environment coverage, rejects expired/deleted rows, and preserves the owner boundary.  A
revoked BTP grant therefore removes scoped memory access immediately.  Source conversations
are checked during creation, and their scope is inherited unless the caller supplies the same
scope explicitly.

## Retrieval and evidence

Retrieval uses PostgreSQL metadata and bounded lexical term overlap.  It does not require
embeddings, OpenAI, a vector database or external network access.  Knowledge-document RAG
remains a separate tool and store.  Retrieved memories are labelled data, not instructions.

Agent evidence now carries one of `confirmed`, `likely`, `possible` or `unknown`.  Successful
server-authorized tool results produce `confirmed` evidence references; model knowledge and
unavailable evidence remain non-confirmed.  The system never treats a missing connector,
unavailable tool or model hypothesis as proof of system health or incident resolution.

## DeepSeek reasoning integration

The existing DeepSeek adapter remains the only chat provider.  Follow-up messages receive
the relevant prior turns, so pronouns and references can be interpreted from conversation
context.  The current request remains primary, and the model sees history/memory/evidence as
untrusted data.  Routing remains application-controlled: the model can classify a route but
cannot choose arbitrary memory namespaces or tool names.  Tool execution and BTP scope checks
remain in `ToolGateway` and `ToolRegistry`.

Private chain-of-thought is neither requested nor persisted.  The graph persists structured
route, plan, tool status, evidence references, failure metadata and bounded IDs instead.

## Failure and recovery

- Missing or expired memory is an empty retrieval, never a success claim.
- Memory database errors propagate through the existing safe database error handler.
- DeepSeek errors keep their existing stable error codes and API behavior.
- Invalid model output remains rejected by the existing strict parser.
- Permission changes are rechecked on each memory retrieval and graph authorization step.
- Existing LangGraph checkpoint setup stays explicit and separate from the main database.
- Existing Prompt 2 in-flight tool/LLM replay limitation remains; node-boundary checkpointing
  cannot make an external call exactly-once without a durable idempotency ledger.

## Privacy and limitations

Passwords, tokens, API keys, refresh tokens, credentials, secret-bearing provider payloads,
raw tool data and private checkpoint contents are not persisted as memory or sent to DeepSeek.
Redaction is defense-in-depth; scope authorization and data minimization remain primary.

There is no automatic memory extraction from every conversation.  This is intentional: a
model hypothesis must not become a trusted fact.  A future extraction workflow can produce a
bounded candidate for explicit user/admin confirmation without changing the retrieval or
authorization contracts.

The existing React chat already loads prior conversation rows and retains the active
conversation across refresh.  No UI redesign was needed for follow-up context; memory
management is available through the authorized API and does not expose LangGraph checkpoints.
