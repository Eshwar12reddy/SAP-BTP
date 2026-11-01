# Project Foundation (Phase 1)

This phase provides persistent, database-backed chat: a FastAPI backend that stores conversations and user messages in PostgreSQL, and a React chat UI that reads and writes them. There is no AI yet. Authentication, RAG, DeepSeek and SAP BTP integrations come in later phases.

## Architecture

```
Browser (React + Vite, :5173)
   │  /api/*, /health/*  (Vite dev proxy, same origin)
   ▼
FastAPI (:8000)  ── RequestContextMiddleware: request id, JSON access log, last-resort 500
   │               CORS (explicit origins only)
   │  /health/live, /health/ready
   │  /api/v1/conversations…  → services/conversation_service.py
   ▼
SQLAlchemy 2 async engine (psycopg 3, pooled, pre-ping, timeouts)
   ▼
PostgreSQL 16 + pgvector image  (container btp-postgres, 127.0.0.1:5444 → 5432)
```

| Path | Responsibility |
|---|---|
| `backend/app/main.py` | App factory, lifespan (engine create/dispose), middleware, routers |
| `backend/app/__main__.py` | `python -m app` launcher (picks a psycopg-compatible event loop on Windows) |
| `backend/app/core/config.py` | Typed settings shared by FastAPI, Alembic and tests |
| `backend/app/core/logging.py` | JSON logs to stdout with request ids |
| `backend/app/core/exceptions.py` | Error types and the consistent error response format |
| `backend/app/core/middleware.py` | Request ids, access logging, unexpected-exception handling |
| `backend/app/db/` | Declarative base, engine and session factory, readiness check |
| `backend/app/models/` | `Conversation`, `Message` |
| `backend/app/schemas/` | Pydantic request and response models |
| `backend/app/services/conversation_service.py` | Transactions, idempotent message insert, pagination |
| `backend/app/api/` | Health and v1 conversation routes |
| `backend/alembic/` | Migrations (the only way the schema is changed) |
| `frontend/src/services/api.ts` | The only place the UI talks to the backend |
| `frontend/src/hooks/` | Conversation list, message history, idempotent sender |
| `frontend/src/pages/ChatPage.tsx` | Sidebar + chat view |

The placeholder directories `backend/app/{auth,rag,agents,integrations,sizing}`, `backend/tests/{auth,rag}` and `frontend/src/auth` are kept for later phases and are untouched.

## Database

The existing `btp-postgres` container (`pgvector/pgvector:pg16`, created by `docker-compose.yml`) is reused. Nothing in this phase creates, recreates or reconfigures it.

| | Value |
|---|---|
| From the host | `127.0.0.1:5444` |
| From the compose network | service `postgres`, port `5432` |
| Database / role | `btp_agent` / `btp_agent` (password lives in `.env`) |
| Application tables | `conversations`, `messages`, `alembic_version` |
| Test database | `btp_agent_test` (same server, created automatically by the test suite) |
| pgvector | Available in the image (0.8.7) but **not enabled**; the RAG phase will add `CREATE EXTENSION vector` in a migration |

### Schema

`conversations`: `id uuid PK`, `title varchar(200)` (default "New conversation", not blank), `created_at`, `updated_at` (`timestamptz`). `updated_at` is the activity timestamp and is bumped in the same transaction as each new message. Index: `(updated_at, id)`.

`messages`: `id uuid PK`, `seq bigint identity` (insertion order, used for ordering and cursors), `conversation_id uuid FK → conversations ON DELETE CASCADE`, `role` (`user|assistant|system`, check constraint), `content text` (non-empty), `client_message_id uuid`, `created_at timestamptz`.
- `UNIQUE (conversation_id, client_message_id)` is the idempotency key. Server-generated messages use `NULL` and never collide.
- Index: `(conversation_id, seq)`.

### Migrations

Alembic reads the same settings as the app (`DATABASE_URL`, or the `POSTGRES_*` parts). The URL is never written into `alembic.ini`, so special characters in passwords are safe.

```powershell
cd backend
python -m alembic current             # applied revision
python -m alembic upgrade head --sql  # preview SQL without touching the database
python -m alembic upgrade head        # apply
python -m alembic check               # models and database in sync?
python -m alembic revision --autogenerate -m "describe change"   # then review the file
```

Rules: review every generated migration, ship only additive changes unless a destructive one has been explicitly agreed, never `alembic stamp` blindly, never `create_all()`. The app does **not** migrate on startup; `/health/ready` reports `schema_not_ready` until the database is at the code's head revision.

## Configuration

One `.env` at the repository root is read by both docker compose and the backend (`backend/.env`, if present, overrides it). Copy `.env.example` to create it. Real environment variables take precedence over both files.

| Setting | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `development` or `test`. `production` is refused until authentication exists. OpenAPI docs are served only in development. |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | Used by `python -m app`. |
| `LOG_LEVEL` | `INFO` | |
| `DATABASE_URL` | none | `postgresql+psycopg://…`; `postgresql://` is accepted and switched to psycopg. Other drivers are rejected; there is no SQLite fallback. |
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | `127.0.0.1` / `5444` | Used to build the URL (with correct escaping) when `DATABASE_URL` is unset. `POSTGRES_DB/USER/PASSWORD` also initialise the container on first creation. |
| `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` | `5` / `10` | |
| `DATABASE_CONNECT_TIMEOUT` | `5` s | Applies per connection attempt. |
| `DATABASE_POOL_TIMEOUT` | `10` s | Wait for a free pooled connection. |
| `DATABASE_STATEMENT_TIMEOUT_MS` | `15000` | Server-side `statement_timeout`. |
| `HEALTH_CHECK_TIMEOUT` | `3` s | Upper bound for `/health/ready`. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated. Wildcards are rejected. |
| `TEST_DATABASE_URL` | `<db>_test` | Tests refuse any database not named `*_test`. |

