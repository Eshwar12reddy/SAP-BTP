# Single Agent and Tool Registry (Phase 7)

One agent answers a saved chat message. It routes the request, runs only registered read-only tools under the caller's own permissions, and labels every answer with where its information came from. It never changes infrastructure.

## Flow

```
POST /api/v1/conversations/{id}/messages/{message_id}/reply
  → sign-in, chat:use, conversation ownership, BTP context re-check (same as Phase 2)
  → router: fixed rules first; DeepSeek label (validated enum) only if no rule matches
  → operation request ............ refused, nothing changed
  → BTP accounts ................. btp_catalog_read      (stored mapping, never "live")
  → BTP applications ............. resolve one authorized environment, then
                                   btp_applications_list (live adapter; currently unavailable)
  → general / docs / architecture / sizing → DeepSeek with fixed rules
  → reply stored with reply_to_message_id + agent_metadata, returned with evidence
```

Code: [backend/app/agents/](../backend/app/agents/), [backend/app/tools/](../backend/app/tools/). Safety controls (redaction, untrusted-input handling, output validation): [ai-safety.md](ai-safety.md).

## Tools

| Tool | Input | Permission | Source | Live |
|---|---|---|---|---|
| `btp_catalog_read` | none | `btp:read` | `stored_metadata` | never |
| `btp_applications_list` | `environment_id` (UUID) | `btp:read` | `live_btp` | only on success |

`ToolRegistry.execute` is the only execution path. It refuses unregistered tools and write tools, checks the permission, validates input (unknown fields are rejected), applies a timeout, and validates output. Each tool then re-checks the specific resource against the caller's scope. Denials are audited (`authz.access_denied`). Failures return a status (`denied`, `invalid_input`, `unavailable`, `failed`) and a stable code, never exception text. `live_check_performed` is set by the registry, only for a successful `live_btp` tool.

No documentation, architecture or sizing tool is registered, because nothing real exists behind them yet. Those routes are answered from model knowledge and labelled as such.

## Honesty rules

- Catalog listings, unavailable checks, refusals and clarifications are fixed text. The model does not touch them.
- Environments named in a request ("development", "prod") are matched only against the caller's authorized catalog. An unmatched name is never replaced by another environment, and several matches lead to a question.
- DeepSeek receives only the current message and fixed rules. It never receives BTP data or earlier messages, so text from revoked scopes cannot reach it.
- Every answer ends with a source line written by code. If a model answer claims a live check anyway, a correction note is appended.
- Limits per request: at most 2 DeepSeek calls and 2 tool calls.

## Reply endpoint

| Status | Meaning |
|---|---|
| 201 | Reply created: `{message, route, routing_method, outcome, sources, live_check_performed, tool_results, llm_calls, model}` |
| 200 | A reply already existed and is returned without a new AI call (also after a restart) |
| 409 `reply_in_progress` | Another request (any worker) is generating this reply. Not a failure: poll the status endpoint (`Retry-After: 3`) |
| 404 | Not your conversation, or not a user message |
| 403 `btp_scope_revoked` | The conversation's BTP context is no longer authorized |
| 429 / 502 / 503 / 504 `deepseek_*` | AI failure. Nothing is stored, the attempt is recorded as `failed`, and asking again retries |

`GET …/messages/{message_id}/reply` is read-only and never starts an AI call. It returns `{status, reply}`, where `status` is `not_requested`, `generating`, `failed` or `completed` (with the reply). Message lists include each user message's `reply_status`, so a failed or unfinished reply is still visible after a page refresh. Messages whose reply was never requested (`null`) are ordinary history.

Reply state is durable: `messages.reply_status` / `reply_status_at`. Starting a reply is one conditional `UPDATE`, so exactly one request across all workers calls the agent. A run is limited to `2 × DEEPSEEK_REQUEST_DEADLINE + 60` seconds. A `generating` state older than that (plus 30 s) belongs to a dead request: it is reported as `failed` and can be retried. Completed replies are never regenerated, and `reply_to_message_id` stays unique as a second guard.

The chat renders assistant replies as Markdown (react-markdown + remark-gfm) with raw HTML skipped, only `http`/`https`/`mailto` links, and no images loaded. User messages are always shown as plain text.

## Limitations

- No live SAP BTP integration: application questions report `btp_integration_unavailable`.
- No documentation source (RAG) and no validated sizing engine.
- Routing rules are keyword-based and deliberately conservative. An imperative such as "Create an architecture diagram" is treated as an operation request and refused.
- Single-turn: follow-up questions do not see earlier messages.
- No streaming: a reply appears when it is complete.
