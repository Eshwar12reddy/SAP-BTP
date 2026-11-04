# Knowledge source management

Phase 9 stores versioned reference documents and searchable metadata. Phase 10 adds local
extraction, parent/child indexes, and permission-filtered extractive search. Optional OpenAI
embeddings are disabled by default. The AI agent receives a read-only knowledge search tool;
it receives no knowledge write tools. See [rag-architecture.md](rag-architecture.md).

## Architecture and access

The API is mounted under `/api/v1/knowledge`. The FastAPI router applies a coarse
permission check; the service then checks the requested source's exact knowledge scope
before reading data or accepting uploads. Unknown, removed, and out-of-scope source IDs
return the same 404 response. Access denials are recorded in the audit log.

| Role | Permissions | Scope behavior |
| --- | --- | --- |
| Viewer, Operator, Approver | `knowledge:read` | Read metadata and validated files where their existing BTP grants cover the source. |
| Knowledge Reader | `knowledge:read` | Read sources in assigned BTP scopes; a platform grant covers global sources only. |
| Knowledge Manager | `knowledge:read`, `create`, `update`, `remove` | Manage sources in assigned BTP scopes; a platform grant permits global sources only. |
| Administrator | None by default | Administration does not imply knowledge access. An explicit Knowledge Reader or Manager grant is required. |

BTP account descendant behavior follows the existing Phase 2 grant rules, including the
explicit `include_descendants` setting. Environment grants cover that environment alone.
The source owner and creator are set from the authenticated user and cannot be submitted
by the client.

## Upload validation and private storage

The only accepted formats are PDF, UTF-8 text and Markdown. Extension and content signature
must agree. PDFs are parsed with a page limit and rejected if encrypted or if known active
content markers are present. Text rejects invalid UTF-8, control characters and empty
content. Validation is format checking, not malware scanning.

Uploads are streamed into a private staging directory, capped at 10 MiB by default (and
also capped at the HTTP request boundary), hashed with SHA-256, and published under a random
opaque key. Original filenames never form paths. Published objects are read-only. Downloads
recheck scope, validation status, file size and checksum before sending bytes; responses use
attachment disposition and `Cache-Control: no-store`.

PostgreSQL and filesystem writes are not one atomic transaction. The service flushes rows,
publishes the object, then commits. A failed database commit removes the published object;
a process crash between publish and commit can leave an orphan. Run `python -m app.cli
knowledge reconcile` to report missing, damaged, orphaned and stale staging files. Pass the
CLI's delete option only when intentionally removing aged orphan/staging files. Referenced
objects are never removed by reconciliation.

## Versions, updates and removal

Registration creates the source and validated version 1 together. A replacement upload
locks the source row, allocates the next version number, and changes the active pointer in
the same transaction. A unique `(source_id, version_number)` constraint is the final
concurrency guard. Existing active versions remain in history and can still be downloaded
while the source is active.

Version metadata is immutable in PostgreSQL: historical versions cannot be changed or
deleted, except a reserved transition from `pending` validation to a terminal state. The
source's active version must belong to it and be validated. Metadata updates require the
client's current `revision`; stale updates return 409. Removal is soft: the source is hidden
from list, detail, version and download operations, while document bytes and audit history
are retained.

## Endpoints

| Method | Endpoint | Permission | Purpose |
| --- | --- | --- | --- |
| `POST` | `/sources` | `knowledge:create` | Multipart registration with first file. |
| `GET` | `/sources` | `knowledge:read` | Search/filter active accessible source metadata. |
| `GET` | `/sources/{source_id}` | `knowledge:read` | Read metadata and active version. |
| `PATCH` | `/sources/{source_id}` | `knowledge:update` | Update title, description or tags with revision. |
| `DELETE` | `/sources/{source_id}` | `knowledge:remove` | Soft-remove with a reason. |
| `POST` | `/sources/{source_id}/versions` | `knowledge:update` | Upload a replacement version. |
| `GET` | `/sources/{source_id}/versions` | `knowledge:read` | List immutable version history. |
| `GET` | `/sources/{source_id}/versions/{version_id}` | `knowledge:read` | Read version metadata. |
| `GET` | `/sources/{source_id}/versions/{version_id}/download` | `knowledge:read` | Download a checksum-verified file. |

Identity, checksum, storage key and version number fields are server-owned. Multipart
requests reject unknown fields. List endpoints return accessible active sources only.

## Audit and migration

Successful creation, metadata updates, removal, uploads and validations are audited.
Rejected uploads and denied access are recorded with safe reason codes. Audit events
include source/version identifiers, actor and scope where applicable; document contents are
not logged.

The migration `cc924a6aa7ae` follows `5d8e2a7c41b9`. Apply it only after reviewing the
target database and taking the normal database backup:

```powershell
Set-Location backend
python -m alembic upgrade head
```

For isolated verification, configure `TEST_DATABASE_URL` to a database whose name ends in
`_test`, or use the configured database plus the `_test` suffix. The test fixture rejects
database names without that suffix. Never use the production/application database for test
migrations or test cleanup.

## Operational limits

- Default maximum upload: 10 MiB; default PDF page limit: 1,000.
- Supported formats: PDF, TXT and Markdown only.
- Malware scanning and OCR are not included. Phase 10 adds bounded extraction, lexical and
  optional OpenAI embedding indexes, and permission-filtered extractive knowledge search;
  see [rag-architecture.md](rag-architecture.md).
- Soft-removed files consume storage until a separately reviewed retention process is
  introduced; no automatic permanent deletion is implemented.
- Filesystem durability and PostgreSQL durability are separate; reconciliation is required
  after crashes or storage incidents.
