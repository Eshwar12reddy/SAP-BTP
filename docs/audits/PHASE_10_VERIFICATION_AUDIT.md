# Phase 10 Verification Audit

Date: 2026-11-04  
Scope: initial verification and corrective follow-up of the implementation in this workspace.
The corrective follow-up changed application code/tests/docs only; the main database was not modified.

## A. Overall result

**Initial audit: FAIL for Phase 11 readiness. Corrective implementation: code blockers closed;
deployment readiness remains BLOCKED by the intentionally untouched main database.**

The isolated Phase 10 schema and the implemented retrieval path are substantially present and pass focused mocked/integration checks. However, the configured main database is not migrated at all, the upload parser is incompatible with the installed Starlette runtime, and indexing occurs after Phase 9 version activation with no publication guarantee. These are release-blocking conditions.

Phase 11 readiness: **BLOCKED pending approved main-database migration and verification**.

The isolated checks below are not live-provider verification. OpenAI, DeepSeek, and SAP BTP were not called.

## B. Feature verification matrix

| Component | Implemented | Enabled | Tested | Evidence | Remaining limitation |
|---|---:|---:|---:|---|---|
| Parent/child chunking | Yes | Yes | Yes | app/rag/chunking.py; deterministic chunk test and isolated extraction check | Uses cl100k_base when available; explicitly falls back to character windows offline. |
| PDF/TXT/Markdown extraction | Yes | Yes | Yes | app/rag/extraction.py; synthetic PDF/TXT/Markdown checks | Terminal whitespace can drop the final TXT paragraph. No OCR. |
| PostgreSQL lexical index | Yes | Yes | Yes | Isolated migration and knowledge integration tests | Main database is not migrated. |
| OpenAI embeddings adapter | Yes | No (OPENAI_EMBEDDINGS_ENABLED=false) | Yes, mocked | app/integrations/openai_embeddings.py; 5 focused cases | No live provider test was performed. |
| pgvector persistence/search | Yes | Only with embeddings enabled | Yes, mocked vectors | vector(1536) column; hybrid integration case | No HNSW/IVFFlat index is created; acceptable for small collections, but scale testing is absent. |
| Hybrid/RRF retrieval | Yes | Lexical now; hybrid opt-in | Yes | app/services/retrieval_service.py; isolated hybrid and fallback checks | No cross-encoder stage. |
| Cross-encoder reranking | No | No | No | Only a fixed disabled_model_not_approved status exists | Acceptance criteria for cross-encoder reranking are not passed. |
| Parent expansion | Yes | Yes | Yes | Retrieval service returns bounded parent context and child evidence | Expansion is truncated to the evidence budget, as designed. |
| Trusted citations | Yes | Yes | Yes | Citation objects are built from backend rows; revoked chat citation test passes | Stored metadata retains source/version references only; no live provider-generated answer is implemented. |
| Read-only knowledge_search tool | Yes | Yes | Yes | Registered and frozen in app/main.py; tool/agent tests pass | Routing is deliberately keyword/conservative. |
| LLM grounded synthesis | No | No | No | answer_mode="extractive_only"; SingleAgent.knowledge_search does not call DeepSeek | DeepSeek document synthesis and citation validation remain future work. |
| Security and revocation | Mostly yes | Yes | Yes | Scope, removal, grants, tool restrictions, and saved-reply revalidation tests | Must be reverified after the deployment database is migrated. |

## C. PostgreSQL and migrations

### Main database

The running btp-postgres container reports PostgreSQL 16.15. The configured btp_agent database is currently an empty application database:

- no alembic_version table;
- no vector extension (pg_extension contains only plpgsql);
- no public application tables.

Therefore the reported revision a17b6c8d4e21 is not the current revision of the main database.

### Isolated test database

The repository fixture created and migrated the separate btp_agent_test database. Direct inspection showed:

- Alembic revision: a17b6c8d4e21 (head);
- pgvector: 0.8.7;
- tables: knowledge_sources, knowledge_source_versions, knowledge_index_generations, knowledge_parent_chunks, knowledge_child_chunks;
- child embedding type: vector(1536);
- FTS type: tsvector with GIN index ix_knowledge_child_fts;
- one published generation per source: partial unique index uq_knowledge_index_one_published_per_source;
- source/version and generation/parent/child composite foreign keys are present;
- trg_knowledge_versions_immutable protects version updates/deletes;
- the immutability function permits only pending-to-final validation fields to change.