Secrets are `SecretStr`. Configuration errors list the failing fields without echoing values, and logs only include the database name, host and port. The frontend never sees database settings; its only knobs are in `frontend/.env.example`.

## Local development

```powershell
# Terminal 1
cd backend
.\.venv\Scripts\Activate.ps1
python -m alembic upgrade head
python -m app --reload

# Terminal 2
cd frontend
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` and `/health` to `http://127.0.0.1:8000` (override with `BACKEND_URL`).

**Windows note:** psycopg's async driver cannot use the Proactor event loop. `python -m app` and `uvicorn --reload` both use a compatible loop. Plain `uvicorn app.main:app` without `--reload` needs `--loop asyncio:SelectorEventLoop`; otherwise startup fails with a message explaining this.

## API

Error responses always look like `{"error": {"code", "message", "request_id"[, "details"]}}`. Every response carries an `X-Request-ID` header (a caller-supplied id is reused if it matches `[A-Za-z0-9._-]{1,64}`).

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/health/live` | 200 `{"status":"alive"}` (no database access) | |
| GET | `/health/ready` | 200 `{"status":"ready","database":"connected"}` | 503 `{"status":"not_ready","database":"unavailable" \| "schema_not_ready"}` |
| POST | `/api/v1/conversations` | 201 conversation. Body `{"title"?: string ≤ 200}` (optional) | 422, 503 |
| GET | `/api/v1/conversations?limit=50&offset=0` | 200 `{"items", "has_more"}`, most recently active first. `limit` 1–100 | 422, 503 |
| GET | `/api/v1/conversations/{id}` | 200 conversation | 404, 422, 503 |
| GET | `/api/v1/conversations/{id}/messages?limit=100&before={message_id}` | 200 `{"items", "has_more"}`: the newest `limit` messages, oldest first. Page backwards with `before`. `limit` 1–200 | 404, 422, 503 |
| POST | `/api/v1/conversations/{id}/messages` | 201 new message, or 200 for an identical retry | 404, 409, 422, 503 |

Message body: `{"content": string, "client_message_id": uuid}`. `role` may be omitted or `"user"`; anything else is 422. Content is stored verbatim (multiline and indentation preserved). It is rejected if blank, longer than 32,000 characters, or contains NUL. Reusing a `client_message_id` with different content returns 409. Concurrent retries are serialised by the unique constraint (`INSERT … ON CONFLICT DO NOTHING`): exactly one request creates the row. The first message renames a conversation that still has the default title. Success is returned only after the transaction commits.

Interactive docs: http://127.0.0.1:8000/docs (development only).

## Logging

JSON lines on stdout, suitable for `docker logs` or any collector:

```json
{"timestamp": "...", "level": "INFO", "logger": "app.request", "request_id": "…", "message": "request completed", "event": "http_request", "method": "POST", "route": "/api/v1/conversations/{conversation_id}/messages", "status_code": 201, "duration_ms": 12.4}
```

Logged: startup and shutdown, database readiness at startup, every request (route template, not the raw URL), database outages, and unexpected exceptions with tracebacks. Never logged: message contents, request bodies, headers, credentials, or full connection strings. SQLAlchemy is created with `hide_parameters=True`; the uvicorn access log is replaced by the middleware.

## Testing

```powershell
cd backend; python -m pytest          # 63 tests: config, database, API, health, logging
cd frontend; npm test                 # 18 tests: API client, chat page flows
cd frontend; npm run build            # type-check + production build
```

Backend tests use the `btp_agent_test` database, which they create and migrate automatically and truncate between tests. They never write to `btp_agent`. Outages are simulated with an unreachable port and wrong credentials; the real container is never stopped.

Manual browser check:
1. Start the backend and frontend, then open http://localhost:5173.
2. Click **New chat**, type a two-line message (Shift+Enter), and press Enter. Expect the "Message saved…" notice and a cleared composer.
3. Refresh. The conversation stays selected (URL `/c/<id>`) and the message is shown.
4. Stop the backend and send another message. Expect "Message not saved. Cannot reach the server…", with the text kept and a **Retry** button.
5. Start the backend again and click **Retry**. The message is saved once.
6. Optional: run `docker exec btp-postgres psql -U btp_agent -d btp_agent -c "SELECT role, content FROM messages ORDER BY seq"`.

## Known limitations

- **No authentication.** Conversation endpoints are for trusted local development only. The API binds to `127.0.0.1` and `APP_ENV=production` is refused. Do not expose it on a network.
- No AI responses. After saving, the UI shows a neutral notice that is not stored as a message.
- No edit, delete or rename endpoints for conversations or messages.
- The sidebar shows the 100 most recently active conversations (the API supports paging beyond that).
- No backend container yet; the backend runs on the host against port 5444.

## Future integration points

- **Auth:** add `owner_id` to `conversations` (migration), resolve the user in a dependency next to `get_session`, filter every query in `conversation_service` by owner (return 404 for other users' data), then lift the production guard in `config.py`.
- **DeepSeek:** an assistant reply is a server-created `Message(role="assistant", client_message_id=None)`, written in its own transaction after the user message commits. `DEEPSEEK_API_KEY` is already reserved in `.env.example`.
- **RAG:** a migration enabling `vector` plus embedding tables. The `pgvector` Python package is already a dependency.
- **Infrastructure actions / approvals:** new tables and services. Reuse the error and logging conventions above.
