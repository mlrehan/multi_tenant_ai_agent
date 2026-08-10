# Knowledge Base Ingestion & RAG Query Pipeline — Plan

Status: **Phases 10–14 all done.** This is the spec for turning the AI-resource schema and ports built in Phase 7 (knowledge bases, documents, the `VectorSearchClient`/`ObjectStoragePathFactory`/`VectorNamespaceFactory` ports, all currently backed by in-memory or logging stand-ins — see [22-deployment-and-operations.md](22-deployment-and-operations.md#known-gaps)) into a working system: a tenant owner uploads files or points at URLs/websites, content gets parsed, chunked, embedded, and stored in a real vector database, and an embeddable public widget answers visitor questions against it with citations, scoped by RBAC.

Source diagram: [../Architectural_Diagram.txt](../Architectural_Diagram.txt) (two flows — async ingestion, real-time query/answer). This doc turns that diagram into delivery phases and names the concrete decisions needed to build it.

---

## Why five phases, not one

This project's rule is one phase per session, each independently completable and verifiable (see [09-roadmap.md](09-roadmap.md)). This feature has five natural seams, each a coherent runtime capability on its own:

| Phase | Deliverable | Depends on |
|---|---|---|
| 10 | Real storage & vector infrastructure (the plumbing behind existing ports) | Phase 7 (ports already exist) |
| 11 | Background ingestion pipeline for uploaded files (the `workers/` runtime, for real) | Phase 10 |
| 12 | URL & website ingestion (crawling) | Phase 11 (reuses its chunk/embed/upsert pipeline) |
| 13 | RAG query pipeline (LangGraph retrieval + grounded generation) | Phase 10 (needs real vector search) |
| 14 | Embeddable public widget + public chat API | Phase 13 |

Phases 10–12 are the **ingestion side** (Flow A in the diagram); 13–14 are the **query side** (Flow B). They can, in principle, run in either order after Phase 10 — but a widget with nothing indexed to answer from isn't useful to verify, so the plan builds ingestion first.

---

## Phase 10 — Real storage & vector infrastructure ✅ DONE

**Goal:** replace the three stand-in adapters with real ones, behind the *same* ports Phase 7 already defined. No new user-facing behavior — this phase is invisible except that uploads and queries now touch real systems instead of memory.

### What was actually built

| Piece | File | Notes |
|---|---|---|
| Settings groups | `core/config.py` | `StorageSettings`, `QdrantSettings`, `OpenAISettings`, `CohereSettings`, `TavilySettings`, `IngestionSettings` — all defaulting to empty credentials so the IAM platform still boots without them |
| `ObjectStorageClient` port | `application/ai_resources/ports.py` | `put`/`get`/`delete`. No list-by-prefix method, deliberately — that's where a cross-tenant read would hide |
| Local FS adapter | `infrastructure/storage/local_filesystem.py` | Atomic write via `os.replace`; **path containment verified on every call** |
| Cloudflare R2 adapter | `infrastructure/storage/cloudflare_r2.py` | boto3 against the R2 S3 endpoint; lazy import matching `aws_secrets_manager.py` |
| `EmbeddingClient` port + OpenAI adapter | `application/.../ports.py`, `infrastructure/embeddings/openai_client.py` | Batches at OpenAI's 2048-input cap; **sorts by `index`** so vectors can't be mismatched to chunks |
| Real Qdrant client | `infrastructure/vector/qdrant_search.py` | Collection-per-tenant, `knowledge_base_id` as an indexed payload filter |
| Unconfigured stand-in | `infrastructure/vector/unconfigured.py` | Raises, naming the setting to fix |
| Faithful in-memory fake | `infrastructure/vector/in_memory_search.py` | Real cosine similarity — upgraded from the Phase 7 seed-and-replay stub, for tests only |
| Dev Qdrant | `docker-compose.dev.yml` | `qdrant/qdrant:v1.19.0` on port 56333 |

### Decisions made during implementation

1. **Collection-per-tenant, not per-knowledge-base.** The diagram sketches `tenant_university_a_xxxx`; the stored `vector_namespace` is `{tenant_id}/{knowledge_base_id}`. Reconciled by mapping namespace → collection `tenant_{uuid.hex}` with `knowledge_base_id` as an in-collection filter. Per-KB collections would not survive the "thousands of tenants" this platform is sized for (Qdrant carries real per-collection overhead), and an assistant drawing on several knowledge bases can now be served by one filtered query instead of a fan-out. Isolation still lands on the collection boundary — the security-critical one.
2. **Namespace parsing lives beside the builder** (`vector/namespaces.py`), not in the Qdrant adapter, so the format's producer and consumer can't drift apart.
3. **An unconfigured deployment gets a client that raises, not the in-memory fake.** Falling back to in-memory would answer every search with an empty result set — indistinguishable from a knowledge base with genuinely no matches, and invisible in logs. This is the same "inert by design" shape Phase 9 found three instances of.
4. **`embedding_dimensions` is requested from the API and used as the collection's vector size**, so there is one number rather than a model-implied value and a separately-configured index width that could desynchronise.
5. **`build_object_storage_client` is deliberately not wired into `AppContainer` yet** — nothing reads or writes bytes until Phase 11's upload route and worker. An unused container field is one more inert wire to mistake for a working feature.
6. **Qdrant is not in the `/readyz` dependency set.** The API serves identity and authorization traffic fine with the vector store down; a readiness probe failing on it would pull every pod out of rotation over a degraded RAG feature.

### Verified

`tests/integration/test_qdrant_vector_store.py` runs against a **live Qdrant**, not a mock — the tenant-isolation property is exactly the kind a mock cannot establish. Two tenants index an identical vector and each sees only its own; two knowledge bases within one tenant stay filtered apart. Also proven: idempotent `ensure_namespace`, upsert-replaces-not-duplicates, scoped delete, and fail-closed empty results for a tenant with no collection.

`tests/unit/ai_resources/test_object_storage.py` proves path traversal is refused on read, write *and* delete (a guard on `put` alone would still leak arbitrary file reads), and that an `AccessDenied` from R2 is not flattened into "not found".

Full suite: **344 tests pass** (up from 310), alongside clean `ruff`, `mypy --strict`, and all three `import-linter` contracts.

**Note the Qdrant version pin:** `qdrant-client` refuses more than one minor version of server skew and warns loudly. `pyproject.toml` and `docker-compose.dev.yml` must be bumped together.

**What's already there and gets reused, not rebuilt:**
- `ObjectStoragePathFactory` / `VectorNamespaceFactory` — server-derived paths, already correct, already tested (`docs/16`'s "server-derived namespaces and paths" guarantee).
- `VectorSearchClient` port shape (`query(*, namespace, query_text, top_k)`).
- `DocumentIngestionQueue` port shape (`enqueue(*, tenant_id, document_id, at)`).
- `provider_credentials` table + `CredentialEncryptor` (Fernet) — already built for exactly this: a tenant's own OpenAI/Cohere keys, encrypted at rest, never echoed back. Reused for BYO-key tenants; platform-level keys (this deployment's own OpenAI/Cohere/Qdrant credentials) are plain config, resolved through the existing `SecretProvider` mechanism like every other secret.

**New infrastructure adapters** (`infrastructure/storage/`, `infrastructure/vector/`):
- `ObjectStorageClient` port (new — nothing currently *writes* bytes anywhere, only derives a path for where they'd go) with two adapters, selected by environment the same way `SecretProvider` already is:
  - **Local filesystem adapter** (development): writes under a tenant-scoped directory inside the project (e.g. `var/storage/{tenant_id}/{knowledge_base_id}/{document_id}`), gitignored.
  - **Cloudflare R2 adapter** (production): S3-compatible, via `boto3` (already a dependency) pointed at the R2 endpoint — no code difference from a real AWS S3 target, just endpoint/credential config.
- Real `VectorSearchClient` backed by **Qdrant** (`qdrant-client`, already a dependency): one Qdrant *collection* per tenant, named from the same `vector_namespace` value `knowledge_bases` already stores — tenant isolation at the vector-store level mirrors the Postgres RLS isolation model, not a parallel scheme.
- `EmbeddingClient` port (new) + OpenAI adapter, `text-embedding-3-large`, 3072 dimensions (matches the diagram).

**Dev environment:** add a `qdrant/qdrant` service to `docker-compose.dev.yml` alongside Postgres/Redis, so local dev matches the topology you'll run in production (your own Qdrant server) rather than diverging from it. *(Open question below — confirm before I start.)*

**New config settings group**, following the existing `DatabaseSettings`/`JwtSettings` pattern (plain `BaseModel`, resolved secrets as `SecretStr`):
```
STORAGE__MODE=local|r2                    # local in dev, r2 in production
STORAGE__LOCAL_PATH=var/storage
STORAGE__R2_ACCOUNT_ID / R2_BUCKET / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
QDRANT__URL
QDRANT__API_KEY                            # if your self-hosted instance needs one
OPENAI__API_KEY
OPENAI__EMBEDDING_MODEL=text-embedding-3-large
OPENAI__CHAT_MODEL=gpt-5.5
COHERE__API_KEY
TAVILY__API_KEY                            # held for Phase 12/13 use, wired here for one place to configure all provider keys
```

---

## Phase 11 — Background ingestion pipeline (uploaded files) ✅ DONE

**Goal:** an uploaded file actually becomes searchable. This is where the `workers/` runtime — reserved in [19-folder-structure.md](19-folder-structure.md) since Phase 4 but never built — got built for real, as a Celery app over the Redis that was already running.

### What was actually built

| Piece | File | Notes |
|---|---|---|
| Celery app | `workers/celery_app.py` | `acks_late` + `reject_on_worker_lost` + `worker_prefetch_multiplier=1`; `task_ignore_result=True` (Postgres is the status of record, not a result backend) |
| Worker bootstrap | `workers/bootstrap.py`, `workers/main.py` | One event loop and one container per worker *process*, built lazily post-fork — a container built pre-fork hands every child a copy of the same connection pool |
| **Per-job re-validation** | `workers/job_context.py` | The security core of this phase — see below |
| Ingestion job | `workers/jobs/process_document_upload.py` | Every exit path terminal; idempotent by delete-then-write |
| Rich parsers | `infrastructure/parsing/rich_documents.py` | `docling` for PDF/DOCX/XLSX/PPTX/images; lazy import, run in `asyncio.to_thread` |
| Structured parsers | `infrastructure/parsing/structured.py` | CSV/TSV, JSON, XML on stdlib — with row/JSON-path source locations |
| Dispatcher | `infrastructure/parsing/dispatcher.py` | Structured parsers first, docling as the fallback for everything it handles |
| Chunker | `infrastructure/parsing/chunking.py` | `langchain-text-splitters` + `tiktoken`, 700/100 from `IngestionSettings` |
| Real queue | `infrastructure/queue/celery_queue.py` | Replaces `LoggingDocumentIngestionQueue` |
| `document_chunks` table | `alembic/versions/b7e3c210df94_*.py` | Provenance in Postgres under RLS; Qdrant stays a search index, not the record of truth |
| Multipart upload | `api/v1/assistants/router.py` | Real bytes, 50 MB cap enforced *while* reading, type refused at the boundary with a 415 |
| Console UI | `frontend/.../knowledge-bases/page.tsx` | Drag-and-drop upload + a document list that polls while anything is `processing` |

### The security core: `job_context.py`

A worker is not a request, but it needs the same RLS discipline — and one thing a request does not. A job carries a `tenant_id` and an `actor_user_id` that were authorized *when the job was enqueued*, which may be minutes or hours before it runs. In that window the tenant can be suspended, the membership revoked, or the user deleted.

So the job context sets the RLS context from the **claimed** tenant first, and only then validates it: tenant active → membership active → user account active and not deleted. Doing it in that order is deliberate — the validation queries themselves run under the tenant's own RLS scope, so a forged `tenant_id` cannot read rows belonging to a tenant it doesn't own even during the check that would reject it. Any failure raises `JobAuthorizationError` and the job stops.

This closes the second half of **[threat model](03-threat-model.md) scenario 8**, which Phase 8 could only record as ⚠ Partial because no worker runtime existed to attack. `tests/security/test_worker_job_revalidation.py` proves refusal for a suspended tenant, revoked membership, suspended membership, suspended user, deleted user, cross-tenant claim and nonexistent tenant/user — plus a positive case, so the seven refusals cannot be passing vacuously.

### Decisions made during implementation

1. **Bytes are written before the row is created.** `UploadDocument` puts the object in storage, then inserts the `documents` row, then enqueues. Orphan bytes beat orphan rows: an orphaned object is invisible dead weight a sweep can reclaim, whereas a `documents` row pointing at bytes that were never written is a document that will sit in `processing` and then fail for a reason the tenant can do nothing about.
2. **Failures are recorded in a separate transaction, after the failing one unwinds.** This is [docs/18](18-schema-rls-and-migrations.md)'s rollback pitfall in its most damaging form: marking a document `failed` inside the `async with uow:` block that then raises rolls the mark back, and the document stays `processing` forever. `tests/unit/ai_resources/test_ingestion_job.py` asserts the invariant directly — *no exit path leaves a document in `processing`*.
3. **Tenant-visible failure reasons are deliberately lossy.** `_readable_reason()` maps an exception to a short sentence the uploader can act on; the exception itself goes to the log. A stack trace surfaced into the console is both useless to a tenant admin and a small internals leak.
4. **The job is idempotent by deleting first.** Chunks and vectors for the document are removed before new ones are written, so Celery's at-least-once delivery — which `acks_late` deliberately chooses over at-most-once — cannot produce doubled chunks.
5. **Unsupported types are refused at upload, not asynchronously.** The person is still at the keyboard; a 415 they can read beats a document that turns red five minutes later.
6. **`workers/` may not import `api/`** — a fourth import-linter contract in spirit, enforced by the existing layered one. Building the worker's container originally reached into `bootstrap.py`, which drags `api.main` into the worker's import graph; the shared factories moved to `infrastructure/factories.py` instead. The linter caught this after I had explicitly waved it away in a docstring as harmless. It wasn't.

### Verified

- `tests/security/test_worker_job_revalidation.py` — 9 tests, the scenario-8 closure described above.
- `tests/integration/db/test_document_chunks_rls.py` — 6 tests against **live Postgres**: tenant isolation, chunk *text* non-leakage, fail-closed with no tenant context, an explicit cross-tenant filter still returning empty, `WITH CHECK` rejection on cross-tenant insert, and `ON DELETE CASCADE` from `documents`.
- `tests/unit/ai_resources/test_ingestion_job.py` — 11 tests: success path, idempotency (asserting delete precedes insert), every failure path, the "never stays in processing" invariant, an authorization refusal *not* being recorded on the document (it isn't the document's fault), and an empty document settling to `ready` rather than `failed`.

**The defect that mattered most in this phase was invisible to all of the above.** Every test imports `process_document_upload` directly and drives it with fakes — which proves the pipeline is correct and proves nothing about whether `celery -A iam_platform.workers.main:celery_app worker` can start. It could not. `build_celery_app()` called `autodiscover_tasks(..., force=True)`, so discovery ran *while* `celery_app.py` was still executing its module body; discovery imported `workers/jobs/tasks.py`, which does `from ...celery_app import celery_app`, and that name did not exist yet. The worker died on an `ImportError` before accepting a single job, and the entire phase was inert while every test passed. Found by running the entrypoint rather than importing its parts. Fixed by leaving discovery lazy (Celery then runs it at worker startup, after the module is initialized), and guarded by `tests/unit/workers/test_celery_app_wiring.py`, which imports `workers.main` the way the `celery` CLI does.

### Driven end to end against the live stack

Tests prove the pipeline is *correct*. They do not prove it *runs*. Uploading real files through a real API, into real storage, onto a real queue, picked up by a real worker, embedded by real OpenAI and indexed into real Qdrant is a different question — and it found four defects the entire suite was blind to, plus a broken production image.

What was proven working: a CSV and a PDF both go upload → `var/storage/{tenant}/{kb}/{doc}` → `documents` row → Redis → Celery worker → parse → chunk → embed → Qdrant → `ready`. Chunk provenance survives (`row 2`, `row 3`, `row 4`). Retrieval routes correctly across documents — *"When will my parcel arrive?"* hits the shipping PDF, *"Can I get my money back?"* hits the refund CSV, neither sharing keywords with its source. Redelivering a job leaves 3 chunks and 3 points, not 6. An `.exe` is refused with 415 and **no bytes written**.

**Defect 1 — the worker could not start at all.** Covered above; found by importing the entrypoint the way the `celery` CLI does.

**Defect 2 — docling compiles C++ at parse time.** Its layout model runs through TorchInductor, which *generates and compiles C++ when a document is parsed*, not when the image is built. [Phase 9's hardened runtime image](22-deployment-and-operations.md) deliberately ships no compiler — that is the point of the builder-stage split — so in production every PDF, DOCX, XLSX, PPTX and image would have failed with `InvalidCxxCompiler` while CSV/JSON/XML kept working. A partial failure that reads like a bad file rather than a bad deployment. Surfaced on Windows (`Compiler: cl is not found`), which is the same hole as a container with no `gcc`. Fixed by `_disable_torch_compilation()` in `infrastructure/parsing/rich_documents.py`, plus the same variables set in the `Dockerfile`.

**Defect 3 — the first fix for defect 2 was not a fix.** `TORCHDYNAMO_DISABLE=1` is the name most search results give and is **silently ignored by torch 2.13**: setting it leaves `torch._dynamo.config.disable` at `False`. `TORCH_COMPILE_DISABLE` is the one that works. A manual test appeared to pass with the dead name, which is exactly how a non-fix ships. Both names are now set, and the test asserts on the one that functions.

**Defect 4 — a near-miss settings name was silently swallowed.** `OPENAI_API_KEY` (one underscore) instead of `OPENAI__API_KEY`. `extra="forbid"` rejects a name that matches *nothing* — `TOTALLY_BOGUS=x` refuses to boot — but pydantic-settings *claims* a key whose lowercased name starts with a nested field's name, so `openai_api_key` is never reported as extra; and because the delimiter is `__`, the remainder never resolves to `api_key` and the value evaporates. No error, no value. The platform ran with ingestion disabled while the operator had, as far as they could tell, configured the key. Guarded now by `_reject_near_miss_group_names()` in `core/config.py`, which names the correct spelling in the error and stays quiet when the correctly-spelled variable is also present (a stray `OPENAI_API_KEY` exported for another tool must not block boot).

**And the production image could not be built.** `.dockerignore` excluded `scripts/` while the `Dockerfile` does `COPY scripts ./scripts` — so `docker build` failed with `"/scripts": not found`. [DEPLOYMENT.md](../DEPLOYMENT.md) documents production bootstrap as `docker compose run --rm migrate python scripts/bootstrap_platform_admin.py`, "now baked into the image", so the `COPY` is intended and the ignore entry was wrong. The `COPY` had been added in a later session than the last real image build, and nothing rebuilt the image until now.

**A second Dockerfile mistake, caught the same way.** The model pre-fetch layer originally ran `DocumentConverter()` — which downloads nothing, because construction is lazy and models are only fetched when a document is actually converted. The build failed on the following `chmod` (the directory it should have populated did not exist); had the `chmod` been written more forgivingly the layer would have "succeeded" while doing nothing, silently moving a multi-hundred-megabyte download back to the first production parse. `initialize_pipeline(InputFormat.PDF)` is what forces the fetch.

**A schema correction found by running the migration:** `document_chunks` is the first table to reference `documents` by composite FK `(tenant_id, id)`, and Postgres refused it — a composite FK needs a matching `UNIQUE` constraint on the referenced side. Added `uq_documents_tenant_id_id`. Same class of finding as Phase 6's `CREATE POLICY ... FOR INSERT, UPDATE, DELETE` syntax error: only running it against Postgres surfaces it.

**`documents.failure_reason` is new** — the column did not exist, so "surfaced back to the console" was unimplementable as specified. Added to the table, the domain entity (`mark_ready()` clears it, `mark_failed(*, reason=None)` sets it), the API response and the UI.

Full suite: **408 tests pass** (up from 344), alongside clean `ruff`, `mypy --strict` and all three `import-linter` contracts.

**Two new endpoints:** `POST /v1/tenants/{tenant_id}/knowledge-bases/{kb_id}/documents` became a real multipart upload (it previously accepted JSON *metadata* about a file whose bytes went nowhere), and `GET .../documents` was added — without it the console had no way to show ingestion status at all.

---

## Phase 12 — URL & website ingestion ✅ DONE

**Goal:** the same pipeline, fed by crawled content instead of uploaded bytes.

### What was actually built

| Piece | File | Notes |
|---|---|---|
| **SSRF guard** | `infrastructure/crawling/url_safety.py` | The security core of this phase — see below |
| `UrlValidator` adapter | `infrastructure/crawling/url_validator.py` | Same guard at the API boundary; translates to a mapped 400 |
| Crawl limits | `core/config.py::CrawlSettings` | depth 3, 500 pages, 30s/page, 2h/job, robots.txt — agreed with the platform owner, not guessed |
| `WebCrawler` port | `application/ai_resources/ports.py` | Yields pages rather than returning a list |
| crawl4ai adapter | `infrastructure/crawling/crawl4ai_crawler.py` | Breadth-first frontier, every bound enforced locally |
| `data_sources` table | `alembic/versions/c5f1a90b2e47_*.py` | With the crawl columns docs/16 lacks, plus RLS |
| Domain entity | `domain/ai_resources/entities.py::DataSource` | Refuses a URL-crawl with no URLs, and a site crawl with several start URLs |
| Use cases | `application/ai_resources/manage_data_source.py` | Same authorization path as upload |
| Crawl job | `workers/jobs/process_url_crawl.py` | Per-page transactions, same re-validation as ingestion |
| **Shared indexer** | `workers/jobs/indexing.py` | Extracted from Phase 11 so both jobs use one copy |
| Routes + console UI | `api/v1/assistants/router.py`, `frontend/.../knowledge-bases/page.tsx` | |

### The security core: SSRF

Everywhere else in this platform, tenant input decides what *their own* data is read or written. Here it decides **what the worker process connects to** — and the worker runs inside this deployment's network, holding database credentials, with a route to the cloud provider's metadata service. Unguarded, "crawl this website" is a complete credential-exfiltration path built out of a feature request: point it at `169.254.169.254`, let it index the response into a knowledge base, then read the credentials back through the ordinary search API.

Four properties, each of which is the one most SSRF filters get wrong:

1. **Scheme allowlist** — `http`/`https` only. `file://` reads the worker's disk.
2. **Resolve, then check the resolved addresses**, never the hostname. A name allowlist loses to `evil.test` resolving to `127.0.0.1`.
3. **Check every address a name resolves to**, not the first. A name with a public A record and a loopback AAAA record would otherwise pass on one and connect on the other.
4. **Re-check inside the crawl loop**, on every redirect and every discovered link. Validating at the boundary and trusting thereafter is what makes most SSRF filters decorative.

Recorded as [threat-model](03-threat-model.md) scenario 13, along with the **residual risk that is not closed**: DNS rebinding — a name resolving public when checked and private when connected — needs the connection pinned to the validated address inside crawl4ai's transport.

### Decisions made during implementation

1. **A crawled page is a `documents` row.** Same table, same chunk rows, same vector namespace — so a knowledge base holding both uploads and crawled pages answers one query across both. Only provenance differs (`data_source_id`, `source_url`).
2. **The shared indexer was extracted, not copied.** `workers/jobs/indexing.py` holds the delete-before-write ordering that makes redelivery safe and the `strict=True` zip that stops a chunk being indexed under another chunk's vector. Two copies of that is two chances to get it subtly wrong. Phase 11's 11 tests pass unchanged through it, which is what makes the refactor safe.
3. **Per-page transactions, not one per crawl.** A 500-page crawl in one transaction holds locks for its entire runtime and discards 498 pages of paid-for embedding work if page 499 fails.
4. **The fetched markdown is stored, not just indexed.** A few kilobytes buys re-embedding on a model or chunk-size change without re-crawling someone else's server — which would be slow, impolite, and might return different content than was originally indexed.
5. **A crawl uses `tenant.documents.upload`, not a new permission.** It adds documents to a knowledge base; that is the same authority arriving by a different route.
6. **`CrawlJobQueue` is separate from `DocumentIngestionQueue`.** A parse is seconds, a crawl is up to two hours — keeping them distinct is what allows separate worker pools later without one starving the other. The crawl task also uses `max_retries=1`, not 3: retrying a two-hour job three times is six hours and triple the bill for a site that may simply be down.
7. **The request schema has no depth, page or timeout fields.** Those are platform limits that bound what this deployment spends on a tenant's behalf; accepting them from the tenant would defeat their only purpose.

### Two schema gaps in docs/16, found by trying to build against it

- **`data_sources` had nowhere to record what to crawl.** The Phase 3 spec gives it `kind`, `knowledge_base_id`, `sync_status` and `last_synced_at` — so `kind='url_crawl'` was unsatisfiable as specified. Added `config` JSONB (non-secret only, the rule docs/16 already states for `integrations.config`), plus `failure_reason` and page counters, and a CHECK making a URL-crawl row with no URLs unstorable.
- **A composite FK needs a UNIQUE on the referenced side — again.** Postgres refused `documents → data_sources` until `uq_data_sources_tenant_id_id` existed. Identical to Phase 11's `uq_documents_tenant_id_id`, which was written up in CLAUDE.md and memory and *still* recurred.

### Verified

- `tests/security/test_crawl_ssrf_guard.py` — 30 tests, **mutation-tested**: removing the in-loop guard makes them fail.
- `tests/unit/ai_resources/test_web_crawler.py` — 14 traversal tests. One originally passed for the wrong reason (an off-host unsafe link, which same-host confinement drops anyway, so it would have passed with the guard removed); rewritten to use a same-host link on a blocked port, which only the guard can reject.
- `tests/security/test_crawl_job_revalidation.py` — 6 tests against live Postgres, including a positive control proving an authorized job *does* reach the crawler.
- `tests/integration/db/test_data_sources_rls.py` — 6 RLS proofs. A crawl source holds URLs that map a tenant's infrastructure (internal wikis, staging hosts), so the table needs the same isolation as the content it produces.

Full suite: **464 tests pass** (up from 408), alongside clean `ruff`, `mypy --strict` (202 files) and all three `import-linter` contracts. `npm run build`, `tsc --noEmit` and `eslint src` pass.

**A guard that could not see the thing it guards.** `test_exception_mapping_is_exhaustive.py` exists because unmapped exceptions surfaced as 500s three times. It **passed** for Phase 12's new errors — because they derived from `ValueError` rather than `AiResourceError`, so the scan could not see them at all. Both now live in `exceptions.py` under the module base class, mapped to 400, with the infrastructure error translated at the adapter boundary.

---

## Phase 13 — RAG query pipeline

**Part A (the pipeline) ✅ DONE. Part B (the public widget surface) ✅ DONE.**

Split deliberately. Part B introduces a genuinely new authentication surface and deserves its own threat-model pass; building the pipeline first, behind the *existing* tenant JWT, meant it could be verified against auth already trusted — so a new auth surface was never the thing standing between "is this correct?" and knowing.

### What was built

| Piece | File | Notes |
|---|---|---|
| Chunk-level retrieval | `ports.py::RetrievedChunk`, `search_chunks` | Distinct from `query`, which collapses chunks to documents — right for "which files match?", wrong for grounding |
| `Reranker` + Cohere adapter | `infrastructure/reranking/cohere_reranker.py` | Maps results through the returned `index`, never response order |
| `ChatModel` + OpenAI adapter | `infrastructure/chat/openai_chat.py` | Streaming; temperature sent only if configured |
| The pipeline | `application/ai_resources/answer_question.py` | Sanitize → retrieve 20 → rerank to 5 → grounded generation |
| SSE endpoint | `api/v1/assistants/router.py` | Sources first, then tokens, then a `done` frame |
| Streaming BFF proxy | `frontend/src/app/api/backend/[...path]/route.ts` | Pipes `text/event-stream` instead of buffering |
| Console UI | `frontend/.../knowledge-bases/page.tsx` | Ask panel with live tokens and citation badges |

### Groundedness: three defences, none of them the prompt alone

1. **No passages, no generation.** If retrieval returns nothing the model is *not called*. A model handed an empty context answers from training data and no system prompt reliably stops it. A test asserts the fake chat model records zero calls — a claim about behaviour, not wording.
2. **Citations are validated, not trusted.** A `[2]` in the output is reported only if label `2` was genuinely in the context sent. A fabricated reference becomes a *missing* citation, which is visible, rather than a plausible link to a document that says nothing of the kind.
3. **The namespace is server-derived.** Read off the knowledge-base row the caller was already authorized for — the Phase 7 guarantee — so no crafted question reaches another tenant's passages.

Prompt-injection defence is structural rather than filtering: sources are fenced with `<<<SOURCE n>>>` markers, the system prompt states content inside them is never instructions, and every claim must carry a citation. **Input blocklisting was deliberately not built** — "ignore previous instructions" has infinite paraphrases, and filtering would add the *appearance* of protection while the structural half carried the weight.

### Two decisions worth not re-litigating

1. **LangGraph was not used, departing from this document's own plan.** The flow is four sequential steps: no branching, no cycles, no tool selection, no shared mutable state. A graph framework over a straight line buys indirection and a request-path dependency and costs the ability to read the sequence top to bottom. `AnswerQuestion.execute` is a clean seam to put one behind when Part B or Phase 14 introduces genuine branching — query rewriting, multi-hop retrieval, tool use.
2. **An unconfigured reranker degrades; an unconfigured chat model raises.** The distinction is whether the feature can be honestly delivered without the dependency. Without Cohere there are still real, relevant passages, just ordered by embedding similarity — a quality reduction. Without a chat model there is no answer, and emitting "no information available" would be indistinguishable from a genuinely empty knowledge base.

### Driven live, and it found a defect the tests could not

`temperature=0` was hardcoded, with a docstring arguing for it: variation in a grounded answer is paraphrase drift away from the source. The configured OpenAI model **rejects any explicit temperature** — `Unsupported value: 'temperature' does not support 0 with this model` — so every answer failed with a 400 while all 11 pipeline tests passed, because they use a fake chat model and the failure lived entirely in the adapter's request shape. Now `OPENAI__CHAT_TEMPERATURE`, omitted unless set (an explicit `null` is rejected too, so the key must be *absent*), with tests covering both branches.

Full suite: **506 tests pass** (up from 464), alongside clean `ruff`, `mypy --strict` and all three `import-linter` contracts.

The live run proved the whole chain against real providers: Qdrant search → OpenAI embedding → Cohere rerank → streamed answer *"This domain is for use in documentation examples without needing permission. Yes, you may use it in examples. [1]"*, citation validated. And the grounding refusal held: *"What is the CEO of Acme Corporation paid annually?"* → **"The sources do not contain the answer."**

**A second issue the live run exposed, in the console rather than the API:** the BFF proxy called `upstream.text()` on every response, which waits for completion — so a streamed answer would have been buffered whole and delivered at once, silently converting streaming back into waiting. `text/event-stream` is now piped through.


### Part B — the public widget surface

A tenant embeds a `<script>` on their own site; visitors ask questions and get answers grounded in one knowledge base. **This is the only place in the platform where an unauthenticated stranger reaches tenant data**, so it was built with its own threat-model pass rather than by extending the authenticated surface.

| Piece | File |
|---|---|
| `chat_widgets` table | `alembic/versions/d1a4c73e59b8_*.py` |
| Session tokens (separate audience) | `infrastructure/security/widget_token.py` |
| Origin matching | `domain/ai_resources/entities.py::ChatWidget.permits_origin` |
| Public use cases | `application/ai_resources/public_chat.py` |
| Fail-closed quota | `infrastructure/cache/widget_quota.py` |
| Tenant-side management | `application/ai_resources/manage_chat_widget.py` |
| Public routes | `api/v1/public_chat/router.py` |

**The audience separation is the boundary, and it is structural.** `PyJwtService.verify` pins the console audience; `WidgetTokenService.verify` pins the widget one. PyJWT rejects each token at the other verifier before any application code runs — nobody has to remember a check. Widget claims carry **no user id, membership or permissions**: a visitor is not a user, and there is no field for code to mistakenly resolve as one. Threat-model scenario 15, mutation-tested.

**The public key is an identifier, not a secret.** It ships in a script tag on a public page. It is not hashed at rest (hashing a value the internet can read protects nothing, and the console must display it back) and it does not authorize an answer. What binds a widget to a site is the origin allowlist; what bounds cost is the daily cap.

**Six decisions worth not re-litigating:**
1. **The public path reaches the *same* pipeline.** `AnswerQuestion.answer_from_namespace` was extracted so both front doors meet there. A parallel implementation would be a second place for the groundedness rules to drift out of, and "no passages, no generation" is not a property to have two versions of.
2. **The widget is re-read on every question**, not only at session issuance. Tokens live 30 minutes; without this, disabling a widget would take effect as sessions expired — "turn it off" would mean "in half an hour".
3. **Quota is consumed before generation.** Checking afterwards would only record that the money was spent.
4. **The quota store fails closed.** An unconfirmable limit refusing a question is right; becoming unlimited when Redis is down is the one outcome the counter exists to prevent, and it would be invisible until the bill arrived.
5. **Routes live at `/v1/public/chat/*`, not under `/v1/tenants/{id}`.** A public endpoint inside the tenant tree is one forgotten dependency away from looking protected while it is not. No tenant id appears in any public path.
6. **Visitors get citation *locations*, never chunk or document ids.** Those are internal identifiers; publishing them would expose the shape of a tenant's corpus for no reader benefit.

**An honest limit, recorded rather than glossed (threat-model scenario 17, marked ⚠ Partial).** The origin allowlist is real against a *browser* — page JavaScript cannot forge `Origin` — so it does stop another website embedding a tenant's widget. It is **not** a defence against a non-browser client, which can send any origin it likes. Against that, the daily cap and per-IP rate limiting are the actual controls, and they bound cost rather than preventing abuse.

**Driven live against the running server**, and every security property held: a widget session token returns **401 against both the tenant API and the platform user directory**; wrong origin, suffix-lookalike (`evil-help.acme.test`) and missing `Origin` all 403; an unknown key returns the same 404 as a disabled widget; CORS echoes the validated origin with `Vary: Origin`, never `*`; an anonymous visitor got a correct cited answer with **source locations only, no chunk or document ids**; a question the corpus cannot answer returned *"The sources do not contain the answer."*; and **disabling the widget made a still-valid session token fail immediately** rather than at expiry. No defects found — the second phase running to reuse an already-live-proven path.

**Verified:** `tests/security/test_widget_token_isolation.py` (8) proves the boundary holds in both directions and fails when the audiences are made equal; `tests/security/test_public_widget_surface.py` (14) covers unknown keys, disabled widgets reporting the same error as missing ones (no probing oracle), disallowed origins including the `evil-help.acme.test` suffix case, mid-session disable and origin removal, a token naming another knowledge base, namespace derivation, and quota refusing before generation.

---

## Phase 14 — Embeddable widget + public chat surface ✅

**Goal:** the `<script>` tag in the diagram's top box. Done.

New: `api/v1/public_chat/widget.js` (served by `GET /v1/public/chat/widget.js`), `api/middleware/public_cors.py`, `SetChatWidgetStatus` + `POST .../chat-widgets/{id}/status`, `public_api_base_url` in `core/config.py`, and an **Embed** dialog on the tenant console's Knowledge Bases screen.

### The widget script

One file, no framework, no build step — which is *why* it is a plain `.js` served by the API rather than a module in the Next.js console. Four constraints, each ruling out the obvious approach:

1. **It runs on someone else's page**, so it declares no globals (an IIFE) and renders in a **shadow root**. Verified against a deliberately hostile host stylesheet (Comic Sans, `content-box`, yellow dashed 30px buttons); none of it reached the widget.
2. **`EventSource` is unusable** despite this being SSE: it only issues GET and cannot set `Authorization`, and the session token must not travel in a query string where it lands in access logs and `Referer`. The stream is read from `fetch` and the frames parsed by hand — buffering the tail so a frame split across two network chunks doesn't tear a word in half.
3. **The answer is written with `textContent`, never `innerHTML`.** It is model output built from documents a tenant uploaded, rendered on a *customer's* page; treating it as markup would turn a poisoned document into script execution on someone else's site. A test asserts the single legitimate `innerHTML` (the static shell) is the only one.
4. **The public key stays an identifier, not a secret.** It ships in the page source and authorizes nothing on its own.

### Three defects, all found by running the real artifact — none visible to `curl`

The Phase 13B security tests drive use cases directly and the Phase 13B live pass used `curl`. Neither sends a CORS preflight. Only a browser does.

1. **The widget could not have worked on a single real page.** `OPTIONS /v1/public/chat/session` returned `400 Disallowed CORS origin, method` from the global `CORSMiddleware`, whose allowlist is the console's deploy-time origin list — the exact opposite of a per-widget allowlist that lives in the database and changes when a tenant edits it. Fixed with a prefix-scoped middleware that answers preflight for `/v1/public/chat/*` **before** the global one. A parametrised test asserts the rest of the API is *not* widened, because that confinement is the entire safety argument.

   Preflight is answered permissively and that is not a hole: a preflight carries no body and no `Authorization`, so the widget being addressed is genuinely unknowable at that moment, and a preflight only grants permission to *send*. Enforcement stays on the real request.

2. **Every widget error looked identical to the visitor.** Error responses carried no `Access-Control-Allow-Origin`, so the browser discarded them and handed the page a bare `TypeError` — making the widget's 401/404/429 branches unreachable dead code. A visitor who had hit the daily cap, the one failure they can act on, was told "unavailable right now". Errors now echo the requesting origin; their bodies are the deliberately opaque no-oracle strings, so there is nothing in them to protect.

   **Success responses deliberately still echo the *validated* origin**, not the requester's — verified live: a stolen session token replayed from `https://evil.test` gets `Access-Control-Allow-Origin: http://localhost:8090`, so the thief's browser refuses to hand them the answer.

3. **The console would have handed every tenant a snippet that pointed at the wrong host.** It has no public backend origin by design (all calls go through a same-origin server-side proxy), so the URL it assembled pointed at that authenticated proxy. The API now builds the snippet — `public_api_base_url`, falling back to the request's own base URL — so one builder means the pasted line cannot drift from where the script is actually served.

### The off switch

`manage_chat_widget.py` had only Create and List, so disabling a widget required a database session. That is the control the rest of this design leans on: the origin allowlist only binds browsers, and the daily cap only bounds spending after the money is gone. `SetChatWidgetStatus` is one use case for both directions (they differ by a boolean and share every check), permission-gated on `tenant.documents.upload` and audited.

### Driven live, end to end

A third-party page on `http://localhost:8090` embedding the script from `http://localhost:8000`: both preflights fired and returned 204, the session minted, the answer streamed and cited *"the domain is for use in documentation examples without needing permission. [1]"* — then **disable via the console → the same still-valid 30-minute token was refused immediately** with the specific message, and re-enable → answering again. The console's Embed dialog created the widget, showed the correct snippet, and toggled it off and on.

### Measured, not eyeballed: the stream is real, and it barely helps

Phase 13's live drives proved a streamed answer *arrived correctly*. That is a weaker claim than it sounds — a fully buffered stream also arrives correctly, just late and all at once, which is exactly the BFF-proxy defect Phase 13 found. So Phase 14 measured timing rather than content, against the real endpoint with raw sockets.

**The first attempt was wrong and nearly shipped a false verdict.** Driving the middleware through `httpx.ASGITransport` reported BUFFERED — until a control run with *zero middleware* reported BUFFERED identically. `ASGITransport` collects the whole body; a middleware test conducted through a buffering transport can only ever say "buffered", whatever the middleware does. Over a real socket the complete stack (`CorrelationId → Metrics → RateLimit → SecurityHeaders → PublicChatCors → CORS`) delivers at the server's own cadence, first frame at t=0.03s.

End to end against the live widget endpoint, warm:

| | run 1 | run 2 |
|---|---|---|
| retrieval + rerank done (`sources` frame) | 2.51s | 1.98s |
| first model token | 5.20s | 7.47s |
| last model token | 5.85s | 7.77s |
| **share of the wait before any character appears** | **89%** | **96%** |

Nothing in this codebase buffers: 25–33 *separate* network deliveries carried tokens (not one flush), the `sources` frame arrives seconds ahead of them, and `OpenAIChatModel.stream_answer` yields each delta as it comes. The pre-token time is `gpt-5.5` thinking — a reasoning model emits nothing until it is done — plus ~2s of retrieval and reranking.

**The finding is therefore a UX one, and it was invisible to every content-based check:** streaming works perfectly and buys the visitor almost nothing, because ~90% of the wait happens before the first token exists. The widget put up an empty grey bubble and left it empty for five to eight seconds. It now shows three pulsing dots until the first character lands, built from elements rather than text so the `aria-live` log does not announce the wait, with a `prefers-reduced-motion` fallback. Both no-token exit paths clear the indicator — dots left pulsing forever is worse than a plain failure, because it never resolves.

**Full suite: 522 tests pass** (up from 506), alongside clean `ruff`, `mypy --strict` and all three `import-linter` contracts.

### Packaging

`widget.js` is a non-Python file inside the package; `[tool.setuptools.package-data]` names it explicitly rather than relying on `include-package-data` defaults. An editable install serves it either way — which is precisely how Phase 11's `.dockerignore` bug stayed invisible until a real build. **Verified by building a wheel and listing its contents**, not by reading the config: `iam_platform/api/v1/public_chat/widget.js` is in it.

### One deliberate deviation from this section's own plan

The plan says branding should be **console-manageable** alongside the allowed domains. The domains are — they are a security control and must be. Branding is not: `data-title`, `data-accent` and `data-greeting` are read from the script tag instead, and there is no database column for them.

The reasoning is that the person embedding the widget is already editing that line of HTML, and per-page control (a different greeting on the pricing page than the docs page) is something a single stored value cannot express. The cost is honest: changing the colour needs a site edit rather than a console visit, and a tenant admin who does not own the website cannot restyle it themselves. If that turns out to be the wrong trade, the fix is a `branding jsonb` column on `chat_widgets` returned by the session endpoint, with the data attributes kept as overrides — the script already reads its config in one place.

---

---

## Performance pass — after Phase 14

Two defects raised in review after the pipeline was complete. Both were settled by
measurement rather than argument, and neither touches the security model.

### Docling ran on every rich document, including text-native PDFs

Docling infers text from a **rendered image** of each page using ML layout models
(DocLayNet, TableFormer) through torch. That is exactly right for a scanned document and
exactly wrong for a PDF exported from Word, which already carries the author's own text in
an embedded layer — reading that layer is a *parse*, not an inference. This deployment made
it worse still: the hardened image ships no C++ compiler, so `TORCH_COMPILE_DISABLE=1`
forces eager execution.

Measured on a one-page PDF, warm process:

| | |
|---|---|
| docling (ML layout models) | **14,789.7 ms** |
| pypdfium2 text layer | **10.9 ms** |
| | **1,351×** |

`infrastructure/parsing/fast_pdf.py` reads the text layer first and **declines** when there
is none, so scans still reach docling's OCR. Three decisions worth not re-litigating:

1. **Declining is a distinct signal from failing.** `ParserDeclined` means "this file is
   fine, another parser suits it better" and the dispatcher falls through. A
   `DocumentParseError` deliberately does *not* fall through — a broken file must report its
   own reason rather than be retried by a parser that will also fail, hiding the real cause.
2. **The coverage ratio is 0.6, not 1.0.** Real reports contain full-page diagrams and blank
   separators; one of those must not push a 100-page document through OCR. A *mostly*
   scanned document still goes to docling whole, rather than silently losing its image pages.
3. **pypdfium2, not PyMuPDF.** PyMuPDF is the better-known choice and is AGPL-3.0 or paid
   commercial — network copyleft, which is a real liability for a hosted multi-tenant
   product. pypdfium2 wraps PDFium (the engine in Chrome), is BSD-3-Clause/Apache-2.0, and
   was already an indirect dependency via docling. It is now declared **directly**: relying
   on a transitive dependency for something load-bearing is one upstream refactor from
   breaking.

**Citations improved, not just speed.** Page numbers come free from the text layer, so a
passage cites `"page 7"`; the docling path falls back to headings because it reports pages
inconsistently across formats. Confirmed in the live worker — `pdf parsed via text layer
(1 pages, 1 blocks)`, document `ready`, `source_location = "page 1"`.

### The answer path had an unbounded reasoning tail

`OPENAI__CHAT_REASONING_EFFORT` is sent only when set — the same opt-in shape as
`chat_temperature`, and for the same reason: a non-reasoning model rejects the parameter
outright. `gpt-5.5` accepts `"low"` and **rejects `"minimal"`**, which is why the value is
passed through unvalidated: a local allowlist would go stale and start refusing values the
API accepts.

The first measurement was a single sample pair and overstated this as "4.4× faster". Six
questions each, real prompt, time-to-first-token:

| | min | median | max |
|---|---|---|---|
| unset | 1.03s | 2.11s | **10.80s** |
| `"low"` | 0.83s | 1.24s | **1.58s** |

**The median barely moves; the tail collapses.** Left unset, the model occasionally spends
eleven seconds thinking about a question the retriever had already answered — and a
reasoning model emits nothing at all while thinking, so that is a visitor watching an empty
bubble. One such wait is what closes a tab, which makes the worst case the number that
matters here.

### What this did not fix

Retrieval is now the dominant term: **~2.5s of a ~5.6s answer**, spent on three serial
network hops (OpenAI embedding → Qdrant → Cohere rerank) with no caching of any kind. The
remaining items from the same review, in impact order: HTTP-first crawling with concurrency
(the crawl loop is still strictly sequential behind a full Chromium), an evaluation harness
before any further retrieval change, hybrid BM25 + dense retrieval, structure-aware chunking
that actually uses docling's output, and splitting the Celery queues so a two-hour site
crawl cannot block document parsing.

## Knowledge-base management pass — after the performance pass

Raised as a seven-part review of the knowledge-base surface: one ingestion flow for both
upload paths, a modal that stays inside the viewport, verified URL ingestion, the failing
PDF, source management, honest ingestion status, and tenant isolation on the new routes.

### The defect underneath the rest: zero chunks counted as success

A scanned 40-page PDF reached `ready` with **0 chunks**. It was in the knowledge base, it
looked ingested, and it could never answer anything — the worst of the three possible
outcomes, because a visible failure gets retried and a silent one does not.

The cause was that `index_blocks` returning `0` was not an error anywhere. It still is not,
*in the indexer* — one navigation-only page in a 500-page crawl is genuinely not a failure —
so the count is returned and **each caller decides**:

- `process_document_upload.py` raises `DocumentParseError`. A file a tenant uploaded and
  expects to search is a failure at zero, and the message distinguishes "nothing was
  extracted" (likely a scan) from "read, but no indexable text".
- `process_url_crawl.py` marks the page failed **by returning, never raising** — the failure
  write happens in its own transaction, and raising inside the `session.begin()` block would
  roll back the very status it is trying to record. That is
  [docs/18's rollback pitfall](18-schema-rls-and-migrations.md), and it was written and caught
  during this pass rather than avoided by memory.

`documents.failure_reason` was also never persisted: `AiResourceDocumentRepository.save()`
updated `status` and `deleted_at` and silently dropped the reason, so even a correctly failed
document showed no explanation. Fixed in the same repository method.

### Retry and delete, because a failure the tenant cannot act on is not much better

`application/ai_resources/manage_document.py` adds `RetryDocumentIngestion` and
`DeleteDocument`, both on `tenant.documents.upload` — changing what a knowledge base contains
is one authority whether that means adding a file or removing one, and a second permission
granted alongside the first is how tenants end up able to upload but not clean up.

Two things worth not re-litigating:

1. **Authorizing the knowledge base is not authorizing the document.** Both use cases check
   `document.knowledge_base_id != knowledge_base_id` and raise `DocumentNotFoundError`. The
   repository is RLS-scoped, so a cross-*tenant* id is already invisible; this closes the
   cross-*knowledge-base* case inside one tenant, which RLS cannot see. Verified live: wrong
   KB and other-tenant ids both answer **404**, never 403 — a resource the caller cannot see
   must not be provable to exist.
2. **Delete order is vectors → chunk rows → stored bytes → soft-delete.** Vectors first
   because that is the copy a query can still reach: an orphaned point keeps answering
   questions and citing a source the tenant was told is gone. Bytes are best-effort, since
   refusing the whole delete over a storage hiccup leaves a document that cannot be removed.

Mutation-tested rather than assumed: removing the cross-KB guard and removing the vector
delete each fail `tests/unit/ai_resources/test_manage_document.py` (4 of 8), and restoring
them passes.

### Verified against the live stack

Retry on the real failing PDF: `ready`/0 chunks → `processing` with the old reason cleared →
`failed` with *"no text could be extracted … if it is a scanned document, the page images may
be too large or too complex to read on this worker"*. The fast PDF path correctly **declined**
first (`0/40 pages` carry a text layer) and deferred to docling, which is the intended split.

Delete, on a CSV indexed for the purpose: 2 chunk rows and 2 Qdrant points before, **0 rows,
0 points, no stored file and the collection back to its prior 23 points** after — so the
"no orphaned Qdrant points" requirement is measured, not asserted.

### Two frontend defects, both invisible to a typecheck

1. **Rejected files were reported to nobody.** `addFiles` collected its rejection messages
   *inside* the `setStaged` updater and toasted them after the call — but React defers an
   updater to the render phase, so the array was still empty when the toast loop ran. Every
   "unsupported file type" / "larger than 50 MB" / "already in the list" message was
   computed and discarded. Validation is now pure and outside the updater, which also makes
   StrictMode's double-invoke harmless (the reason the code had been written that way).
2. **A long note ran straight across the columns to its right.** `TableCell` sets
   `whitespace-nowrap`, correct for the short cells and fatal for the failure-reason and
   empty-document notes. Fixed locally with `whitespace-normal` rather than by changing the
   shared cell.

The modal overflow was fixed at the root — `DialogContent` itself gained
`grid-cols-[minmax(0,1fr)] max-h-[calc(100dvh-2rem)] overflow-y-auto`, so every dialog in the
console is bounded, not just this one.

### Still open

The Qdrant `Api key is used with an insecure connection` warning is **accurate and local-only**
— the dev instance genuinely accepts unauthenticated HTTP. Left as-is deliberately: it is a
warning about the dev topology, not a defect in the code, and per the review's own instruction
working behaviour is not changed because a log line is yellow. Blanking `QDRANT__API_KEY`
locally silences it.

### The document inspector, and re-syncing a crawl

Both were the outstanding half of source management, and both are deliberately thin over
machinery that already existed.

**`GetDocumentDetail` returns the text that was actually indexed.** Status and chunk count
say *that* something went wrong; only the extracted passages say *what*. It reads through a
new `DocumentRepository.list_chunks(limit, offset)` — paged, capped at 50, since a large PDF
runs to hundreds and this is read by a person, not by retrieval. It requires **read** access
only (`for_modification=False`): the same passages are already reachable by asking the
knowledge base a question, so demanding modify rights would withhold the diagnosis while
protecting nothing. It still makes the cross-knowledge-base check the mutating paths make.

**It earned its place on first use.** Opening the crawled London Academy of IT page showed
that **chunk 1 is the cookie-consent banner** — 700 tokens of "We use cookies to enhance your
browsing experience" indexed as course material. Nothing in the status, the chunk count or
the retrieval tester would ever have said so.

**`ResyncDataSource` adds no second pipeline.** It re-enqueues the *same* job `CreateDataSource`
enqueues, because that job was already idempotent by construction: a crawled page is looked
up by `(knowledge_base_id, source_url)` and updated rather than inserted (backed by
`uq_documents_source_url_per_kb`), and `index_blocks` deletes a document's chunks and vectors
before writing new ones. Proven live rather than argued: re-crawling left **one** document
row with the same id, its chunks replaced 23 → 18, and Qdrant holding exactly 18 — refreshed,
not accumulated.

Two decisions inside it:

1. **Stored URLs are re-validated, not trusted because they passed once.** A hostname that
   resolved to a public address at creation can resolve somewhere internal by the time
   someone presses re-sync; resolving-then-checking is only meaningful at the moment of use.
2. **Documents for pages that have vanished are left alone.** Deleting a tenant's indexed
   content as a side effect of a refresh is not a decision a refresh should make — a site
   that 404s briefly during a deploy would otherwise quietly empty a knowledge base.

Both are mutation-tested: dropping the re-validation, the cross-knowledge-base check, the
chunk-page cap, or the document-total count each fails a test that passes when restored.

**Two schema facts closed on the way.** `Document` never carried `source_url` despite the
column existing since Phase 12, so nothing above the repository could tell a crawled page
from an uploaded file; and `DataSourceRepository` had no `save()`, so a status transition
had nowhere to go outside the worker's raw SQL.

**A layout defect only a real document exposed.** Crawled markdown is full of long unbroken
asset URLs, and `whitespace-pre-wrap` preserves line breaks without breaking a long token —
so the chunk list scrolled sideways. `break-words` on the passage and `truncate` on the
per-chunk source URL fix it; measured at `scrollWidth === clientWidth`, not eyeballed.

Not built in this pass: **re-embedding** without re-fetching. The stored markdown makes it
possible (that is why it is stored) and it is the right response to an embedding-model
change, but it needs a job that reads `document_chunks` and re-embeds in place rather than
re-running the parse.

## Decisions I'm assuming — flag any of these you want changed

Stated up front per this project's own rule ("state assumptions before implementing anything non-obvious" — CLAUDE.md ground rules):

1. **OpenAI/Cohere keys are platform-level config by default**, using the key(s) you provided, with the *existing* `provider_credentials` mechanism (Fernet-encrypted, built in Phase 7) available for a tenant to later bring their own key — not rebuilding a second credential system.
2. **Qdrant runs in `docker-compose.dev.yml` for local development**, pointed at your real self-hosted server for staging/production via `QDRANT__URL` — same pattern as every other environment-switched dependency in this project. If you already have a shared dev Qdrant instance you'd rather point at instead, say so and this changes to a config value, not a new container.
3. **Chunking defaults to 700 tokens / 100 token overlap** (from the diagram), configurable, not hardcoded.
4. **Crawl limits** (Phase 12): defaulting to a max depth and max page count per job (exact numbers decided at implementation time) to bound cost/risk — needs your sign-off on what's reasonable for your use case before it's load-bearing.
5. **File upload limits**: a per-file size cap and the exact accepted MIME types (PDF/DOCX/XLSX/PPTX/CSV/JSON/XML/images per your list) will be enforced at the API boundary, not just assumed from the file extension.
6. **`gpt-5.5`** is taken as a literal configured model string, not validated against a known model list — if OpenAI rejects it at call time, that surfaces as a normal upstream API error, not something this plan second-guesses now.

## What does *not* need to be rebuilt

Worth naming explicitly, since it's easy to assume a RAG feature starts from zero: tenant isolation (RLS + the mirrored Qdrant-collection-per-tenant scheme), the four-mode visibility system, the audit-log pattern, the `SecretProvider` abstraction, the server-derived-path/namespace guarantee, and the provider-credential encryption boundary are all already built and get *reused*, not reinvented, across all five phases.
