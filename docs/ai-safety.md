# AI Safety Controls (Phase 8)

**Rule:** the AI can recommend and explain. Only backend code can authorize, approve or execute. The agent is not "prompt-injection-proof". The guarantees below come from deterministic Python, not from prompts or text filters.

## Trust boundaries

```
User ─► FastAPI auth (session, chat:use) ─► conversation owner + BTP context re-check
     ─► agent: sanitize input ─► rules route on normalized text (model label only if no rule)
     ─► tools: fixed per route in code ─► registry checks ─► tool re-checks BTP scope
     ─► evidence set by the registry (never by the model)
     ─► DeepSeek: explanation only, gets the user's question as labelled data
     ─► output checks ─► stored reply ─► safe Markdown renderer
```

| Decision | Made by | Model influence |
|---|---|---|
| Who the user is, which permissions and scopes apply | `app.auth` (session + grants, reloaded per request) | none |
| Which tool runs for a request | Fixed route → tool mapping in `single_agent.py` | can only pick a route label from a fixed enum |
| Which environment is used | Matching against the user's own authorized catalog | none (IDs never come from model text) |
| Whether a tool may run | `ToolRegistry.execute`: registered, read-only, permission, typed input, timeout, typed output | none |
| Whether something is a live check | Registry: only a successful `live_btp` tool | none |
| Source line under a reply | Code | none (model-written "Source:" lines are marked unverified) |
| Approvals and infrastructure changes | Not implemented. Write tools are refused, and operation requests are answered with a fixed refusal before any model call | none |

## Trust levels (`app/safety/input_guard.py`)

`TRUSTED_APPLICATION` (instructions written in code), `AUTHENTICATED_USER_INPUT`, `EXTERNAL_DOCUMENT`, `RETRIEVED_CONTENT`, `TOOL_RESULT`, `MODEL_OUTPUT`.

Only code can build `UntrustedContent`, and it cannot be given `TRUSTED_APPLICATION`. `build_messages` is the only prompt layout:
- **The system message** is the application's instructions plus fixed data-handling rules.
- **The user message** holds `<untrusted_content source=… trust=… suspicious=…>` blocks.
- **Each block is:**
  - sanitized (control, zero-width, bidi and Unicode tag characters removed);
  - redacted;
  - size-limited (32k for the user, 8k for documents and tool results, 48k in total);
  - defanged, so it cannot close its own block.

User text can never become a system message.

**Suspicious-pattern signals** (instruction override, role impersonation, secret requests, authority claims, tool invocation, delimiter spoofing, encoded payloads, hidden characters) are labels, not a block-list:
- They mark the block and produce a log event.
- A question *about* prompt injection is still answered.

**Rule routing** runs on NFKC-normalized text, so full-width or zero-width tricks do not get "restart my app" past the operation rule.

Only the user's own question is sent to DeepSeek; retrieved document content and parent
contexts are not. OpenAI embeddings are separately disabled by default. When enabled, only
authorized individual child chunks during indexing and redacted search queries are sent to
the Embeddings API. See [rag-architecture.md](rag-architecture.md).

## Secret redaction (`app/safety/redaction.py`)

Redaction is applied at four points:
- data before it is placed in a prompt;
- model output before it is stored;
- tool output (`ToolResult.data`);
- free text and tracebacks in every JSON log line.

It works in two layers:
1. **Exact configured values:** `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `JWT_SECRET_KEY`, the database password (raw and URL-encoded). Only values of 12 or more characters are matched, and results report a count, never which secret matched, so a redaction cannot confirm a guessed short secret.
2. **Patterns:** `Authorization`/`Proxy-Authorization` values, bearer tokens, JWTs, `sk-…` keys, PEM private keys, `scheme://user:password@host`, and `password=`/`api_key:`/`*_SECRET=`-style assignments.

In structured tool output, values under keys such as `*_secret`, `*_token`, `password`, `authorization` and `credentials` are always masked. Scanning stops at 200k characters, and the unscanned rest is dropped, never passed on.

## Structured model output (`app/safety/output_validation.py`)

`parse_structured` turns model JSON into a `StrictModelOutput` contract:
- unknown fields are rejected;
- enums and numeric bounds are validated;
- strings are at most 500 characters;
- one code fence is allowed;
- the whole output is at most 4,000 characters.

Invalid output is rejected and never repaired by a second model call. The route classifier must return exactly `{"route": "<LABEL>"}`. `{"route": …, "approved": true}`, `{"route": …, "tool": …}`, unknown labels or prose all become `UNSUPPORTED` (a clarification) and are logged.

A valid object is still only a proposal: any tool name goes through the registry, where it is a dictionary key, never an import path.

## Model answers

`check_answer` does three things:
- masks secrets;
- prefixes model-written lines such as `Source:` or `Evidence:` with "(unverified model text)", so they cannot pose as the backend footer;
- appends a fixed correction when the text claims a live check ("I checked… / your apps are healthy") or a change ("Approved. / has been increased / successfully deployed").

## Security events (JSON logs, metadata only)

| Event | When |
|---|---|
| `ai_input_rejected` | Nothing readable left after sanitizing |
| `ai_suspicious_content_detected` | Instruction-like signals in the user's own text (expected for security questions) |
| `ai_untrusted_instruction_detected` | Instruction-like signals in documents, retrieved content or tool output |
| `ai_secret_redacted` | Values masked (count and trust level only) |
| `ai_output_validation_failed` | A model contract was rejected, or an answer needed a correction (category only) |
| `ai_tool_authorization_denied` | Registry refused a tool (also audited as `authz.access_denied` for permission and scope denials) |
| `ai_operation_request_refused` | A change request was answered with the fixed refusal |

Prompts, documents, tool results, secrets and model reasoning are never logged.

## What this does not guarantee

1. Prompt injection cannot be eliminated. Instruction-like text can still influence the *wording* of a model answer.
2. Encoded, translated or indirect attacks can bypass the text signals.
3. Pattern-based secret detection is incomplete. Unknown secret formats, and short configured secrets, are not caught.
4. Structured validation proves shape, not truth. Answers can still be wrong or hallucinated.
5. Future retrieved documents may be incorrect or malicious. Labelling them as data does not make them true.
6. Human approval alone is not enough. A future execution path must verify the exact approved operation, target and parameters with code.
7. Text sanitization is not a substitute for least-privilege tools and credentials.
8. These tests cover known attack shapes; they cannot prove immunity to new ones.