Model/schema comparison:

    alembic check -> No new upgrade operations detected.

No HNSW or IVFFlat index is defined. This is acceptable for the requested small-collection verification and does not prove production-scale performance.

## D. Extraction and chunking

Synthetic isolated checks confirmed:

- PDF extraction returns page 1/page 2 references;
- Markdown headings are preserved as Intro and Intro / Child;
- malformed PDFs return pdf_extraction_failed;
- empty text returns insufficient_extractable_text;
- byte limits are enforced;
- no extraction code invokes an external AI provider or executes uploaded content;
- chunking is deterministic;
- configured parent/child targets are 1,000/250 tokens with 50-token overlap;
- the normal tokenizer is tiktoken cl100k_base;
- a bounded Unicode-character fallback is explicitly marked in the index config version when the tokenizer asset is unavailable.

Finding: app/rag/extraction.py:14 uses a paragraph regex whose final match requires either a blank-line boundary or an immediate end-of-string. A TXT document ending with spaces/newline after its final non-space character can lose that final paragraph. Direct evidence: "First paragraph.\n\nSecond paragraph with normalization.  \n" returned only the first paragraph. This is a correctness defect, not a token-estimation limitation.

## E. Embeddings

The adapter is implemented in app/integrations/openai_embeddings.py and enforces:

- model text-embedding-3-small;
- dimension 1536;
- bounded batches, semaphore concurrency, timeout, and bounded retries;
- disabled mode and missing-key fail-closed behavior;
- response model, index ordering, exact count, dimensions, finite values, and malformed-response checks;
- no request text, response body, or key in adapter errors.

The adapter submits redacted child text during indexing and a redacted query during semantic search. The isolated tests used mocked HTTP transports only. There was no live OpenAI request.

Current configuration has embeddings disabled. DeepSeek document synthesis is also not enabled.

## F. Retrieval

Lexical retrieval uses PostgreSQL websearch_to_tsquery, ts_rank_cd, current authorized source scope, active validated version, and published generation filters. Vector retrieval uses pgvector cosine distance and checks model/dimension identity. Hybrid retrieval merges lexical and vector candidates with RRF:

    score += 1 / (fusion_constant + lexical_rank)
    score += 1 / (fusion_constant + vector_rank)

It deduplicates by child ID, applies deterministic child-ID tie breaking, limits results, and caps two selected children per source. Vector failure and model mismatch fall back to lexical retrieval.

The isolated knowledge suite verified keyword search, mocked vector search, hybrid mode, model-mismatch lexical fallback, scope filtering, and out-of-scope embedding exclusion.

No claim is made that semantic search has been verified against the live OpenAI service.

## G. Cross-encoder

No cross-encoder implementation, model loading, reranking call, or query-child pair interface exists. KNOWLEDGE_RERANK_CANDIDATES is only a setting, and the response reports reranking_status="disabled_model_not_approved". RRF fusion is not cross-encoder acceptance.

## H. Citations and parent expansion

The backend constructs citations from selected child, parent, source, and active version rows. Returned evidence includes source ID/title, version ID/number, page range, section heading, parent ID, child ID, download endpoint, exact child excerpt, parent context, ranks, and fused score.

Parent expansion is deduplicated by parent ID and bounded by KNOWLEDGE_EXPANDED_PARENT_TOP_K and KNOWLEDGE_EVIDENCE_BUDGET_TOKENS. Retrieval requires the source to remain active and the chunk generation to match the active version, preventing inactive-version or cross-source expansion.

The agent renders extractive evidence directly; document text is not sent to DeepSeek. Saved assistant replies store source/version references and are revalidated before display. The isolated chat test confirmed that revoking the knowledge grant hides the saved document-based content and citations.

## I. Agent and chat integration

knowledge_search is registered in app/main.py:95-100, is read-only, requires knowledge:read, uses bounded input (2..1000 characters and 1..8 results), validates typed output, and runs through the frozen single ToolRegistry.

The existing SingleAgent remains the coordinator. Knowledge retrieval is optional and explicitly routed; general and operational routes do not automatically receive document context. Missing evidence returns an honest no-match response. Reply persistence uses idempotent message/reply keys and stored metadata. Safe Markdown rendering is retained in the existing frontend path.

The actual enabled answer capability is **extractive cited answers**, not DeepSeek-generated grounded answers.

## J. Security

Static inspection and isolated tests support the following:

