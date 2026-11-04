# Phase 10: document retrieval

## Data flow

Knowledge uploads are stored privately and validated under the Phase 9 source/version rules.
The indexer extracts PDF, TXT, and Markdown text, then creates deterministic parent chunks
and overlapping child chunks. PostgreSQL stores the chunks, generated English full-text
vectors, indexing generation metadata, and (when enabled) 1536-dimensional pgvector values.

Lexical retrieval is always available. Semantic retrieval is opt-in through
`OPENAI_EMBEDDINGS_ENABLED=true`; the shared asynchronous adapter calls only the OpenAI
Embeddings API with `text-embedding-3-small`. It sends individual child chunks during
indexing and an individual redacted query during search. It never uploads a file, parent
context, or prompt. When the switch is false, neither text nor queries are sent to OpenAI.
DeepSeek remains the reasoning/generation provider and receives no retrieved document data.

The retrieval query applies the caller's current Phase 9 Reader grants and active, validated
source version in SQL before either lexical or vector candidates can be returned. The two
ranked lists are fused with reciprocal-rank fusion, then bounded by top-K, source diversity,
and an evidence token budget. Parent expansion is returned as evidence metadata. Citations
are constructed from current database source/version/chunk rows, not model output.
If the local `cl100k_base` tokenizer asset is unavailable offline, chunking falls back to
bounded Unicode character windows and marks that generation's config version accordingly.

## Configuration and migration

`backend/.env.example` contains safe placeholders. `backend/.env` is ignored by Git. OpenAI
embeddings are disabled by default. Set the key through the existing FastAPI settings and
enable the switch only after approving transmission of authorized chunks and queries.
API errors preserve lexical indexing and retrieval where possible; they never log request
text, response bodies, or credentials.

Migration `a17b6c8d4e21` adds index-generation, parent, and child tables. It requires the
PostgreSQL `vector` extension to already exist and does not create it. The isolated test
fixture enables the extension only in a database whose name ends in `_test`. The main
`btp_agent` database has not been changed for this Phase 10 migration. A database operator
must separately approve enabling the extension and applying this migration there.

## Deliberate boundaries

- Cross-encoder reranking remains disabled until a specific model and runtime budget are
  approved. The retrieval response reports this state.
- Knowledge search returns extractive evidence and verified references. Private parent
  context and retrieved excerpts are not sent to DeepSeek. Using them to compose answers
  requires a separate data-sharing approval and permission-revalidation design for stored
  replies.
- No separate vector database, local embedding model, model download, OCR, or mutation tool
  is used.
- After enabling embeddings or changing index configuration, use the scoped reindex
  endpoint to rebuild an authorized active source version.