- knowledge reader/manager permission separation;
- global knowledge distinguished from BTP-account/environment knowledge;
- SQL-side scope filtering rather than frontend-only filtering;
- cross-account and cross-environment isolation;
- removed sources and inactive versions excluded from retrieval/download;
- disabled users and revoked grants covered by existing authorization tests;
- embeddings receive only authorized child text/query inputs, with redaction before provider transmission;
- the tool registry refuses unregistered and write-capable tools;
- arbitrary import-path or callable execution is not exposed;
- document instructions remain data and are not sent to the reasoning model.

The PDF validator rejects active-content markers, archives, executables, malformed/encrypted PDFs, and invalid text, while its own documentation correctly states that this is not malware scanning.

## K. Tests and checks

Accepted focused verification results:

| Check | Result |
|---|---:|
| tests/rag/test_chunking.py + tests/test_openai_embeddings.py | 5 passed |
| tests/integration/test_knowledge.py | 7 passed |
| tests/test_tool_registry.py + tests/test_agent_router.py | 60 passed (including parametrized cases) |
| tests/integration/test_authorization.py | 19 passed (including parametrized cases) |
| tests/integration/test_conversation_security.py | 6 passed |
| tests/integration/test_agent.py -k knowledge_reply_is_extractive | 1 passed, 17 deselected |
| Accepted focused pytest total (initial audit) | **98 passed, 0 failed, 17 deselected, 0 skipped** |
| alembic current on isolated DB | a17b6c8d4e21 (head) |
| alembic check on isolated DB | passed; no new operations |
| ruff check . (corrective follow-up) | **passed; all checks passed** |
| Backend corrective regression suite | **122 passed, 0 failed** |
| Frontend type-check | **passed** in an isolated `npm ci` environment from the checked-in lockfile |
| Frontend tests | **74 passed across 6 files** |
| Frontend production build | **passed** (`vite v8.3.5`) |

The initial Ruff findings were source-quality failures in:

- backend/alembic/versions/20261101_a17b6c8d4e21_knowledge_lexical_index.py import ordering;
- backend/app/models/__init__.py __all__ ordering;
- backend/app/services/knowledge_service.py import ordering.

The first unshimmed upload integration attempt also demonstrated the host dependency problem below: Starlette 0.41.3 did not accept the max_part_size keyword used by backend/app/api/v1/knowledge.py:111, producing HTTP 500. Corrective tests now run without that shim. The installed runtime also lacked Starlette's newer HTTP_413_CONTENT_TOO_LARGE alias; the application now uses the supported HTTP_413_REQUEST_ENTITY_TOO_LARGE constant.

## L. Issues

### Critical

**C-01 — Main database is not at Phase 10.**  
Affected state: configured btp_agent database; migration a17b6c8d4e21.  
Evidence: no alembic_version, no vector, and no application tables.  
Recommended correction: obtain the database operator approval to enable pgvector and run alembic upgrade head against the intended main database, then verify readiness and data preservation. This audit did not do so.

### High

**H-01 — Active version is committed before index publication.**  
Affected paths: backend/app/services/knowledge_service.py:503, backend/app/services/knowledge_service.py:571, backend/app/rag/indexing.py:66.  
Evidence: upload commits the new active version, then calls indexing; indexing failures are recorded/returned as an IndexResult that the upload path ignores. Retrieval requires both the active version and a published generation, so a failed or timed-out index leaves an active version with no searchable generation while the API can still return success.  
Recommended correction: define an explicit activation/index state or publish only after a successful generation; alternatively return a durable index_pending/failed state and make the product contract explicit. Preserve the previous generation until the replacement is fully published.

**H-02 — TXT extraction can silently omit a final paragraph.**  
Affected path: backend/app/rag/extraction.py:14.  
Evidence: a valid TXT payload with a trailing newline/spaces returned only the first paragraph in direct testing.  
Recommended correction: normalize terminal whitespace before paragraph matching or make the paragraph regex accept trailing horizontal whitespace before end-of-string; add regression tests for final newline, spaces, CRLF, and multiple paragraphs.

**H-03 — Upload route depends on an undeclared Starlette API level.**  
Affected paths: backend/app/api/v1/knowledge.py:111, backend/requirements.txt, backend/pyproject.toml.  
Evidence: installed FastAPI 0.115.6/Starlette 0.41.3 raised TypeError: Request.form() got an unexpected keyword argument max_part_size, causing the upload endpoint to return HTTP 500.  
Recommended correction: pin/document a Starlette minimum that supports this keyword, or implement the limit using an API available across the supported FastAPI/Starlette range. Add a clean-environment upload smoke test.

### Medium

**M-01 — Cross-encoder reranking is not implemented.**  
Affected paths: backend/app/services/retrieval_service.py, backend/app/schemas/retrieval.py, backend/requirements.txt.  
Recommended correction: only if Phase 10 acceptance requires it, approve a specific model/runtime, implement bounded reranking over query-child pairs, and add evidence-preservation tests. Do not count RRF as reranking.

**M-02 — No live-provider verification.**  
OpenAI embeddings are disabled and all embedding checks were mocked. DeepSeek document synthesis was not exercised. This is policy-compliant for the audit but remains an activation limitation.

**M-03 — Frontend verification was unavailable.**  
frontend/node_modules is absent, so frontend type-check and relevant tests were not run. Install/use the project’s approved frontend dependency environment before release verification.

### Low

**L-01 — Ruff has three fixable findings.**  
These are import/ __all__ ordering issues listed in Section K. They do not indicate a demonstrated runtime security failure but prevent a clean lint gate.

**L-02 — Documentation drift.**  
docs/agent.md still describes the system as having no documentation source/RAG, while docs/rag-architecture.md describes Phase 10 retrieval. Update the older overview after the implementation decision is finalized.

## M. Remaining activation steps

1. Approve and apply the pgvector extension plus Alembic head to the intended main database; verify the revision and schema in that database.
2. Reverify the corrected code against the approved deployment database after migration.
3. Decide whether cross-encoder reranking and DeepSeek grounded synthesis are Phase 10 requirements or deferred capabilities. If deferred, retain the explicit disabled status and do not claim them as complete.
4. Perform separately approved live-provider tests, if required, with non-sensitive test data and bounded spend.

## N. Phase 11 readiness

**BLOCKED.**

The code has a usable isolated lexical/vector retrieval implementation with strong authorization
and extractive citation behavior. Phase 11 should not begin until the main database is migrated
and the corrected implementation is reverified against the approved deployment database.

## O. Corrective follow-up

The following corrective work was applied after the initial audit. No commit, push, main-database
migration, restore, reset, or write to the main `btp_agent` database was performed.

| Finding | Corrective result |
|---|---|
| H-01 active version could commit before indexing | Closed in code. Upload registration and replacement now prepare extraction/chunks/optional embeddings first, then persist the version, active pointer, file publication, and published generation in one commit boundary. Failed replacements return `503 knowledge_index_failed`; the prior published generation remains available. Reindex failures record a failed generation without retiring the prior published generation. |
| H-02 final TXT paragraph could be dropped | Closed in code. Paragraph matching accepts terminal spaces, tabs, LF, and CRLF; regression coverage includes multiple paragraphs, final whitespace, Unicode, and whitespace-only rejection. |
| H-03 Starlette upload API mismatch | Closed in code. The unsupported `max_part_size` keyword was removed; aggregate body size remains bounded by upload middleware and form fields remain bounded by Pydantic models. A runtime multipart regression was added. |
| L-01 Ruff findings | Closed in code. Import and `__all__` ordering was corrected. |
| M-03 frontend verification | Closed for verification. Isolated `npm ci` succeeded from `frontend/package-lock.json`; typecheck, 74 tests across 6 files, and the production build passed. The existing in-place `node_modules` remains untouched because a Vite process held a native binary. |

### Database identity investigation

The effective application settings point to `127.0.0.1:5444/btp_agent`, served by the running
`btp-postgres` container (`pgvector/pgvector:pg16`) and the current
`btp-ai-operations-agent_postgres_data` volume. Read-only `pg_controldata` inspection identifies
that cluster as PostgreSQL 16, system identifier `7704201859574439974`, created on 2026-11-04.
It still has no `alembic_version`, vector extension, or application tables.

An orphaned `docker_clyptus-postgres-data` volume also exists, with PostgreSQL 15 and a different
system identifier (`7693074225219121186`), created on 2026-10-05, and no attached container. Its
compose labels point to the older `docker`/clyptus project rather than this BTP workspace. It was
not started, mounted read-write, queried, copied, restored, or migrated. This is strong evidence
of a replaced/new current cluster plus a retained older data volume, but it does not prove the
old volume contains the exact intended production rows. Main activation therefore remains
blocked until the database owner identifies and approves the intended source of truth.

