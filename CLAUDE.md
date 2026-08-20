# Enterprise Multi-Tenant Identity, Authentication & Authorization Platform

## What this project is

A production-grade identity/authentication/authorization service for an enterprise, multi-tenant AI Assistant SaaS platform. Supports platform-level and tenant-level users, multi-tenant membership, hierarchical RBAC, resource ownership, secure support impersonation, OAuth/OIDC social login, and future ABAC/policy-engine integration. Designed for thousands of tenants and millions of users.

Full requirements: [docs/02-requirements.md](docs/02-requirements.md). Full spec index: [docs/README.md](docs/README.md).

## Tech stack

Python 3.13+, FastAPI, Pydantic 2, SQLAlchemy 2.0 Async, Alembic, PostgreSQL (with Row-Level Security), Redis, OAuth 2.0 / OIDC, JWT + rotating refresh tokens.

## Delivery workflow — read before doing anything

This project is built in the 9 phases below, **one phase per work session, never all at once**. Before starting work, check [docs/09-roadmap.md](docs/09-roadmap.md) for the current phase status and pick up from there — do not regenerate completed phases.

1. Requirements, assumptions, threat model — ✅ done ([docs/01-assumptions-and-scope.md](docs/01-assumptions-and-scope.md), [docs/02-requirements.md](docs/02-requirements.md), [docs/03-threat-model.md](docs/03-threat-model.md))
2. Architecture and authorization flows — ✅ done ([docs/04-architecture-overview.md](docs/04-architecture-overview.md), [docs/05-authentication-flows.md](docs/05-authentication-flows.md), [docs/06-authorization-model.md](docs/06-authorization-model.md))
3. Database schema, ER diagram, constraints, RLS — ✅ done ([docs/10-schema-conventions.md](docs/10-schema-conventions.md) through [docs/18-schema-rls-and-migrations.md](docs/18-schema-rls-and-migrations.md), 51 tables across 7 domains)
4. Folder structure and dependency rules — ✅ done ([docs/19-folder-structure.md](docs/19-folder-structure.md), [docs/20-dependency-rules.md](docs/20-dependency-rules.md), [docs/21-configuration-and-secrets.md](docs/21-configuration-and-secrets.md))
5. Authentication (code) — ✅ done — see "Phase 5 — what's built" below
6. Platform and tenant authorization (code) — ✅ done — see "Phase 6 — what's built" below
7. APIs and AI-resource policies (code) — ✅ done — see "Phase 7 — what's built" below
8. Tests and security validation — ✅ done — see "Phase 8 — what's built" below
9. Deployment and operations — ✅ done — see "Phase 9 — what's built" below

**All 9 phases are complete.** Continuing work means extending a finished system, not starting a new phase: read the relevant "Phase N — what's built" section first, and check [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md#known-gaps) for the consolidated list of what was deliberately left unbuilt.

**Phases 10–14 — Knowledge Base Ingestion & RAG Query Pipeline.** Full spec, phase boundaries, and per-phase implementation notes: [docs/24-knowledge-base-ingestion-and-rag.md](docs/24-knowledge-base-ingestion-and-rag.md). Turns the Phase 7 AI-resource ports into a real pipeline using Qdrant (self-hosted), Cloudflare R2/local filesystem for storage, OpenAI (`text-embedding-3-large` + a configured chat model), Cohere for reranking, and `crawl4ai`/`docling`/`langgraph` for crawling/parsing/orchestration — culminating in an embeddable public widget with its own session-token auth surface (distinct from the platform/tenant JWT model). **Phases 10–14 are done** — the pipeline is complete, from an uploaded file or crawled site through to an embeddable widget answering a stranger's question on a third-party page.

## Phase 10 — what's built

Real storage and vector infrastructure, behind the *same* ports Phase 7 defined — no new user-facing behavior, but uploads and queries now touch real systems instead of memory. New settings groups (`StorageSettings`, `QdrantSettings`, `OpenAISettings`, `CohereSettings`, `TavilySettings`, `IngestionSettings`), a new `ObjectStorageClient` port with local-filesystem and Cloudflare R2 adapters, a new `EmbeddingClient` port with an OpenAI adapter, a real Qdrant-backed `VectorSearchClient` (extended with `ensure_namespace`/`upsert`/`delete_document` for Phase 11 to use), and a `qdrant` service in `docker-compose.dev.yml`.

**Six decisions worth not re-litigating**, all detailed in docs/24's Phase 10 section:
1. **Collection-per-tenant, not per-knowledge-base.** `vector_namespace` (`{tenant_id}/{kb_id}`) maps to collection `tenant_{uuid.hex}` with `knowledge_base_id` as an indexed payload filter. Per-KB collections wouldn't survive the "thousands of tenants" this platform is sized for; isolation still lands on the collection boundary, the security-critical one.
2. **Namespace parsing lives beside the builder** (`infrastructure/vector/namespaces.py`), so the format's producer and consumer can't drift.
3. **An unconfigured deployment gets `UnconfiguredVectorSearchClient`, which raises** — never the in-memory fake. Falling back to in-memory would answer every search with an empty result set, indistinguishable from a knowledge base with genuinely no matches, and invisible in logs. Same "inert by design" shape Phase 9 found three of.
4. **`embedding_dimensions` is requested from the API *and* used as the collection's vector size** — one number, so the index and the vectors cannot desynchronise.
5. **`build_object_storage_client` is deliberately not wired into `AppContainer` yet** — nothing reads or writes bytes until Phase 11's upload route and worker; an unused container field is one more inert wire to mistake for a working feature.
6. **Qdrant is not in `/readyz`.** The API serves IAM traffic fine with the vector store down; failing readiness on it would pull every pod from rotation over a degraded RAG feature.

**Verified against a live Qdrant, not a mock** — `tests/integration/test_qdrant_vector_store.py` proves two tenants indexing an *identical* vector each see only their own, and that two knowledge bases within one tenant stay filtered apart; a mock could only assert that the code passed the filter it was told to. `tests/unit/ai_resources/test_object_storage.py` proves path traversal is refused on read, write **and** delete (a guard on `put` alone would still leak arbitrary file reads — the escape was demonstrated empirically before the test was written), and that an R2 `AccessDenied` is not flattened into "not found".

**Status:** `ruff check src tests scripts`, `mypy src` (strict), `lint-imports` (all 3 contracts), and the full suite — **344 tests, up from 310** — all pass clean.

**Two environment notes:** the venv had lost its dev extras (`ruff`/`mypy`/`pytest`/`import-linter`) when the RAG libraries were installed — `pip install -e ".[dev]"` restores them, and the resulting mypy 2.x upgrade needed one pre-existing `no-any-return` fixed in `redis_client.py` plus a scoped `[[tool.mypy.overrides]]` for the stub-less boto3/qdrant/cohere packages. And **`qdrant-client` refuses more than one minor version of server skew** — `pyproject.toml` and `docker-compose.dev.yml` must be bumped together (currently both 1.19).

## Phase 11 — what's built

The background ingestion pipeline: an uploaded file now actually becomes searchable. The `workers/` runtime — reserved in docs/19 since Phase 4 but never built — exists for real as a Celery app over the Redis that was already running. Upload is a **real multipart endpoint** (it previously accepted JSON *metadata* about a file whose bytes went nowhere), a new `GET .../documents` route backs the console's status view, and the Knowledge Bases screen has drag-and-drop upload with a document list that polls while anything is `processing`.

New: `workers/{celery_app,bootstrap,main,job_context}.py` + `workers/jobs/`, `infrastructure/parsing/{dispatcher,rich_documents,structured,chunking}.py` (docling for PDF/DOCX/XLSX/PPTX/images; stdlib parsers for CSV/TSV/JSON/XML, each carrying a row or JSON-path source location), `infrastructure/queue/celery_queue.py`, and migration `b7e3c210df94` adding `document_chunks` (with RLS) plus `documents.failure_reason`.

**`workers/job_context.py` is the security core, and it closes threat-model scenario 8.** Phase 8 could only record that scenario as ⚠ Partial because no worker runtime existed to attack. A job carries a `tenant_id` and `actor_user_id` that were authorized *when it was enqueued* — minutes or hours before it runs, during which the tenant can be suspended, the membership revoked, or the user deleted. The context sets RLS from the **claimed** tenant first and only then validates tenant → membership → user account, in that order deliberately: the validation queries themselves then run under the claimed tenant's own RLS scope, so a forged `tenant_id` can't read another tenant's rows even during the check that rejects it. `tests/security/test_worker_job_revalidation.py` (9 tests) proves refusal for a suspended tenant, revoked membership, suspended membership, suspended user, deleted user, cross-tenant claim and nonexistent tenant/user — plus a positive case, so the seven refusals can't be passing vacuously.

**Six decisions worth not re-litigating**, detailed in docs/24's Phase 11 section:
1. **Bytes are written before the row is created.** Orphan bytes beat orphan rows — an orphaned object is invisible dead weight a sweep reclaims; a `documents` row pointing at bytes never written is a document that fails for a reason the tenant can do nothing about.
2. **Failures are recorded in a separate transaction, after the failing one unwinds** — docs/18's rollback pitfall in its most damaging form. A unit test asserts the invariant directly: *no exit path leaves a document in `processing`*.
3. **Tenant-visible failure reasons are deliberately lossy** (`_readable_reason()`); the exception goes to the log. A stack trace in the console is useless to a tenant admin and a small internals leak.
4. **The job is idempotent by deleting chunks/vectors first**, so `acks_late`'s at-least-once delivery can't double-index.
5. **Unsupported types are refused at upload with a 415**, not asynchronously — the person is still at the keyboard.
6. **`workers/` must not import `api/`.** Building the worker container originally reached into `bootstrap.py`, which drags `api.main` into the worker's import graph; the shared factories moved to `infrastructure/factories.py`. import-linter caught this *after* I had waved it away in a docstring as harmless — it wasn't.

**The defect that mattered most was invisible to every test in the phase.** All of them import `process_document_upload` directly and drive it with fakes — proving the pipeline correct, and proving nothing about whether the worker can *start*. It couldn't: `build_celery_app()` called `autodiscover_tasks(..., force=True)`, so discovery ran while `celery_app.py` was still executing its module body, imported `workers/jobs/tasks.py`, and that module's `from ...celery_app import celery_app` hit a name that didn't exist yet. The worker died on an `ImportError` before accepting a single job — the whole phase inert, suite green. Found by *running the entrypoint*, not importing its parts. Discovery is now lazy (Celery runs it at worker startup), guarded by `tests/unit/workers/test_celery_app_wiring.py`, which imports `workers.main` exactly the way the `celery` CLI does.

**Phase 11 was then driven end-to-end against the live stack** — real API, real storage, real Redis queue, real Celery worker, real OpenAI embeddings, real Qdrant. A CSV and a PDF both reached `ready`; retrieval routes correctly across them (*"When will my parcel arrive?"* → the shipping PDF, *"Can I get my money back?"* → the refund CSV, neither sharing keywords with its source); a redelivered job leaves 3 chunks and 3 points rather than 6; an `.exe` is refused with 415 and no bytes written. **Doing so found four more defects the 397-test suite was blind to, plus a production image that could not be built** — all detailed in docs/24's "Driven end to end" section:

1. **docling compiles C++ at parse time.** TorchInductor generates and compiles C++ *when a document is parsed*. The Phase 9 hardened image ships no compiler by design, so every PDF/DOCX/XLSX/PPTX/image would have failed in production with `InvalidCxxCompiler` while CSV/JSON/XML kept working — a partial failure that reads like a bad file, not a bad deployment.
2. **The first fix for that was not a fix.** `TORCHDYNAMO_DISABLE` is silently ignored by torch 2.13 (`torch._dynamo.config.disable` stays `False`); only `TORCH_COMPILE_DISABLE` works. A manual test appeared to pass with the dead name.
3. **`OPENAI_API_KEY` vs `OPENAI__API_KEY` was silently swallowed.** `extra="forbid"` catches a name matching *nothing*, but pydantic-settings claims a near-miss by prefix and then drops it because the delimiter is `__` — no error, no value. `_reject_near_miss_group_names()` in `core/config.py` now refuses to start and names the correct spelling, while staying quiet if the correct name is also set.
4. **`.dockerignore` excluded `scripts/` while the Dockerfile copies it**, so `docker build` failed outright — the `COPY` was added in a later session than the last image build. DEPLOYMENT.md documents production bootstrap running those scripts from inside the image, so the ignore entry was the mistake.

Plus a second Dockerfile bug caught the same way: the model pre-fetch layer ran `DocumentConverter()`, which downloads nothing (construction is lazy) — `initialize_pipeline(InputFormat.PDF)` is what forces it. The image now bakes the docling models in at build time with `HF_HOME` pointed at a persistent path; previously they were pulled from Hugging Face on first parse into an ephemeral container cache, needing worker egress to huggingface.co.

**The lesson, three times over in one phase: the only trustworthy check is running the real artifact.** Every one of these passed inspection, and two of them passed a manual test.

**Two schema facts found by running things, not reading them:** `document_chunks` is the first composite-FK reference to `documents`, and Postgres refused it until `uq_documents_tenant_id_id` existed (a composite FK needs a matching UNIQUE on the referenced side). And `documents.failure_reason` did not exist at all, so docs/24's "failure reasons surfaced back to the console" was unimplementable as specified — added to the table, the domain entity (`mark_ready()` clears it, `mark_failed(*, reason=None)` sets it), the API response and the UI.

**The worker is a separate process and must be deployed as one:**

```bash
celery -A iam_platform.workers.main:celery_app worker --loglevel=info
```

A deployment that starts only the API will accept uploads and leave every document sitting in `processing` forever.

**Status:** `ruff check src tests scripts`, `mypy src` (strict, 195 files), `lint-imports` (all 3 contracts), and the full suite — **408 tests, up from 344** — all pass clean; `npm run build` (25 routes), `npx tsc --noEmit` and `npx eslint src` pass.

## Phase 12 — what's built

URL and website ingestion. A tenant can point a knowledge base at a list of specific URLs or at a whole site, and the crawled pages flow through **the same** chunk → embed → upsert path Phase 11 proved working — not a parallel one.

New: `infrastructure/crawling/{url_safety,url_validator,crawl4ai_crawler}.py`, `workers/jobs/{process_url_crawl,indexing}.py`, `application/ai_resources/manage_data_source.py`, `CrawlSettings` in `core/config.py`, migration `c5f1a90b2e47` adding `data_sources` (with RLS) plus `documents.data_source_id`/`source_url`, two routes, and a crawl panel on the Knowledge Bases screen.

**`url_safety.py` is the security core, and it closes a genuinely new attack surface.** Everywhere else in this platform, tenant input decides what *their own* data is read. Here it decides **what the worker process connects to** — and the worker sits inside the deployment's network with database credentials and a route to the cloud metadata service. Unguarded, "crawl this website" is a credential-exfiltration path: point it at `169.254.169.254`, let it index the response, read the credentials back through ordinary search. Four properties, each the one most SSRF filters get wrong: scheme allowlist; **resolve then check the resolved addresses**, never the hostname; check **every** address a name resolves to; and re-check **inside the crawl loop** on every redirect and discovered link. Threat-model scenarios 13 and 14 are new and covered; the residual DNS-rebinding risk is recorded rather than glossed.

**Seven decisions worth not re-litigating**, detailed in docs/24's Phase 12 section:
1. **A crawled page is a `documents` row** — same table, same chunks, same namespace, so one query spans uploads and crawled pages.
2. **The indexer was extracted, not copied** (`workers/jobs/indexing.py`). It holds the delete-before-write ordering that makes redelivery safe and the `strict=True` zip that stops a chunk being indexed under another chunk's vector. Phase 11's 11 tests pass unchanged through it.
3. **Per-page transactions, not one per crawl** — a 500-page crawl in one transaction holds locks for its runtime and discards 498 pages of paid-for embedding if page 499 fails.
4. **The fetched markdown is stored, not just indexed** — re-embedding on a model change then needs no re-crawl of someone else's server.
5. **A crawl uses `tenant.documents.upload`**, not a new permission: same authority, different route.
6. **`CrawlJobQueue` is separate from `DocumentIngestionQueue`**, and the crawl task uses `max_retries=1` — retrying a two-hour job three times is six hours and triple the bill for a site that may simply be down.
7. **The request schema has no depth/page/timeout fields** — those are platform limits bounding what the deployment spends on a tenant's behalf; tenant-editable limits defeat their only purpose.

**Three test-quality findings, all more instructive than the code:**
- **A test that passed for the wrong reason.** The SSRF traversal test used an *off-host* unsafe link — but same-host confinement drops those anyway, so it would have passed with the guard entirely removed. Rewritten to use a same-host link on a blocked port, then **mutation-tested**: guard removed → tests fail; restored → pass.
- **A guard that could not see what it guards.** `test_exception_mapping_is_exhaustive.py` exists because unmapped exceptions surfaced as 500s three times. It *passed* for Phase 12's new errors, because they derived from `ValueError` rather than `AiResourceError` and the scan couldn't see them. Both moved under the module base class and mapped to 400.
- **The composite-FK lesson recurred.** Postgres refused `documents → data_sources` until `uq_data_sources_tenant_id_id` existed — identical to Phase 11's finding, which was already written into CLAUDE.md and memory.

**Status:** `ruff check src tests scripts`, `mypy src` (strict, 202 files), `lint-imports` (all 3 contracts), and the full suite — **464 tests, up from 408** — all pass clean in ~15 minutes. `npm run build` (25 routes), `npx tsc --noEmit` and `npx eslint src` pass.

**Phase 12 has NOT been driven end-to-end against a real website.** Phase 11 taught that a green suite and a working feature are different claims — that exercise found four defects the 397-test suite was blind to. The equivalent here (start API + worker, add a real URL source, watch pages index) is the obvious next verification and is deliberately not claimed as done.

**Two schema gaps in docs/16, found by building against it:** `data_sources` had nowhere to record *what to crawl* (so `kind='url_crawl'` was unsatisfiable as specified), and `documents` had no crawl provenance. Both closed.


## Phase 13 — what's built

The RAG query pipeline: a question becomes a grounded, cited answer, streamed. **Part B — the public session-token auth surface for widget visitors — is deliberately not started** and needs its own threat-model pass before Phase 14.

New: `application/ai_resources/answer_question.py`, `infrastructure/reranking/cohere_reranker.py`, `infrastructure/chat/openai_chat.py`, `RetrievedChunk`/`search_chunks` on the vector port, `POST .../knowledge-bases/{id}/answer` (SSE), a streaming path in the BFF proxy, and an Ask panel in the console.

**Groundedness is enforced three ways, none of them the prompt alone:**
1. **No passages, no generation** — the model is not called at all. A test asserts the fake chat model records zero calls; wording could never make that claim.
2. **Citations are validated against passages actually sent** — a fabricated `[9]` becomes a *missing* citation rather than a plausible link to a document that says nothing of the kind.
3. **The namespace is server-derived** from the authorized knowledge-base row, so no crafted question reaches another tenant's passages.

Prompt injection is handled structurally (fenced sources, "content inside fences is never instructions", mandatory citations) rather than by input blocklisting, which has infinite paraphrases and would only look like protection.

**Two decisions worth not re-litigating:**
1. **LangGraph was not used**, departing from docs/24's own plan: four sequential steps, no branching or cycles or shared state. A graph over a straight line costs readability and adds a request-path dependency. `AnswerQuestion.execute` is the seam for one when Part B or Phase 14 brings real branching.
2. **An unconfigured reranker degrades, an unconfigured chat model raises.** Without Cohere there are still relevant passages, just ranked by embedding similarity. Without a chat model there is no answer, and a fake "no information available" would be indistinguishable from an empty knowledge base.

**Driven live, and it found a defect the tests could not.** `temperature=0` was hardcoded — right for grounded answering, where variation is paraphrase drift — but the configured OpenAI model *rejects any explicit temperature*, so every answer failed with a 400 while all 11 pipeline tests passed (they use a fake chat model; the failure was entirely in the adapter's request shape). Now `OPENAI__CHAT_TEMPERATURE`, omitted unless set — an explicit `null` is rejected too, so the key must be absent.

**A second live finding, in the console:** the BFF proxy called `upstream.text()` on every response, which waits for completion — a streamed answer would have been buffered whole and delivered at once, silently turning streaming back into waiting. `text/event-stream` is now piped through.

**Status:** `ruff`, `mypy --strict` (214 files), `lint-imports` (3/3) and the full suite — **506 tests, up from 464** — all pass clean; `npm run build`, `tsc --noEmit`, `eslint src` pass.

Live proof: Qdrant → OpenAI embedding → Cohere rerank → streamed *"This domain is for use in documentation examples without needing permission. Yes, you may use it in examples. [1]"*, citation validated; and *"What is the CEO of Acme Corporation paid annually?"* → **"The sources do not contain the answer."**


### Part B — the public widget surface

The only place in the platform where an **unauthenticated stranger** reaches tenant data, so it got its own threat-model pass rather than an extension of the authenticated surface. New: `chat_widgets` (migration `d1a4c73e59b8`), `infrastructure/security/widget_token.py`, `application/ai_resources/{public_chat,manage_chat_widget}.py`, `infrastructure/cache/widget_quota.py`, `api/v1/public_chat/router.py`, plus tenant-side widget CRUD routes.

**The audience separation is the boundary and it is structural.** `PyJwtService.verify` pins the console audience; `WidgetTokenService.verify` pins `JWT__WIDGET_AUDIENCE`. PyJWT rejects each token at the other verifier before any application code runs. Widget claims carry **no user id, membership or permissions** — a visitor is not a user, and there is no field to mistakenly resolve as one. **Mutation-tested:** make the audiences equal and the boundary tests fail.

**Six decisions worth not re-litigating:**
1. **The public path reaches the *same* pipeline** — `AnswerQuestion.answer_from_namespace` was extracted so both front doors meet there. Phase 13A's 11 tests still pass through it.
2. **The widget is re-read on every question**, not just at session start: tokens live 30 minutes, and "disable this widget" must mean now, not in half an hour.
3. **Quota is consumed before generation** — checking after would only record that the money was spent.
4. **The quota store fails closed.** Redis down must not mean unlimited spending; that failure would be invisible until the bill.
5. **Routes live at `/v1/public/chat/*`**, deliberately outside `/v1/tenants/{id}` — a public endpoint in that tree is one forgotten dependency from looking protected while it is not. No tenant id in any public path.
6. **Visitors get citation *locations*, never chunk or document ids** — internal identifiers would expose the shape of a tenant's corpus.

**The public key is an identifier, not a secret** — it ships in a script tag, so it is stored in plaintext and does not authorize anything. Contrast `provider_credentials`, which genuinely is a secret and is never echoed back.

**An honest limit, recorded not glossed** (threat-model scenario 17, ⚠ Partial): the origin allowlist is real against a *browser* (page JS cannot forge `Origin`) but useless against `curl`. The daily cap and per-IP rate limiting are the actual abuse controls, and they bound cost rather than preventing abuse.

Threat-model scenarios **15, 16 and 17** are new. 22 security tests across `test_widget_token_isolation.py` and `test_public_widget_surface.py`.

## Phase 14 — what's built

The embeddable widget itself: `api/v1/public_chat/widget.js`, served by `GET /v1/public/chat/widget.js`, plus `api/middleware/public_cors.py`, `SetChatWidgetStatus` + `POST .../chat-widgets/{id}/status`, `public_api_base_url` in `core/config.py`, and an **Embed** dialog on the console's Knowledge Bases screen.

One file, no framework, no build step — which is why it is a plain `.js` served by the API rather than a module in the Next.js bundle. It declares no globals and renders in a **shadow root** (verified against a deliberately hostile host stylesheet), reads SSE from `fetch` rather than `EventSource` (which cannot set `Authorization`, and the token must not sit in a query string where access logs and `Referer` keep it), and writes the answer with **`textContent`, never `innerHTML`** — it is model output built from tenant-uploaded documents rendered on a *customer's* page, so treating it as markup would turn a poisoned document into script execution on someone else's site.

**Three defects, every one of them invisible to `curl` and to 22 passing security tests.** The Phase 13B tests drive use cases directly and its live pass used `curl`; neither sends a CORS preflight. Only a browser does.

1. **The widget could not have worked on a single real page.** `OPTIONS /v1/public/chat/session` returned `400 Disallowed CORS origin, method` — the global `CORSMiddleware` is configured with the console's deploy-time origin list, the exact opposite of a per-widget allowlist that lives in the database. Fixed with a prefix-scoped middleware answering preflight for `/v1/public/chat/*` before the global one; a parametrised test asserts the rest of the API is **not** widened, since that confinement is the whole safety argument. Answering preflight permissively is not a hole: it carries no body and no `Authorization`, so the widget being addressed is unknowable then, and a preflight only grants permission to *send*.
2. **Every widget error looked identical to the visitor.** Error responses carried no `Access-Control-Allow-Origin`, so the browser discarded them and handed the page a bare `TypeError` — making the widget's 401/404/429 branches unreachable dead code, and telling a visitor who had hit the daily cap only "unavailable right now". Errors now echo the requesting origin. **Success responses still echo the *validated* origin** — proven live: a stolen token replayed from `evil.test` gets `Access-Control-Allow-Origin: http://localhost:8090`, so the thief's browser refuses to hand them the answer.
3. **The console would have handed every tenant a snippet pointing at the wrong host** — its own authenticated BFF proxy, because it has no public backend origin by design. The API now builds the snippet (`public_api_base_url`, falling back to the request's base URL), so the pasted line cannot drift from where the script is served.

**The off switch was missing.** `manage_chat_widget.py` had only Create and List, so disabling a widget needed a database session — not an incident response, and it is the control the rest of the public surface leans on (the origin allowlist only binds browsers; the daily cap only bounds spending after the money is gone).

**Driven live end to end:** a third-party page on `:8090` embedding the script from `:8000` — both preflights 204, session minted, answer streamed and cited, then **disable from the console refused the same still-valid 30-minute token immediately**, and re-enable resumed answering.

**SSE was then measured rather than eyeballed, and the first attempt was wrong.** Driving the middleware through `httpx.ASGITransport` reported BUFFERED — until a control run with *zero middleware* reported BUFFERED identically: `ASGITransport` collects the whole body, so a middleware test conducted through it can only ever say "buffered". Over a real socket the full stack streams at the server's own cadence (first frame t=0.03s). End to end against the live endpoint, warm: retrieval+rerank done at ~2s, first model token at 5.2–7.5s, *all* tokens emitted in the following 0.3–0.65s — so **89–96% of the wait passes before the visitor sees one character**. Nothing buffers (25–33 separate network deliveries; the adapter yields per delta); the gap is `gpt-5.5` thinking, and a reasoning model emits nothing until it is done. The real finding was a UX one no content-based check could see: the widget showed an empty bubble for five to eight seconds. It now shows a waiting indicator, cleared on the first token and on both no-token exit paths.

**Status:** `ruff check src tests scripts`, `mypy src` (strict, 215 files), `lint-imports` (all 3 contracts), and the full suite — **522 tests, up from 506** — all pass clean in ~19 minutes; `npm run build` (25 routes), `npx tsc --noEmit` and `npx eslint src` pass.

`widget.js` is named explicitly in `[tool.setuptools.package-data]` rather than left to `include-package-data` defaults — an editable install serves it either way, which is exactly how Phase 11's `.dockerignore` bug stayed invisible until a real build. **Confirmed by building a wheel and listing it**, not by reading the config.

**One deliberate deviation from docs/24's plan, stated rather than glossed:** allowed domains are console-managed (they are a security control and must be), but **branding is not** — `data-title`/`data-accent`/`data-greeting` are read from the script tag and there is no database column for them. The person embedding is already editing that line, and per-page control is something one stored value cannot express. The cost: a tenant admin who doesn't own the website can't restyle it. The fix, if wanted, is a `branding jsonb` column returned by the session endpoint with the attributes as overrides.


## Confirmed architectural decisions (do not re-litigate without new user input)

- **Tenancy model:** shared PostgreSQL schema, discriminator-column (`tenant_id`) multi-tenancy, with Row-Level Security as defense-in-depth. No database-per-tenant or schema-per-tenant. Rationale + comparison: [docs/07-tenant-isolation-and-rls.md](docs/07-tenant-isolation-and-rls.md).
- **Tenant resolution:** verified subdomain/custom domain is the primary strategy; server-side authenticated tenant-selection is the fallback for clients without a resolvable domain (mobile/API). A tenant ID in a header/path/body is always re-validated against real membership rows — never trusted on its own. Detail: [docs/07-tenant-isolation-and-rls.md](docs/07-tenant-isolation-and-rls.md).
- **Tokens:** short-lived JWT access tokens (10–15 min, minimal claims, no roles/permissions/tenant embedded) + opaque rotating refresh tokens (DB-backed, hashed at rest, family-based reuse detection). Detail: [docs/05-authentication-flows.md](docs/05-authentication-flows.md).
- **Platform vs tenant authorization:** fully disjoint role/permission tables; a tenant role can never hold a platform permission (enforced at schema + app layer, not convention). Detail: [docs/06-authorization-model.md](docs/06-authorization-model.md).
- **Redis:** cache and rate-limit store only, never authoritative. Fails closed (deny) if it can't confirm freshness, never fails open. Detail: [docs/06-authorization-model.md](docs/06-authorization-model.md).
- **Impersonation:** platform-initiated only, time-boxed, fully audited, distinct token shape (`act` claim preserves original platform identity). Detail: [docs/06-authorization-model.md](docs/06-authorization-model.md).
- **ABAC / external policy engine:** deferred — data model is designed to admit it later without a rewrite, not built now.
- **Dependency injection:** FastAPI's native `Depends()`, no third-party DI container. The composition root that wires concrete `infrastructure` classes into the API's dependency container is `src/iam_platform/bootstrap.py` — deliberately a top-level sibling module, not inside `api/`, since it has to import from every layer to do its job and would otherwise violate the `api`-can't-import-`infrastructure` contract. `api/main.py` and `api/deps/*` only ever see `application`/`core` Protocol types. Detail: [docs/20-dependency-rules.md](docs/20-dependency-rules.md).
- **Layering:** `domain` has zero project-internal imports (pure, framework-free); dependency direction (`api → application → domain`, `infrastructure → application/domain`, everything → `core`) is enforced in CI via `import-linter`, not just code review. Detail: [docs/20-dependency-rules.md](docs/20-dependency-rules.md).
- **Config/secrets:** typed `pydantic-settings`, `extra="forbid"`; secrets resolved through a `SecretProvider` port (env for dev, AWS/Vault/Azure/GCP for staging/prod) via `secret://` references rather than plain env vars in production. Detail: [docs/21-configuration-and-secrets.md](docs/21-configuration-and-secrets.md).

All decisions above were explicitly confirmed by the user; see [docs/08-decisions-log.md](docs/08-decisions-log.md) for the full rationale and alternatives considered.

## Phase 5 — what's built

The full identity/authentication module: registration + email verification, password login, TOTP MFA (enrollment + challenge verification), rotating refresh tokens with reuse detection, logout / logout-all-devices, password reset, and OAuth login/JIT-registration/linking/unlinking for Google + Facebook. Real SQLAlchemy 2.0 models for all 12 identity tables plus the 4 audit-adjacent tables (`audit_logs`, `security_events`, `login_attempts`, `account_lockouts`), a working Alembic baseline migration, and a FastAPI app that actually boots and serves `/v1/auth/*`.

**Deliberately deferred** (noted at the start of the Phase 5 session, not silently skipped): WebAuthn verification (the `mfa_methods` table/domain entity support it; the register/verify handlers would need a dedicated attestation library), the `api_keys` management API, and the `trusted_devices` "remember this device" flow. Models exist for `user_profiles`, `trusted_devices`, and `api_keys` (schema-complete migration) but no repository/use case yet.

**Verified, not just written:** every module in this phase was checked against a live Postgres + Redis (`docker-compose.dev.yml`), not only in-memory fakes — see the next paragraph for why that distinction mattered. Current status: `ruff check`, `mypy src` (strict), `lint-imports` (all 3 architecture contracts), and the full test suite (50 tests: unit + integration + HTTP-level API tests) all pass clean. Re-run with:

```bash
docker compose -f docker-compose.dev.yml up -d
python -m alembic upgrade head
python -m ruff check src tests && python -m mypy src && lint-imports && python -m pytest
```

**Read [docs/18-schema-rls-and-migrations.md](docs/18-schema-rls-and-migrations.md)'s "A rollback pitfall every Unit of Work implementation must avoid" section before writing the tenant/platform authorization Unit of Work in Phase 6.** Integration testing against real Postgres caught two serious, silent bugs that every in-memory-fake-only unit test missed: (1) writing a security-critical side effect (lockout, reuse-detection revocation, MFA-failure record) and then `raise`-ing from inside the same `async with uow:` block rolls that write back, because `__aexit__` rolls back on any exception — the fix is "exit the block normally, raise after." (2) `server_default="now()"` (a bare Python string) silently becomes a frozen literal PostgreSQL default instead of a live function call; always use `func.now()`. Both are now fixed and the unit-test fakes (`tests/unit/identity/fakes.py`) were upgraded to simulate real transaction rollback specifically so this class of bug can't slip through undetected again.

## Phase 6 — what's built

The full platform and tenant authorization module: tenant lifecycle (`CreateTenant`/`SuspendTenant`, platform-permission-gated, bootstraps the owner's first membership + system `tenant_owner` role atomically), member invitation/acceptance, membership lifecycle (`SuspendMembership`/`ReactivateMembership`/`RevokeMembership`), hierarchical RBAC (custom role creation, role-hierarchy edges with cycle prevention, explicit allow/deny overrides), effective-permission resolution for both platform and tenant scopes (with feature-entitlement filtering on the tenant side), and platform-initiated support impersonation (`act`-claim token issuance). Real SQLAlchemy 2.0 models for all 12 new tables across `tenancy`, `platform_authz`, and `tenant_authz`, a working Alembic migration with hand-written RLS policy SQL for every one of them, and 5 new FastAPI routers (`platform`, `tenants`, `memberships`, `rbac`, `impersonation`) wired into the app.

**Self-escalation guard, everywhere power transfers hands:** `domain/shared/policies.py`'s `can_assign_role()` is the single check reused at every point an actor could grant themselves or someone else more access than they hold — direct role assignment (platform and tenant), custom-role *definition* (not just assignment), role-hierarchy edge creation (inheriting a child role's full expanded permission set), invitation role pre-assignment, and ALLOW authorization overrides (DENY overrides skip it, since they can only remove access). Every one of these paths has a dedicated unit test proving both the "actor cannot grant permissions they don't hold" and "actor cannot elevate their own rank via self-assignment" cases.

**Deliberately deferred** (noted at the start of the Phase 6 session, not silently skipped): `tenant_domains`/`tenant_settings`/`tenant_subscriptions`/`tenant_usage_limits` (subdomain-based tenant resolution and billing/plan semantics), Redis caching of effective-permission resolution (always computed fresh — correctness first, caching is a pure performance layer behind the same call shape), and full `act`-claim-aware audit-actor resolution during an active impersonation session (lands with Phase 7's AI-resource endpoints, the first place impersonation is used operationally). No seed data ships in the baseline migration — role/permission catalog rows are an ops/fixture concern, seeded by tests via the migrator connection, not schema.

**Verified, not just written:** the formal RLS proof suite (`tests/integration/db/test_rls_isolation.py`, 9 tests) exercises tenant isolation, fail-closed no-context behavior, cross-tenant `WITH CHECK` rejection, the pooled-connection `NULLIF` fix, and platform-bypass scoping directly against live Postgres roles — not through the application layer, so it proves the *database* enforces isolation independent of whether application code remembers to filter. A separate end-to-end test (`tests/integration/test_platform_and_tenant_authz_flow.py`) drives the real application layer through `CreateTenant → InviteMember → AcceptInvitation → AssignMembershipRole → ResolveTenantEffectivePermissions`. Current status: `ruff check`, `mypy src` (strict), `lint-imports` (all 3 architecture contracts), and the full test suite (130 tests: unit + integration + HTTP-level API tests) all pass clean. Re-run with:

```bash
docker compose -f docker-compose.dev.yml up -d
python -m alembic upgrade head
python -m ruff check src tests && python -m mypy src && lint-imports && python -m pytest
```

**Three non-obvious findings from this phase, all now folded into [docs/18-schema-rls-and-migrations.md](docs/18-schema-rls-and-migrations.md) as ground truth:**
1. **Pooled-connection RLS context leak.** A reused pooled connection that previously had `app.tenant_id` set returns `''` (empty string), not `NULL`, on `current_setting(name, true)` in a later transaction that never re-sets it — a raw `::uuid` cast on that raises `invalid input syntax for type uuid: ""` instead of cleanly evaluating to "no context, deny." Fixed globally with the `NULLIF(current_setting(name, true), '')::uuid` pattern; verified empirically with a `pool_size=1` engine forcing connection reuse (now a permanent regression test, `TestPoolReuse`).
2. **`tenants` table RLS gap.** `tenants` isn't in any "standard tenant-owned table" list from the docs/11–18 schema docs (it *is* the tenant, not owned by one), so an autogenerate pass had no reason to flag it — but leaving it ungated would let any authenticated tenant read or modify every other tenant's row. Fixed with a dedicated read-only, own-row-only policy.
3. **`CREATE POLICY ... FOR INSERT, UPDATE, DELETE` is a Postgres syntax error.** PostgreSQL only allows one command per policy; caught by actually running the migration against Postgres, not by reading the SQL. Fixed by splitting into separate `CREATE POLICY` statements per command for every nullable-tenant table.

## Phase 7 — what's built

The AI-resource authorization layer: assistants with four-mode visibility (tenant / department / team / restricted) plus explicit per-member grants, knowledge bases and documents with server-derived vector namespaces and storage paths, conversations with owner-only content and audited auditor access, model configurations (platform-default plus tenant-owned), and tenant provider credentials behind an envelope-encryption boundary. Real SQLAlchemy models for all 7 new tables, an Alembic migration with hand-written RLS for each, and 14 new `/v1/tenants/{tenant_id}/...` endpoints serving assistants, knowledge bases, documents, conversations, and credentials.

**Two structural guarantees, not conventions.** Both are the point of this phase, and both are enforced by *shape* rather than by remembering a rule:
1. **Server-derived namespaces and paths.** `CreateKnowledgeBaseCommand` has no `vector_namespace` field and `UploadDocumentCommand` has no `storage_path` field — they are derived from already-authorized IDs via `VectorNamespaceFactory`/`ObjectStoragePathFactory` ports. A vector query passes the namespace read off the stored knowledge-base row the caller was just authorized for, so an unauthorized `knowledge_base_id` fails the visibility check before any search runs. That's Phase 1 §12's "vector queries must always use server-generated tenant filters", made unbypassable.
2. **The provider-secret boundary.** Plaintext enters through one command, is immediately Fernet-encrypted, and is never stored, logged, or echoed. Every read path returns `ProviderCredentialSummary`, which has *no field capable of carrying ciphertext* — a DTO that merely declines to populate a secret field is one careless edit from leaking; one with no such field cannot.

**Read access never implies write access.** `domain/ai_resources/policies.py` splits `can_access_resource` from `can_modify_resource`, and `application/ai_resources/authorize.py` is the single load-then-authorize path every use case goes through. Failing the *visibility* check raises `*NotFoundError`, never a 403 — a resource a caller cannot see must not be provable to exist (docs/03-threat-model.md). Failing only the *modify* check raises `ResourceAccessDeniedError`, which is safe because they already proved they can see it.

**Deliberately deferred** (stated at the start of the Phase 7 session, not silently skipped): `integrations` and `data_sources` (external-system sync — an ops integration with no bearing on the authorization model), real vector-store and object-storage clients (Protocol ports with in-memory/logging stands-in; the namespace/path *generation* is the security-relevant half), real KMS wrapping of the encryption data key (the `CredentialEncryptor` port boundary exists so adding it changes one file and no caller), and message-level conversation content — docs/16 explicitly left that shape to be decided here, and the decision is that it belongs with AI serving, not authorization.

**Verified, not just written:** a second RLS proof suite (`tests/integration/db/test_ai_resources_rls.py`, 14 tests) exercises the new tables directly against live Postgres — fail-closed reads with no tenant context across all six tenant-owned tables, cross-tenant assistant/namespace/credential isolation, `WITH CHECK` rejection on cross-tenant insert, the nullable-tenant read-but-not-write split on `model_configurations`, and platform-owned credentials staying invisible to tenants. Current status: `ruff check src tests`, `mypy src` (strict), `lint-imports` (all 3 contracts), and the full suite (198 tests, up from 130) all pass clean. Re-run with the same commands as Phase 6 above.

**Two schema corrections found during implementation**, both folded into the models and migration as ground truth:
1. **`knowledge_bases` had no department/team columns.** docs/16 gives the table a `visibility` column with the same four modes as `ai_assistants` but no columns to scope department/team visibility against — making `visibility='department'` unsatisfiable. Added to match `ai_assistants`, with a CHECK constraint on both tables so a department/team-scoped row without its scoping column can't be stored at all (the domain entity enforces it too; the constraint means a migration or fixture can't bypass the entity).
2. **`documents.storage_path` uniqueness had to be partial.** A plain unique index would let a soft-deleted document permanently reserve its path; the index is `WHERE deleted_at IS NULL`.

## Phase 8 — what's built

The security-validation suite: `tests/security/` carries one automated negative test per numbered row of [docs/03-threat-model.md](docs/03-threat-model.md)'s "Cross-Tenant & Privilege-Escalation Scenarios" table (designated **mandatory** test cases), plus a guard on the STRIDE repudiation mitigation. `tests/api/test_ai_resource_authorization.py` adds HTTP-level proof that the authn → tenant-resolution → permission-resolution dependency chain actually rejects at the route boundary — a use case that correctly denies an under-permissioned caller proves nothing if the route forgot to resolve permissions.

**The phase found two defenses that the design specified but the code never implemented.** Both were verified against the running system before any fix was written, and both are now fixed and guarded:

1. **Scenario 9 — impersonation carried the target's full privileges.** The token's `sub` is the target user, which correctly kept *platform* permissions out, but nothing then constrained the target's *own* permissions. A support agent impersonating a tenant owner inherited `tenant.roles.manage` and could have granted themselves a role, or exported data, under the target's identity. Fixed by `domain/impersonation/policies.py`, which narrows an impersonated session through two independent filters — an explicit blocklist of escalation/exfiltration permissions, and the catalog's `risk_level` (`high`/`critical` denied). Neither alone is sufficient: the blocklist covers a mis-tagged permission, the risk level covers one nobody thought to blocklist. Applied in `ResolveTenantEffectivePermissions` and, for the platform path, by returning an empty set outright when the `act` claim is present.
2. **Repudiation — `audit_logs` was not append-only.** The STRIDE table specifies "DB-level revoke UPDATE/DELETE grants; app role has INSERT-only", but `ALTER DEFAULT PRIVILEGES` in `docker/postgres-init/01-roles.sql` grants full CRUD on every newly created table and no migration ever narrowed it. The live database showed `app_tenant` holding DELETE and UPDATE on `audit_logs` — a compromised app connection could erase the record of its own actions. Fixed by migration `937a69c41b65` for both `audit_logs` and `security_events`, across both app roles.

**Honestly scoped, not silently claimed:** scenario 8 (worker tenant-context bleed) is **partially** covered — no `workers/` runtime exists yet, so there is no job-execution path to attack. The test asserts the mechanism the defense rests on (transaction-scoped `set_config`, proven behaviourally by scenario 12's live-Postgres pool-reuse test); the per-job re-validation half must be tested when workers are built. Scenario 10 covers `required_feature` but not `required_plan`, which needs the still-deferred `tenant_subscriptions` table. Audit tagging of impersonated actions with `impersonation_session_id` also remains outstanding. All three are recorded in the threat-model table itself rather than left implicit.

**Verified:** `ruff check src tests`, `mypy src` (strict), `lint-imports` (all 3 contracts), and the full suite — **236 tests**, up from 198 — all pass clean. Two tests written during this phase were rewritten after review because they could not fail (one asserted a tautology, one only grepped for test names); both are now real behavioural tests that exercise the actual use cases.

## Phase 9 — what's built

The deployment and operations layer: a hardened multi-stage `Dockerfile` (non-root, no build toolchain or dev dependencies in the runtime image, read-only rootfs in compose), `.dockerignore` that keeps `.env`/`*.pem` out of build layers, a production-shaped `docker-compose.prod.yml` with migrations as a **separate job** running under the migrator role, real dependency-probing health endpoints, `secret://` resolution wired at the composition root plus an AWS Secrets Manager provider, per-IP rate-limit middleware, a `/metrics` endpoint, graceful shutdown, and [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md) covering topology, expand/contract migrations, key rotation, scaling arithmetic, failure modes, and a pre-deploy checklist.

**Like Phase 8, this phase's value was mostly in what it found.** Three defects, all "designed but inert" — the docs described each as if it already worked:

1. **`secret://` references were never resolved.** `SecretProvider` and `EnvSecretProvider` shipped in Phase 5 and docs/21 specifies the resolution step in detail, but nothing ever called it — a production deploy setting `DATABASE__PASSWORD=secret://prod/db/password` would have used that literal string as the password. Combined with `Settings` refusing to boot when `environment=production` and `secret_provider=env`, **the service could not have started correctly in production at all.** Fixed by `infrastructure/secrets/resolver.py`, wired into `build_container` (now async, since resolution does I/O).
2. **`/readyz` was a stub** returning hard-coded `{"status": "ready"}`. Under Kubernetes that means a pod whose database connection is dead still passes its readiness gate and keeps receiving traffic — a readiness probe that cannot fail actively suppresses the orchestrator's ability to route around the problem. Now probes both DB roles (they use different credentials and fail independently) and Redis, concurrently and with a timeout, returning 503 when any is down. `/livez` deliberately stays dependency-free: a liveness probe that tracks dependencies restarts *every pod* during a database blip.
3. **Nothing was ever disposed.** Both engine pools, the Redis pool, and the OAuth HTTP client leaked on every shutdown — in a rolling deploy, terminating pods held Postgres connections until the server timed them out, so new pods contended for a connection budget the old ones hadn't released. Fixed with a FastAPI lifespan.

**Two further bugs were caught by the Phase 9 tests themselves**, which is the point of writing them: the readiness probe timeout was set to 2s, but a *cold* connection pool takes ~2.5s for its first connect (measured — subsequent connects are ~0.1s), so every freshly started pod would have failed its first probe and reported a misleading "timeout"; and the metrics middleware labelled every request `route="unmatched"` because `include_router` keeps nested router objects in `app.routes` rather than flattening them, so walking that list misses the entire API. The route template is now read from `scope["route"]`, which Starlette populates during routing.

**Verified, not just written:** the image was built and exercised — `docker run ... id` confirms uid 1001 (non-root), `gcc` and `pytest` are absent from the runtime layer, and the production guard genuinely refuses to start:

```bash
docker run --rm -e ENVIRONMENT=production -e SECRET_PROVIDER=env ... iam-platform
# ValidationError: refusing to start: environment=production requires a real secret provider
```

`ruff check src tests`, `mypy src` (strict), `lint-imports` (all 3 contracts), and the full suite — **261 tests**, up from 236 — all pass clean.

**Deliberately not built** (consolidated in [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md#known-gaps)): the `workers/` runtime, JWT signing-key rotation (needs JWKS-style multi-key verification), `ENCRYPTION__DATA_KEY` rotation (needs a key-versioned envelope scheme and re-encryption), distributed tracing, and Vault/Azure/GCP secret adapters — selecting one of those raises `NotImplementedError` at startup rather than silently falling back to `env`.

## Admin console (frontend) — what's built

`frontend/` holds the **IAM Control Center**: Next.js 16 (App Router) + TypeScript + Tailwind v4 + shadcn/ui, covering auth (login/register/MFA/password reset/OAuth), platform administration (tenants, platform roles, impersonation), tenant administration (members, invitations, custom roles, hierarchy, overrides), and the AI-resource surfaces (assistants, knowledge bases with a live retrieval tester, conversations, provider credentials). See [frontend/README.md](frontend/README.md) for setup, architecture, and the backend-gap list the UI works around.

**The browser never holds a token.** Every authenticated call goes through a same-origin BFF proxy (`frontend/src/app/api/backend/[...path]/route.ts`) that attaches the bearer token server-side, transparently refreshes once on a 401 and retries, and strips raw tokens out of login/refresh/impersonation response bodies into `httpOnly` cookies. There are deliberately no `NEXT_PUBLIC_*` variables — the backend origin is server-only.

**Building it against the real API surfaced gaps the backend docs didn't.** Rather than guess, the API was catalogued endpoint-by-endpoint from the routers first, which exposed several missing read endpoints that made flagship screens impossible. Added (application-layer queries + routes + tests, no new architectural patterns):

- `GET /v1/tenants/{id}/memberships` and `.../memberships/{id}/roles` — without these there was **no way to list a tenant's members at all**, so the Members screen couldn't exist.
- `GET /v1/tenants/{id}/roles` and `.../permissions`, `GET /v1/platform/roles` and `/permissions` — role/permission pickers had no data source.
- `GET /v1/platform/tenants` — no way to list tenants existed even at the repository layer.
- `POST /v1/platform/roles/revoke` — the `RevokePlatformRole` use case existed but was never wired to a route.

**Three real bugs were found and fixed while verifying against a live backend:**

1. **`RevokePlatformRole` had no authorization check at all** (security). Any bearer token could strip any platform user's role — a privilege-sabotage path with no gate. It now runs the same `can_assign_role` self-escalation guard as the grant path, with a test proving an under-privileged actor is refused.
2. **Login crashed with a 500 on any unknown email address.** `login_user.py`'s account-enumeration timing mitigation compares against a fixed `_DUMMY_HASH`, but that constant was hand-typed and not a valid Argon2 encoding, so argon2-cffi raised `VerificationError: Decoding failed` at *decode* time — unmapped by any handler. Worse, the malformed hash failed before the expensive comparison, so it **wasn't providing the constant-time property it existed for**. Fixed with a genuine hash plus broader exception handling in `Argon2IdPasswordHasher.verify` (a malformed hash must mean "not authenticated", never a crash). Covered by `tests/api/test_auth_flows.py::test_login_with_nonexistent_email_returns_401_not_500` — unreachable by unit tests using fakes.
3. **Nested `<button>` hydration error** in the tenant switcher (the copy-to-clipboard `IdentityChip` inside a dropdown trigger button).

**One backend defect found and left unfixed, deliberately** — it needs a migration and was outside the frontend scope: `ai_assistants` carries a plain composite FK `(tenant_id, model_configuration_id) → model_configurations(tenant_id, id)` with a `NOT NULL` `tenant_id`, so **platform-default model configurations (`tenant_id IS NULL`) are unreachable**, despite docs/16 describing them as "readable by all tenants". Phase 6 hit the same nullable-tenant problem with `tenant_roles` and solved it with a single-column FK; `ai_assistants` never got the same treatment. `scripts/seed_demo_data.py` documents the workaround inline.

**One pre-existing flaky test fixed.** `test_security_headers_present_on_every_response` asserted a 200 from `/healthz` purely as "any endpoint" — fine when that route was a Phase 5 stub, but Phase 9 made it dependency-aware, so under connection contention a cold pool's first probe exceeds the health timeout and the test fails with 503 for reasons unrelated to headers. It now probes `/livez`, the one endpoint deliberately guaranteed dependency-free. The degraded-response behaviour it used to incidentally cover is already tested deterministically in `tests/api/test_ops_endpoints.py`. **General rule, now in docs/22: never assert on `/healthz` or `/readyz` in a test that isn't about health.**

**Verified, not just written:** the auth flow, tenant selection, dashboard, and members roster were exercised through a real browser against the running FastAPI backend and Postgres. `npm run build` (21 routes), `npx tsc --noEmit`, and `npx eslint src` all pass clean. `scripts/seed_demo_data.py` seeds a realistic catalog, tenant, members, and AI resources for local work.

> **Running the backend test suite while a dev server is up causes spurious failures** — both compete for the same Postgres/Redis, and the suite's own health probes and rate limiter start timing out. Stop `python -m iam_platform.asgi` before running `pytest`.

## Admin console — identity, user, role, permission and tenant management

A follow-up session completed the management surfaces the first console pass left thin, and fixed four defects found by driving the running system rather than by reading it. New screens: `/account` (profile, password change, TOTP enrollment, linked providers, sign-out-everywhere), `/platform` (operator overview), `/platform/users` (searchable, paginated directory with a per-user detail sheet showing platform roles, resolved permissions, and tenant memberships, plus account suspend/reactivate), and `/platform/permissions` (risk-summarised catalog + a role→permission matrix). Nav is now grouped Platform / Account, and `/platform/tenants` gained a Reactivate action.

**Four bugs, three of which made the console silently or loudly unusable.** All were found by clicking through the app, not by inspection:

1. **Every dropdown menu item in the app was inert** (the reported "signout is not working"). This is Base UI, not Radix: `Menu.Item` fires **`onClick`**, and has no `onSelect` prop at all. But `onSelect` *is* a real DOM handler on a `<div>` (the text-selection event), so `MenuPrimitive.Item.Props` accepted it, TypeScript stayed silent, and the handler was spread onto the element and never called. Six call sites were affected — sign out, tenant switching, "Manage tenants", and the entire member suspend/reactivate/revoke menu. The trap is now documented on `DropdownMenuItem` itself in `components/ui/dropdown-menu.tsx`.
2. **The BFF proxy 500'd on every `204 No Content` reply** (the reported "500 when suspending a tenant"). `204/205/304` are null-body statuses — the `Response` constructor throws if given any body, including the empty string `upstream.text()` yields. The proxy built its response before checking the status, so *every successful mutating action in the console* — suspend a tenant, assign a role, revoke a membership, delete a credential — returned a 500 to the browser after the backend had already carried the action out. Fixed with a `NULL_BODY_STATUSES` guard.
3. **Signing out never revoked the session server-side.** `POST /v1/auth/logout` revokes a specific refresh-token family and needs the raw token, but the browser has never had it by design, so the client sent `{"refresh_token": ""}` as a placeholder and the proxy forwarded it unchanged. The backend answered 204, the cookies were cleared, and it looked like it worked — while the refresh token stayed valid in the database until natural expiry. Proven directly: with an empty token, `POST /refresh` still returned 200 afterwards; with the real one it returns 400. The proxy now substitutes the cookie value (`substituteRefreshToken`), and logout joined `NEVER_REFRESH_PATHS` so a retry can't present the just-revoked token to reuse detection.
4. **Permission-denied tenant actions returned 500 instead of 403.** `TenantCreationDeniedError` and `DuplicateSlugError` were declared as bare `Exception` subclasses beside their use case rather than under `PlatformAuthzError`, so no handler in `api/exception_handlers.py` matched them. They now live in `application/platform_authz/exceptions.py` — 403 and 409 respectively — with `TenantNotFoundError` added for 404.

**Also fixed:** `scripts/seed_demo_data.py` crashed with a bare `NoResultFound` when re-run with a *different* owner email, because membership creation sat inside the `if tenant is None` branch while a later `.scalar_one()` assumed it had happened; membership is now ensured unconditionally, member seeding is idempotent, and a suspended demo tenant is reactivated on re-seed. A hydration error introduced during this session (a `Skeleton` `<div>` inside a `<p>`) was caught by the browser console and fixed before it shipped.

**New backend surface** (all permission-gated, no new architectural patterns): `GET /v1/auth/me`, `POST /v1/auth/password/change`, `GET /v1/platform/users` (searchable + paginated, capped at 100/page), `GET /v1/platform/users/{id}`, `POST /v1/platform/users/{id}/suspend|reactivate`, `POST /v1/platform/tenants/{id}/reactivate` (`Tenant.activate()` had existed since Phase 6 with no use case or route — a suspended tenant could not be brought back through the API at all), and `GET /v1/platform/roles/permissions` + `GET /v1/tenants/{id}/roles/permissions`. Two new platform permissions, `platform.users.read` and `platform.users.manage`, are seeded by both `scripts/bootstrap_platform_admin.py` and `scripts/seed_demo_data.py` — **re-run the bootstrap script after upgrading** to pick them up.

Suspending an account bumps the security stamp and revokes every session, so the target is signed out immediately rather than at token expiry; self-suspension is refused outright, since recovering from it needs direct database access.

**Verified, not just written:** every fix above was reproduced in the browser before and confirmed after — including checking `refresh_tokens.revoked_reason = 'logout'` in Postgres to prove sign-out actually revokes. `npm run build` (25 routes), `npx tsc --noEmit`, `npx eslint src`, `ruff check src tests scripts`, `mypy src` (strict) and `lint-imports` (all 3 contracts) pass clean. `scripts/` is now inside the ruff path, which it never was before.

## Admin console — full user lifecycle, tenant provisioning UX, and a login-gate fix

A third console session closed the remaining "how do I actually *do* this?" gaps and, in the course of driving them, found four more defects — one of them serious.

**The bigger security bug: `get_current_claims` never checked whether the session was still live.** It verified the JWT signature and expiry and returned. docs/05 states plainly that bumping `user.security_stamp` must make "any access token issued before this moment fail a freshness check ... even before it naturally expires" — and that check did not exist anywhere on the request path. So `logout-all`, password change, account suspension *and* account deletion all left the existing access token working for its full 10–15 minute life. The stamp was bumped diligently by five different use cases and read by nothing. Now `api/deps/authn.py` loads the user and session per request and rejects a revoked session, a stale stamp, or an inactive/deleted account, reporting all three as the same opaque 401 so a stolen token can't be used to probe what happened to the account. Costs two indexed lookups per request; noted in docs/22's scaling section so nobody optimises it away without replacing it.

**The related bug: suspension and deletion did not prevent signing in either.** `LoginUser` checked account lockouts and whether the *identity* (auth method) was active, but never looked at `users.status` or `deleted_at`. Suspending an account bumped the security stamp, which killed tokens already issued — and then the person could simply log in again and be handed fresh ones. Deleting had the same hole. `platform.users.manage` was, in effect, cosmetic. Fixed in `login_user.py`, with the check placed *after* password verification and reported identically to a wrong password, so it can't be used to enumerate suspended accounts. `PENDING_VERIFICATION` is deliberately still allowed to sign in: no email provider exists, so gating on verification would lock out every self-registered account with no way back.

**Three more, all found by using the console rather than reading it:**

1. **Creating a second tenant for the same owner always 500'd.** `ux_tenant_memberships_one_default_per_user` is a partial unique index over `user_id WHERE is_default`, and `CreateTenant` hard-coded `is_default=True`. Only the owner's first membership may claim the default slot now.
2. **Every administrative session-revocation path 500'd** on `refresh_tokens`' `revoked_reason` CHECK constraint, which allowed only the six Phase 5 values. `password_change` (from the earlier account work), `account_suspended`, `account_deleted` and `email_changed` were all rejected. Widened by migration `a4d2f81c9b30` rather than collapsing them into the generic `admin`, because `revoked_reason` exists to answer "why did this session end?" during incident review.
3. **The signed-in landing page was a dead end for platform operators.** `/` redirected unconditionally to `/select-tenant`, and the sidebar swapped scopes on URL prefix — so a platform admin with no tenants got an empty rail and "ask a platform administrator to create a tenant for you". `/` now routes by resolved permissions (platform → `/platform`, single tenant → that dashboard, otherwise the picker) and the sidebar renders every scope the caller actually holds.

**New management surface.** `POST /v1/platform/users` (admin-provisioned account, active immediately — the administrator *is* the vouching step email verification would provide), `PATCH` (rename, with uniqueness check, verification reset and session revocation) and `DELETE` (soft delete; the row is kept because `audit_logs` references it). `User` gained real domain transitions — `reactivate`, `soft_delete`, `change_email` — and `SetUserStatus` now goes through `suspend()`/`reactivate()` instead of assigning `status` directly, which is what makes "can't resurrect a deleted account" enforceable rather than conventional.

**UX the user specifically called out, rebuilt:** the tenant form derives its slug from the organization name (overridable, with a "reset to auto" affordance), flags a taken slug before submit while still treating the backend's 409 as the authority, and replaces the raw owner-UUID field with a searchable `UserPicker` that degrades to a plain id input when the caller lacks `platform.users.read`. `/platform/users` gained create/rename/suspend/delete with confirmation, and inline platform-role grant/revoke in the detail sheet.

**A structural guard was added for a mistake made three times running:** `tests/unit/test_exception_mapping_is_exhaustive.py` fails if any application exception subclass is missing from `api/exception_handlers.py`'s status map, or is declared outside an `exceptions.py`. It immediately earned its place — written after `TenantCreationDeniedError` and `UserManagementDeniedError`, it then caught a third, pre-existing instance (`TenantListDeniedError`) that had been answering 400 to what is plainly a 403.

**Verified:** `ruff check src tests scripts`, `mypy src` (strict), `lint-imports` (all 3 contracts), and the full suite — **289 tests**, up from 265 — all pass clean. `npm run build` (25 routes), `npx tsc --noEmit` and `npx eslint src` pass. The session-freshness fix was confirmed live rather than only in tests: the same bearer token returns 200, then 401 the instant `logout-all` runs. **A new migration ships with this work (`a4d2f81c9b30`) — run `python -m alembic upgrade head`.**

**A test-suite defect that was corrupting results:** `tests/api/conftest.py` builds a full container per test — two Postgres pools, a Redis pool, an HTTP client — and never called `container.shutdown()`. The app's lifespan normally releases them, but the fixture constructs the container directly and never enters it. By the back half of a ~290-test run the leaked pools exhausted Redis and Postgres, and unrelated tests failed with `redis.exceptions.TimeoutError` and 503s from `/readyz`. Several of those were initially chased as logic bugs. **If the suite fails in places unrelated to what they test, check for leaked resources before debugging the test.**

## Admin console — audit pass: fixing every "can't we..." gap the operator actually hit

A fourth console session started from a single, explicit user audit of nine screens ("Can't we edit a tenant?", "Can't we create a role?", "how do I restore a revoked membership?", "what is Model configuration ID?", and more) and closed every real gap it found, rather than only explaining them.

**Backend additions, all following the existing self-escalation-guard/exception-mapping/audit-record conventions — no new architectural patterns:** `TenantMembership.restore()` + `RestoreMembership` use case (the `(tenant_id, user_id)` unique constraint had made a revoked membership permanently unrecoverable — there was no reverse transition, not just a missing button); `Tenant.rename()` (display name only — the slug is immutable by design, it's baked into links and API references); `AddMemberDirectly` (bypasses the email-invitation flow entirely, since this deployment sends no email) and `UpdateMembership` (job title); `AddPermissionToRole`/`RemovePermissionFromRole` for **existing** tenant roles (role creation already let you pick permissions once; editing after the fact did not); a full platform-role mirror of the tenant RBAC pattern — `CreateCustomPlatformRole` + `AddPermissionToPlatformRole`/`RemovePermissionFromPlatformRole`, using the same `can_assign_role` self-escalation guard, `_MANAGE_ROLES_PERMISSION = "platform.tenants.create"` since no dedicated permission exists yet; and, for assistants, `UpdateAssistant` (name/description/system_prompt/model_configuration_id), `ArchiveAssistant` (soft delete, using the domain's pre-existing but previously-unwired `archive()` transition), and `ListModelConfigurations` (the repository method `list_available_to_tenant` already existed from Phase 7 — nothing had ever called it from a route).

**Deliberately not built, and said so out loud rather than faked:** raw permission *creation* through the UI (would create catalog rows no code path checks — actively misleading, not merely incomplete) and platform-level per-user permission overrides (the overrides table is tenant-scoped only; no schema support exists at the platform level).

**One real gap the audit surfaced in the API response shape, not just the UI:** `AssistantResponse` never included `system_prompt` — so an edit form for it would have shown blank and silently overwritten the existing prompt with empty string on every save. Added to the schema and the router's mapper before wiring the edit form, not after.

**Every one of the nine audited screens now has the CRUD the operator was missing:** Platform → Tenants gained rename; Platform → Users' "platform roles vs tenant roles" question is answered inline in the guide (fully disjoint tables, never conflated) rather than needing code changes; Platform → Roles/Permissions gained role creation, per-role permission add/remove, and a searchable-by-email `UserPicker` in place of a raw user-ID text field for grant/revoke; Platform → Impersonation gained a `TenantPicker` (new component — `useTenants()` has no server-side search, so it filters the already-cached list client-side, mirroring `UserPicker`'s UX) plus reuses `UserPicker` for the target user; Tenant → Members gained Add-member-directly, Restore, and edit-job-title, alongside the existing invite/suspend/reactivate/revoke; Tenant → Roles & Permissions gained per-role permission editing to match Platform → Roles; Tenant → Assistants gained edit and archive, a real `ModelConfigurationField` picker in place of a raw-UUID paste field, and department/team visibility options rendered **disabled** with an inline explanation rather than offered and silently broken — nothing in the product yet lets an administrator assign a member to a department or team, so those visibility modes would be unreachable by anyone if selected.

**A genuine Base UI defect, not a design tradeoff:** `<Select.Value>` resolves its displayed label by reading the currently-mounted `<Select.Item>` children — which works fine when the options list is static, but not when it's populated by an async query (`useModelConfigurations`). Pre-selecting a value while editing an existing assistant showed the raw UUID instead of the model name, because the matching `<Select.Item>` hadn't mounted yet when the `value` prop was first set, and nothing forced it to re-resolve afterward. Fixed with `<Select.Value>`'s render-prop form (`children={(v) => byId.get(v)?.model_name ?? v}`), which looks the label up directly instead of depending on item-mount timing. Documented inline on `ModelConfigurationField` in `frontend/src/app/(app)/tenant/[tenantId]/assistants/page.tsx` — any future async-populated `Select` with a pre-set value needs the same treatment.

**Verified:** `ruff check src tests`, `mypy src` (strict), `lint-imports` (all 3 contracts), and the full suite — **309 tests, up from 289** (new coverage for `UpdateAssistant`/`ArchiveAssistant`/`ListModelConfigurations`, including the self-escalation and "cannot modify someone else's assistant" cases) — all pass clean against live Postgres/Redis. `npm run build` (25 routes), `npx tsc --noEmit`, and `npx eslint src` pass clean. The full flow was driven live in the browser against the real backend and Postgres — edit an assistant (confirmed the model-configuration label bug and its fix), archive it (confirmed it drops out of the active list, matching the soft-delete contract), and confirm department/team are genuinely inert in the visibility picker, not just described as inert. `docs/23-admin-console-guide.md` is updated throughout to match — Platform → Tenants, Platform → Roles, Platform → Impersonation, Tenant → Members, Tenant → Roles & Permissions, and Tenant → Assistants sections all reflect what the console can now actually do, including an explicit explanation of what "Model configuration" means and why department/team visibility is disabled.

## Performance pass — PDF fast path and reasoning latency

Two ingestion/answer defects raised by the user in review, both settled by measurement rather than argument. Neither touches the security model.

**1. docling ran on every rich document, including text-native PDFs.** docling infers text from a *rendered image* using ML layout models — right for a scan, wrong for a PDF exported from Word that already carries the author's exact text. Worse here: the hardened image ships no compiler, so `TORCH_COMPILE_DISABLE=1` forces eager execution. Measured on a one-page PDF, warm process: **docling 14,789.7 ms vs pypdfium2 text layer 10.9 ms — 1,351×.**

`infrastructure/parsing/fast_pdf.py` reads the text layer first and **declines** when there is none, so scanned documents still reach docling's OCR. Declining is a *distinct signal from failing*: the dispatcher falls through on `ParserDeclined` and deliberately does **not** on `DocumentParseError`, because a broken file must report its own reason rather than be retried by a parser that will also fail. Citations improve too — page numbers come free from the text layer (`"page 1"`), where docling reports them inconsistently and falls back to headings. Verified in the live worker, not just a bench.

**pypdfium2, not PyMuPDF** — PyMuPDF is AGPL-3.0 or paid commercial, and network copyleft is not a liability worth taking on for a hosted multi-tenant product. pypdfium2 (PDFium, BSD-3-Clause/Apache-2.0) was already an indirect dependency via docling; it is now declared **directly**, since relying on a transitive dependency for something load-bearing is one upstream refactor from breaking.

**2. The answer path had an unbounded reasoning tail.** `OPENAI__CHAT_REASONING_EFFORT` is sent only when set — same opt-in shape as `chat_temperature`, same reason (non-reasoning models reject it outright; `gpt-5.5` accepts `"low"` and **rejects `"minimal"`**).

The first measurement was a single sample pair and overstated this as "4.4× faster". Six questions each, real prompt, time-to-first-token:

| | min | median | max |
|---|---|---|---|
| unset | 1.03s | 2.11s | **10.80s** |
| `"low"` | 0.83s | 1.24s | **1.58s** |

**The median barely moves; the tail collapses.** A reasoning model emits nothing while thinking, so an 11-second outlier is a visitor watching an empty bubble — one such wait is what closes a tab. The value is passed through unvalidated on purpose: a local allowlist would go stale and start refusing values the API accepts.

**Still unfixed and dominant:** retrieval is ~2.5s of a ~5.6s answer — three serial network hops (embedding → Qdrant → Cohere) with no caching.

**Status:** `ruff`, `mypy --strict` (216 files), `lint-imports` (3/3) and the full suite — **537 tests, up from 522** — all pass clean in ~18 minutes.

## Knowledge-base management pass — retry, delete, and the zero-chunk lie

A seven-part user review of the knowledge-base surface (one ingestion flow for both upload paths, modal overflow, URL ingestion verification, a failing PDF, source management, honest status, tenant isolation).

**The defect underneath the rest: zero chunks counted as success.** A scanned 40-page PDF sat at `ready` with **0 chunks** — in the knowledge base, looking ingested, unable to answer anything. The worst of the three outcomes, because a visible failure gets retried and a silent one doesn't. `index_blocks` returning `0` is still not an error *in the indexer* (one navigation-only page in a 500-page crawl genuinely isn't), so it returns the count and **each caller decides**: the upload job raises `DocumentParseError`; the crawl job marks the page failed **by returning, never raising**, because raising inside `session.begin()` would roll back the very status write it is recording — docs/18's pitfall, hit and caught during this pass. Separately, `AiResourceDocumentRepository.save()` **never persisted `failure_reason`** — it wrote `status` and `deleted_at` and dropped the reason — so even a correctly failed document explained nothing.

**New:** `application/ai_resources/manage_document.py` (`RetryDocumentIngestion`, `DeleteDocument`), `Document.mark_processing()`, `delete_chunks`/`count_chunks` on the document repository, `chunk_count` on `DocumentResponse`, and two routes (`POST .../documents/{id}/retry`, `DELETE .../documents/{id}`).

**Two decisions worth not re-litigating:**
1. **Authorizing the knowledge base is not authorizing the document.** Both use cases check `document.knowledge_base_id != knowledge_base_id` → `DocumentNotFoundError`. RLS already hides a cross-*tenant* id; this closes the cross-*knowledge-base* case inside one tenant, which RLS cannot see. Live: wrong KB and other-tenant ids both answer **404**, never 403.
2. **Delete order is vectors → chunk rows → bytes → soft-delete.** Vectors first because that is the copy a query still reaches — an orphaned point keeps answering questions and citing a source the tenant was told is gone. Bytes are best-effort; failing the whole delete over a storage hiccup leaves a document that can't be removed. Both use `tenant.documents.upload`: changing what a KB contains is one authority either direction.

**Mutation-tested, not assumed:** removing the cross-KB guard and removing the vector delete each fail 4 of the 8 tests in `tests/unit/ai_resources/test_manage_document.py`; restoring them passes.

**Verified against the live stack.** Retry on the real failing PDF: `ready`/0 chunks → `processing`, old reason cleared → `failed` with an actionable reason (the fast PDF path correctly *declined* first — `0/40 pages` carry a text layer — and deferred to docling). Delete of a purpose-indexed CSV: 2 chunk rows + 2 Qdrant points before → **0 rows, 0 points, no stored file, collection back to its prior 23** after.

**Two frontend defects a typecheck could never see:**
1. **Rejected files were reported to nobody.** `addFiles` collected rejection messages *inside* the `setStaged` updater and toasted them after the call — but React defers an updater to the render phase, so the array was empty when the toast loop ran. Every "unsupported file type" message was computed and discarded. Validation is now pure and outside the updater (which also makes StrictMode's double-invoke harmless — the reason it had been written that way).
2. **`TableCell` sets `whitespace-nowrap`**, so a failure reason ran straight across the columns to its right. Fixed locally with `whitespace-normal`, not by changing the shared cell.

Modal overflow was fixed at the root: `DialogContent` gained `grid-cols-[minmax(0,1fr)] max-h-[calc(100dvh-2rem)] overflow-y-auto`, so every dialog in the console is bounded.

**Left alone deliberately:** Qdrant's `Api key is used with an insecure connection` warning is accurate and local-only (the dev instance really does accept unauthenticated HTTP) — a warning about the dev topology, not a defect; blank `QDRANT__API_KEY` locally to silence it.

### Document inspector + crawl re-sync

The remaining half of source management. Both are thin over machinery that already existed — no new pipeline, which was the explicit constraint.

**`GetDocumentDetail`** returns the passages actually indexed, via a new paged `DocumentRepository.list_chunks(limit, offset)` (capped at 50). **Read access only** (`for_modification=False`): the same passages are already reachable by asking the knowledge base a question, so demanding modify rights would withhold the diagnosis while protecting nothing. Still makes the cross-knowledge-base check. **It earned its place on first use** — the crawled page's chunk 1 turned out to be 700 tokens of cookie-consent banner indexed as course material, which no status, count or retrieval test would ever have revealed.

**`ResyncDataSource`** re-enqueues *the same job* `CreateDataSource` enqueues. That job was already idempotent: pages are upserted on `(knowledge_base_id, source_url)` (backed by `uq_documents_source_url_per_kb`) and `index_blocks` deletes before writing. **Proven live:** a re-crawl left one document row with the same id, chunks replaced 23 → 18, Qdrant exactly 18 — refreshed, not accumulated. Two decisions: stored URLs are **re-validated** (a hostname safe at creation can resolve internally later — resolving-then-checking only means anything at the moment of use), and documents for **vanished pages are kept** (a site that 404s during a deploy must not empty a knowledge base as a side effect of a refresh).

**Two schema facts closed on the way:** `Document` never carried `source_url` despite the column existing since Phase 12, so nothing above the repository could distinguish a crawled page from an upload; and `DataSourceRepository` had no `save()`, so a status transition had nowhere to go outside the worker's raw SQL.

**A layout defect only real content exposed:** crawled markdown is full of long unbroken asset URLs, and `whitespace-pre-wrap` preserves newlines without breaking a long token — the chunk list scrolled sideways. Fixed with `break-words`, verified by measuring `scrollWidth === clientWidth`.

Mutation-tested: dropping the URL re-validation, the cross-KB check, the chunk-page cap, or the document-total count each fails a test that passes when restored.

**Still not built:** re-embedding without re-fetching. The stored markdown makes it possible (that is why it is stored) and it is the right response to an embedding-model change, but it needs a job that re-embeds `document_chunks` in place rather than re-running the parse.

## Model-configuration governance — platform owns, tenants are entitled

A user-requested audit-then-fix of who controls model configurations. **Audit first, and it changed the diagnosis:** the brief attributed the empty assistant dropdown to the composite FK `(tenant_id, model_configuration_id) → model_configurations(tenant_id, id)` being unsatisfiable for a platform-owned row (`tenant_id IS NULL`). That FK problem is real but was **latent** — the live database had **zero** platform-owned rows and zero configurations visible to the affected tenant, so `list_available_to_tenant` returned `[]`. Fixing only the FK would have left the dropdown just as empty. The actual gap: **platform governance did not exist at all** — no permission, no use case, no route, no UI. The only writes anywhere were raw SQL in `scripts/seed_demo_data.py`.

**The chosen design keeps `model_configurations.tenant_id` and strengthens the DB invariant rather than weakening it.** New table `tenant_model_configurations (tenant_id, model_configuration_id)`, and `ai_assistants`' composite FK **repointed at it**. The old constraint enforced *"the configuration belongs to my tenant"*; the new one enforces *"the configuration has been granted to my tenant"* — the actual business rule, and one a platform-owned row can satisfy. A single-column FK (the `tenant_roles` precedent) would have been a smaller migration that dropped a DB-level tenant invariant and replaced it with nothing.

**Proven at the database level, as the migrator superuser — bypassing RLS, permissions and every line of application code:** an assistant referencing an unentitled configuration is refused by `fk_ai_assistants_model_configuration`, and revoking a grant an assistant still uses is refused with *"still referenced"*. That is the "do not rely solely on RLS" requirement met by construction. 8 tests in `tests/integration/db/test_model_configuration_entitlements.py`.

**Revocation policy falls out of the FK for free:** the database refuses to strand an assistant. `RevokeModelConfigurationFromTenant` counts blockers first and raises a 409 naming the number, so the operator gets *"1 assistant(s) in this tenant still use this model configuration"* rather than a 500 from a constraint violation. Retiring a model fleet-wide is therefore **archive → migrate assistants → revoke**, with nothing broken at any step. Archiving is deliberately weaker than revoking: it withdraws a model from *new* assignments and leaves existing assistants alone.

**Why not `tenant_features`.** It exists and is a real entitlement mechanism, but it is keyed by a TEXT `feature_code`, cannot be a foreign-key target from `ai_assistants`, has no write API, no RLS and zero rows. Using it would have meant encoding UUIDs into feature-code strings.

**No new pipeline, and `tenant_id` was not removed** — it is the only ownership marker, and tenant-owned rows created before entitlements are still in use (2 live assistants depend on one). The migration backfills grants from **the assistants themselves** union all tenant-owned configurations, so no currently-valid assistant could be orphaned; verified against live data before and after.

**New:** `platform.model_configurations.manage` (one permission, gating reads too — the catalogue has no read-only audience), `application/ai_resources/manage_model_configuration.py` on the BYPASSRLS platform UoW, `/v1/platform/model-configurations` routes, a Platform → Model configurations console screen, and `archived_at` on the entity and table.

**The tenant-facing response got narrower, not wider:** `ModelConfigurationResponse` dropped `tenant_id` and `is_platform_default`. Every row returned is now assignable, so both fields would only invite a client to re-derive a rule the server already applied — and who owns a configuration is platform information.

**Driven live end to end:** platform admin creates → tenant sees nothing → grant → tenant sees it → tenant creates an assistant with it (201) → hand-submitted ids for another tenant's *and* an ungranted configuration both **404** (never 403, so neither is provable to exist) → revoke while in use **409** → tenant admin's create/grant/list all **403**. The assistant dropdown, previously empty with a message explaining a foreign key, now offers the granted model with no note at all.

**One pre-existing defect fixed in passing:** `IntegrityError` was unmapped in `api/exception_handlers.py`, so any FK violation returned a 500.

**Tightening the FK broke 70 assertions in the pre-existing RLS suite** — `tests/integration/db/test_ai_resources_rls.py` builds its graph in raw SQL and now needs the entitlement row too. The correct failure, and one worth budgeting for: application code, use cases and unit tests were all green while the integration fixtures still described the old schema, and that only surfaces ~27 minutes into the full suite. Written up in [docs/18](docs/18-schema-rls-and-migrations.md#tightening-a-foreign-key-breaks-every-hand-built-test-fixture).

## Enterprise text extraction — route on what the bytes are, not what they claim

A user-requested rebuild of document text extraction, informed by a reference project at `D:\Docs of Everything\Docling\main_project`. The organising rule, and the only one worth remembering: **use the cheapest parser that can read this format well, and reserve the layout models for documents whose structure exists only as pixels.**

That is not a speed preference. Docling reconstructs structure from a *rendered page*, so running it over a DOCX discards markup that already states which run is a heading and which cell belongs to which column, then guesses it back — slower **and** worse. This codebase had already proved the point once for text-layer PDFs (`fast_pdf.py`, 1,351×); the same reasoning covers every format whose structure is declared rather than drawn.

**New:** `infrastructure/parsing/file_kind.py` (content-based detection + ZIP-bomb guards), `native_office.py` (Word/PowerPoint/Excel read directly), `markup.py` (HTML, `.eml`, encoding detection), a rewritten `dispatcher.py`, and a hardened docling path in `rich_documents.py` (RapidOCR on onnxruntime, `PyPdfiumDocumentBackend`, `ThreadedStandardPdfPipeline`, and a **one-page-at-a-time retry on memory failure** — the first real failure was a valid 40-page document, not a broken one). Five new `IngestionSettings` fields. HTML and `.eml` were previously refused with a 415 while the *crawler* happily ingested the same HTML from a URL — the same content accepted or refused depending on how it arrived.

**Routing is decided from magic bytes, never the extension or `Content-Type`** — both are caller-controlled. That is a correctness win as often as a security one: browsers send `application/octet-stream` for perfectly ordinary uploads, and refusing those is a bug every tenant sees. `UNKNOWN` is deliberately absent from `SUPPORTED_KINDS`, which is what makes the upload endpoint's 415 honest rather than a guess.

**The ZIP guards are the security half.** OOXML, ODF and EPUB are ZIP files, and a ZIP is a decompression instruction written by a stranger. Detection reads only the central directory, so identification can never be made expensive; expansion is bounded separately by entry count, total uncompressed size **and** per-entry ratio. Both size checks exist because either alone lets a shape of archive through, and the ratio check exempts small entries — a 40-byte XML stub expanding to 40 KB is 1000× and completely harmless.

**Three decisions worth not re-litigating:**
1. **Declining is a distinct signal from failing.** The dispatcher falls through on `ParserDeclined` and deliberately does **not** on `DocumentParseError` — a broken file must report its own reason rather than be handed to OCR that will also fail, slowly, and say something less useful. A file whose bytes are not a PDF at all is now 415 (*unsupported*), not 422 (*unparseable*); those are different facts and the tenant can act on the first one.
2. **The OCR fallback for Office files needs thin text *and* pictures.** Either alone is wrong: a one-line memo is legitimately thin, and a good report with a chart parses perfectly. Together they describe a scan pasted into Word.
3. **XLSX has no fallback.** A spreadsheet's content is its cells; docling reading a *rendered* spreadsheet is strictly worse. An empty workbook is empty, not mis-parsed.

**`decode_text` asks the file before it guesses.** UTF-8 first (bytes that decode cleanly as UTF-8 are essentially never anything else by accident, and a stale `charset=iso-8859-1` in a template now emitting UTF-8 is an everyday occurrence), then the declaration — HTML `<meta charset>` or an email part's `charset=` — then statistics. Measured, not assumed: charset-normalizer decoded a twelve-byte French `café` into an Arabic presentation form, and short documents are ordinary here.

**A third resource attack, found by auditing the dependency list rather than the code.** `defusedxml` was declared and imported nowhere — so before removing it as speculative, I checked whether the XML path needed it. `structured.py` carried a *comment* asserting billion-laughs "does not apply" to `ElementTree`. Half true, and the wrong half: external entities really are not resolved, but internal ones **are** expanded. Measured against the real parser, a ~400-byte file with four entity levels produced **30,006 characters**, and every further level multiplies by ten. `_MAX_XML_ELEMENTS` cannot help — expansion happens during parsing, before there is anything to count. Now parsed with `defusedxml`; the bomb and an XXE `file://` reference are both refused with an actionable reason, and ordinary XML (including a plain DOCTYPE-free document) is untouched. **A comment claiming a safety property is worse than no comment**, because it stops the next person checking. `filelock`, which really was unused, is gone.

**Mutation-tested.** Routing on the extension, dropping the archive check, and dropping the "and images > 0" half of the fallback condition each fail tests that pass when restored. The archive test **initially passed with the guard deleted** — its fixture was a stub with a fake `word/document.xml`, refused by python-docx either way. It is now built from a real document plus one oversized entry, so the guard is the only thing that can fail it. Same failure mode as Phase 12's SSRF test; it is worth assuming a new negative test passes for the wrong reason until a mutation says otherwise.

### The defect that made all of it moot: the BFF proxy destroyed every binary upload

The 40-page PDF that had been failing was not a scan. **95% of its non-ASCII bytes were U+FFFD.**

`frontend/src/app/api/backend/[...path]/route.ts` read every request body with `await request.text()` — a UTF-8 decode with `errors="replace"` — and re-encoded it upstream. Multipart boundaries and headers are ASCII, so the request stayed well-formed and the file still arrived, saved and processed; only the *contents* were gone. A 792 KB PDF was stored as 1.43 MB (1 byte → 3), its pages rendering pure white with zero page objects. **The stored file's stale `startxref 792600` points exactly at where the original's xref sat** — the arithmetic confirms the diagnosis on its own.

It had been invisible for the same reason it was destructive: text-only PDFs and every JSON body survive a UTF-8 round trip unharmed, so everything except binary-rich files worked. Fixed with `request.arrayBuffer()`; logout still decodes, deliberately and narrowly, because it is the one path that must look inside the body.

**Proven by hash, not by inspection.** A purpose-built 799-byte PDF with Flate-compressed streams (15% high bytes; a UTF-8 round trip inflates it to 1017 and changes its SHA-256) uploaded through the proxy now stores **799 bytes, identical SHA-256, zero U+FFFD**, and reaches `ready` with its text extracted and cited `page 1`. A structured DOCX indexed 6 chunks carrying `Refunds > Eligibility`, `Shipping (table)` and a markdown table with column association intact.

**There is no frontend test runner in this repo**, so this fix is guarded by live proof only. Adding one is the obvious next step and is not claimed as done.

### One rule, three places, three disagreements

Driving the above end to end surfaced a second defect with a shared root cause: **"is this account usable?" was written out three times and the three answers differed.**

`LoginUser` deliberately admits `PENDING_VERIFICATION` — this deployment's email sender only logs, so nobody can ever complete verification and refusing them would lock out every self-registered user permanently. But `api/deps/authn.py` asked `user.is_active` (which requires `ACTIVE`), and `workers/job_context.py` demanded `status = 'active'` in raw SQL. So a self-registered account **signed in successfully, was refused on every subsequent request** with an opaque "session is no longer valid", and any file it uploaded returned 201 and then sat in `processing` for ever — the job died before it had a document row to mark failed, so nothing anywhere said why.

The rule now lives once, as `User.can_authenticate`, and all three paths ask it. Suspended, deactivated and deleted accounts are still refused everywhere, so threat-model scenario 8 is unaffected. **The drift, not either rule on its own, was the bug** — which is why the fix is a single shared predicate rather than three edits.

Nothing caught it because **every existing auth test verifies the email first**, so the suite never left an account in the one state that was broken. The new tests deliberately do not: `test_an_unverified_account_can_actually_use_its_token` and `test_an_unverified_account_is_allowed_to_have_its_job_run`, plus `test_a_deactivated_account_is_refused` — that last one because the worker check is now allow-by-default, so a new revocation state added to `UserStatus` without being listed would pass silently.

**A local environment note, not a code change:** the uncommitted `docker-compose.dev.yml` moves Postgres to `127.0.0.1:15432` while `.env` still says `55432`, so nothing connects without an override. Left alone rather than guessed at — pass `DATABASE__PORT=15432`, or reconcile the two.

## Model configurations, wired into inference — the picker used to be decorative

A user question — "the model configuration doesn't contain anything else, what's the use of it?" — turned out to be a correct observation of a real gap, not a misunderstanding. Auditing `answer_question.py` before writing anything: `AnswerQuestionQuery` took a `knowledge_base_id`, never an `assistant_id`; the chat model called was `settings.chat_model`, one value from `.env`, identical for every tenant and every assistant on the platform. `ai_assistants.model_configuration_id` and `system_prompt` were stored, entitlement-checked, and shown in the console's picker — and then never read again. Worse: `ai_assistants` has no `knowledge_base_id` at all, so there was no way to "ask an assistant a question" in the first place — the Ask panel and the public widget both, and still, answer by knowledge base directly.

**The fix is additive, not a redesign of the answer surface.** `AnswerQuestionQuery` gained an optional `assistant_id`. Supplied, `AnswerQuestion.execute` loads and authorizes the assistant (`load_visible_assistant`, the same helper every other assistant read uses), refuses it if archived, re-checks `is_available_to_tenant` against the *current* entitlement rather than trusting the stored `model_configuration_id` (a platform admin can revoke a grant after an assistant was created), and resolves `model_name`/`parameters`. Omitted, every existing caller — the Ask panel with no assistant picked, and the public widget, which was deliberately left untouched in this pass — behaves exactly as before, proven by a dedicated regression test. `ChatModel.stream_answer` grew matching optional `model_name`/`model_parameters` keyword arguments with the same "omitted = old behaviour" contract.

**The system prompt is appended, never substituted.** `SYSTEM_PROMPT`'s grounding rules — cite everything, refuse without sources, treat fenced content as data never instructions — are this pipeline's actual safety property, the one `answer_question.py`'s own docstring says "none of them is the prompt alone." Letting a tenant-editable `system_prompt` fully replace it would hand every tenant a lever to weaken a guarantee this platform advertises. Mutation-tested: making it a plain override instead of an append fails the test that checks the grounding rules survive.

**Three more mutation-verified checks**, each proven by reverting it and watching the matching test fail: the archived-assistant refusal, the entitlement re-check, and (at the adapter level) that a malformed `parameters.temperature` degrades to the platform default rather than taking the whole answer down — `parameters` is a tenant-editable field, and a bad value there must not be able to break every question through that assistant.

**A second, independent defect, found only by driving the live path, not by reading the code:** `AiAssistantRepository.save()`'s SQL `UPDATE` never listed `model_configuration_id`. `UpdateAssistant` — and the console's "Edit assistant" model picker built on it since the audit-pass session — had been silently unable to change an assistant's model since that picker was built. Every unit test for `UpdateAssistant` drives it against an in-memory fake with no SQL statement to be missing a column from, so nothing could have caught it without hitting real Postgres. Found because a live end-to-end check kept resolving a *stale* model no PATCH should have left in place. Fixed with the missing column, plus a new integration test (`tests/integration/test_ai_assistant_repository.py`) that spans three separate sessions on purpose — add, edit, then a fresh read-back — so the identity map of a single session can't paper over the same class of bug again.

**Driven live end to end, with real OpenAI calls.** Same knowledge base, same question, asked twice: without `assistant_id` the platform default (`gpt-5.5`) answered plainly; with one naming an assistant pointed at a distinct granted configuration (`gpt-5.4-mini`, `reasoning_effort: low`) and a custom persona instruction, the answer stayed correctly grounded and cited *and* carried the persona's signature — proving model selection, parameter passthrough, and prompt-appending all landed together, not just in isolation under fakes.

**Deliberately not built in this pass, and said so rather than left implicit:** `provider_credential_id` (BYOK) — swapping the OpenAI client per call for a tenant-supplied decrypted key is separable work, and `CredentialEncryptor.decrypt` still sits uncalled by any use case, exactly as before. The public widget was not given an `assistant_id` path; `chat_widgets` has no such column, and adding one is a schema change and a widget-creation-flow change this pass didn't take on.

## Token budgets, enforced — the number was decorative

`model_configurations.token_budget_per_month` shipped with the entitlement work: a platform admin could type it, the API returned it, the console rendered it — and **no code anywhere read it back**. A number that looks like a spending control and is not one is worse than no number, because an operator sets it and believes they are covered.

**New:** `TokenUsageStore` port + `infrastructure/cache/token_usage.py` (Redis, monthly `YYYY-MM` window), `TokenUsage` on the `ChatModel` port, `TokenBudgetExceededError` → **429**, and per-tenant spend on `GET /v1/platform/model-configurations`.

**The shape differs from the widget quota on purpose, and that is the whole design.** A widget question costs one unit, known in advance, so that counter *consumes before* the work. A question costs an unknown number of *tokens*, known only after the model has answered — so this one **reads before and records after**. A single answer can therefore cross the line rather than being stopped exactly at it: accepted deliberately, because the alternative is estimating cost up front and either over-refusing on a bad guess or being wrong anyway. The budget bounds a month, not one response.

**Reading fails closed; recording fails open.** A budget that cannot be confirmed refuses the answer — that failure is invisible until the invoice, which is the entire reason the counter exists. But a *recording* failure happens after the tenant has their answer and the provider has already charged for it; raising then would show an error for work that succeeded and would not un-spend the money. Losing a count is the smaller harm and it is logged. The console's usage *display* also fails open, for a third reason: refusing to render a page over an unavailable counter protects nothing and takes the console down with Redis.

**`finally`, not "after the loop"** — an answer the caller abandons halfway is still billed. The provider charges for tokens it generated whether or not anyone read them, and a counter that only recorded fully-consumed streams would be avoidable by disconnecting early. Mutation-tested: converting the `finally` into normal-completion-only fails exactly that test.

**Usage is requested from OpenAI only when someone will read it**, so an unmetered answer sends the request it always sent. `stream_options={"include_usage": True}` was **probed against the live API before being written into the adapter** — `temperature` taught this codebase not to assume a parameter is accepted — and the usage-bearing chunk carries *no* `choices`, so it is read before the `continue` that would otherwise discard it unnoticed.

**Three mutations, three caught:** removing enforcement (3 tests fail), making the unreadable counter fail *open* (the fail-closed test fails), and recording only on completed streams (the abandonment test fails).

**Driven live with real OpenAI calls:** an answer through a budgeted assistant recorded **647 real tokens**; lowering the budget to 100 made the next identical question return **429 — "this model's monthly token budget of 100 is spent (647 used)"**; raising it back restored service. The platform-default path answered normally throughout.

**One limit, stated rather than left to be discovered:** budgets attach to a *model configuration*, so only answers that resolve one — i.e. those naming an assistant — are metered or capped. A "Platform default" answer uses `OPENAI__CHAT_MODEL` from `.env`, which is not a configuration row and has no budget field, so it is neither counted nor limited. Closing that would mean making the platform default a real configuration row; it is a design decision, not an oversight, and `test_a_platform_default_answer_is_not_metered` asserts the current behaviour so it cannot change silently.

## Bring-your-own-key — the credential a tenant stored paid for nothing

`provider_credentials` had a full CRUD surface since Phase 7 — create, rotate, revoke, a console screen, Fernet envelope encryption — and **`CredentialEncryptor.decrypt` was called by no code anywhere**. A tenant who pasted their own OpenAI key was still billing the platform for every question, with nothing in the console, the response or the logs saying so. Same defect shape as `token_budget_per_month`, and the second one found in this file's own "deliberately not built" list.

**The audit changed the design before any code was written.** The obvious move was to read `model_configurations.provider_credential_id`, which already existed. It is the wrong column: a configuration is platform-owned and granted to *many* tenants, so one credential on it can only ever name one key for everyone — it cannot express "bill tenant A when tenant A asks", which is the whole of BYOK. Worse, `provider_credentials` is under tenant RLS, so a platform admin's own credential is invisible from any tenant's answer path and would have made every answer through such a configuration fail. The attachment belongs on the **grant**, where the (tenant, model) pair already lives.

**New:** `provider_credential_id` on `tenant_model_configurations` (migration `f3c8a1d5be62`), `SetModelCredential` + `PUT /v1/tenants/{id}/model-configurations/{id}/credential`, `credential_ciphertext` on the `ChatModel` port, a decrypting client cache in `openai_chat.py`, `ProviderCredentialUnusableError` → **409**, and a "which key pays for each model" table on the tenant's Provider credentials screen.

**Tenant-confinement is enforced by Postgres, not by application code.** The new FK is composite — `(tenant_id, provider_credential_id) → provider_credentials(tenant_id, id)` — and `tenant_model_configurations.tenant_id` is NOT NULL, so another tenant's credential *and* a platform-owned one (`tenant_id IS NULL` can never match a NOT NULL column) are both unreachable whatever id a request carries. **Proven as the superuser, bypassing RLS and every line of application code:** the owning tenant attaches it, another tenant is refused by the constraint, and deleting a credential still paying for a model is refused with *"still referenced"*.

**The plaintext key never enters `application/`.** `CredentialEncryptor`'s own docstring already specified that only the AI-execution infrastructure decrypts, at model-call time — so the use case resolves the *ciphertext* and the adapter is the only thing that opens it. Clients are cached by a digest of the **ciphertext**, not the credential id: rotation then produces a new cache key instead of silently serving the superseded secret.

**Three decisions worth not re-litigating:**
1. **A grant that names an unusable credential fails the answer — never falls back to the platform key.** The fallback is the tempting option because answers keep flowing; it would move the bill from the tenant's provider account to the platform's with nothing reporting it. First notice would be an invoice.
2. **Only a *tenant's own* 401/403 is reblamed as a credential problem.** The same status on the platform's key is a deployment fault, and telling a tenant admin to fix a credential they do not own sends them somewhere they can change nothing. Matched on status code rather than `isinstance(openai.AuthenticationError)`, so a provider-compatible gateway raising its own type is still recognised — and 429/500 are deliberately *not* matched.
3. **The configuration-level `provider_credential_id` stays platform-scoped and is not read from the tenant answer path**, stated rather than left as a mystery: it is invisible under tenant RLS, and since the platform pays either way, leaving that case to `OPENAI__API_KEY` swaps nobody's bill.

### Two defects only the live run could show

**1. The write was refused by RLS, and the tenant saw a 404 for a model they could plainly see.** `tenant_model_configurations` had a **SELECT-only** policy — correct while the platform was the only writer, since a grant is something done *to* a tenant. One column on that row is now genuinely the tenant's decision. But adding an UPDATE policy alone would have been a privilege escalation: `ALTER DEFAULT PRIVILEGES` had handed `app_tenant` UPDATE on **every column**, including `tenant_id` and `model_configuration_id`, so a tenant could have repointed their own grant at a model they were never granted. Migration `a7d4e91c3f08` therefore uses **two mechanisms**: a column privilege says *what* (`REVOKE INSERT, UPDATE, DELETE`, then `GRANT UPDATE (provider_credential_id)` — RLS cannot express "only this column"), and an RLS policy says *which row* (`USING` **and** `WITH CHECK`; `USING` alone would permit rewriting a row *into* another tenant). `app_tenant` now holds SELECT plus UPDATE on exactly one column. Third recurrence of the Phase 8 `audit_logs` lesson: default privileges are granted once and never narrowed unless a migration does it deliberately.

**2. Every BYOK failure read "the answer could not be completed".** A credential rejection is only knowable once the provider is called — which is *inside* the SSE generator, after the status line has gone. The handler flattened everything to one opaque string, so the 409 branch was unreachable text and a tenant admin with a bad key learned nothing. `AiResourceError` now streams its own message (those are written for the caller); everything else stays opaque. Same class as Phase 14's "every widget error looked identical to the visitor".

**Proven live, and the proof is the part that matters.** "It answered" is exactly what a silent fallback also looks like, so the decisive test was a credential holding a **deliberately invalid** key: the answer failed with *"the provider rejected this model configuration's credential"* — a fallback would have answered normally. Then revoking that credential returned **409 before the model was called**, and restoring the tenant's real key resumed answering. A foreign credential id and an ungranted model both answer **404**, never 403.

**Mutation-tested, eight guards:** dropping the ciphertext en route to the adapter, falling back on a revoked/invisible credential, ignoring the ciphertext in the adapter, keying the client cache on a constant, reblaming the platform's own 401, trusting a caller-supplied credential id, treating a zero-row update as success, and allowing a revoked credential to be attached — each fails exactly the matching test and passes when restored.

**A pre-existing breakage found on the way:** the token-budget pass added `usage=` to every `stream_answer` call without widening `_FakeChatModel`, so **six tests in `test_answer_question_cases.py` were failing** and had not been noticed, because that pass only ran its own new file. A fake narrower than the Protocol it stands in for fails every caller with a `TypeError`.

**Still not built:** BYOK for the public widget (`chat_widgets` resolves no assistant, so it reaches no configuration and no credential) and per-credential spend reporting — token usage is still keyed by configuration, so a tenant on their own key sees their count but not a separate bill.

### The tenant can now see what an assistant buys them

The user's original question — *"what is the real use of AI Assistants? does it only control how many tokens they can use?"* — was fair, because nothing in the tenant's own console showed the answer. Budget and spend existed only on the **platform** screen, so the first sign of a cap was a 429 mid-answer.

`ModelConfigurationResponse` gained `token_budget_per_month` and `tokens_used_this_month`, and the Assistants screen grew a **Model** column showing the model name, `3,228 / 30,000 tokens`, and *"Billed to your own key"* when BYOK is attached. Deliberately wider than the narrowing that removed `tenant_id`/`is_platform_default`: those invited a client to re-derive a server-side rule, whereas a budget is something the tenant *hits*. A revoked grant reads *"No longer available to this tenant"* rather than a blank cell — the next question through that assistant fails, and the fix is a platform admin's.

Reads fail open (`None` → `?`, never `0`), matching the platform screen and for the same reason.

**Verified in a real browser**, not only by curl: both screens render live against the running stack.

### A stale comment, and a guard that had stopped running

Two things found while finishing:

1. **`ModelConfigurationField`'s docstring still explained the old foreign key** to a tenant administrator — the constraint the entitlement work removed. A comment asserting a property that is no longer true is worse than no comment, the same lesson `structured.py`'s billion-laughs comment taught.
2. **`_reject_near_miss_group_names` had stopped firing.** It ran *after* `super().__init__()`, and newer pydantic-settings now rejects a near miss itself with a generic `extra_forbidden` naming the lowercased field. The deployment still refused to start, so the regression was invisible except as a worse message — the guard's entire contribution, *naming the correct spelling*, was silently lost. Moved before `super().__init__()`; `OPENAI_API_KEY` once more produces **"did you mean OPENAI__API_KEY?"**.

   One case is left refused rather than fixed, and stated instead of quietly patched: a machine with a stray `OPENAI_API_KEY` exported for the OpenAI CLI **and** the correct name set cannot start, because `extra="forbid"` rejects the stray even though the guard tolerates it. Relaxing that would mean changing a decision docs/21 records as confirmed, so it is asserted in the test with its reasoning.

## Conversation memory, input guardrails, and prompt precedence

Three features in one pass. The common thread: each closes a gap where the platform *looked* complete and was not.

**Memory.** `conversations` has existed since Phase 7 carrying only metadata — who, which assistant, when. The turns lived nowhere, so a conversation could be listed but never reopened, and every question reached the model with no memory of the one before it. New `conversation_messages` (migration `b8e2f4a71c93`, RLS, composite FK, **`UPDATE` revoked** — a turn that was said cannot be un-said, only the conversation deleted).

**Memory is stored, not recomputed.** `conversations.summary` holds a precis of the compacted prefix and `summary_through_seq` records how far it reaches, so assembling context is "the summary, plus messages after N" — never a re-read of the thread. Three tiers in `conversation_memory.py`: rolling summary → last 6 turns verbatim → retrieved passages. Sending the whole thread fails twice over: cost grows quadratically, and once the thread outgrows the window the *oldest* turns fall out silently — precisely backwards, since the beginning is where someone stated what they were trying to do.

**Compaction is extractive, not model-written.** Summarising with the chat model would put a second paid call on the answer path and hand a poisoned earlier turn a chance to rewrite the record of what was said. `seq` is a per-conversation ordinal rather than a timestamp: two messages in the same millisecond still need a defined order.

**Guardrails** (`domain/ai_resources/guardrails.py`) — one place, two deliberately different entry points. A question is written *by* a person and may be refused; a retrieved passage is content the tenant chose to index and is **neutralised, never refused** — a document containing "ignore all previous instructions" is usually a security policy, and dropping it would silently make the knowledge base wrong. What the layer is honestly for: bounding size, stripping control characters (ANSI, NUL, bidi overrides), refusing the small set of requests with no legitimate phrasing, and closing the **fence escape** — a passage carrying the adapter's own delimiter could otherwise end the quoted region and have its remainder read as prompt. Blocklisting injection phrasing cannot be won at the input; the structural defences carry the weight.

Patterns are deliberately narrow: the test is not "could this be an attack?" but "is there a real question this also matches?" Six ordinary questions (*"What is our password reset policy?"*, *"How do I rotate an API key?"*) are asserted **not** refused — a false refusal is a worse product than a missed paraphrase.

**Prompt precedence**, written where it is enforced: platform → assistant persona → memory → sources → question. Each layer is introduced with an explicit statement of its standing; history is fenced as `<<<HISTORY>>>` and declared a record, non-citable and non-authoritative.

**Three defects only the live run could show**, every one invisible to a green suite:
1. **The security event rolled back.** The refusal was recorded and then `raise`d *inside* the same unit of work — docs/18's rollback pitfall in its most damaging shape: the record of an attack disappearing because the attack was refused. Caught by a fake that simulates real rollback. Now records, exits the block, then raises.
2. **`/conversations/search` was parsed as a conversation id** (`UUID("search")` → 500). My own docstring warned that FastAPI matches in definition order — and appending the routes at the end of the file defeated it. Moved ahead of `/conversations/{conversation_id}`.
3. **The router never forwarded `conversation_id`**, so memory silently did nothing. The first live run *looked* correct because retrieval alone answered the follow-up — a coincidence, not memory.

**A fourth, in the test harness:** `tests/conftest.py` truncates every table in the metadata, so a new table that exists in `iam_platform` but not `iam_platform_test` errors 117 tests. **After any migration that adds a table, migrate the test database too:**

```bash
DATABASE__NAME=iam_platform_test python -m alembic upgrade head
```

A column-only migration does not break `TRUNCATE`, which is why the BYOK migrations had left that database two revisions behind unnoticed.

**Verified live:** guardrail → 400; a two-turn thread where "it" resolved from history; 4 turns persisted with an auto-title from the first question; search 1 hit / 0 miss; rename 204; delete → cascade to 0 messages; admin roster 3.

**Each answer records what it used.** `citations` stores only the labels the model *actually cited* -- not every candidate offered -- because reopening a thread should show what the answer drew on, and the full candidate set would misrepresent it. `token_count` goes on the assistant turn: the provider reports one number for the exchange, and splitting it across the question and the answer would be a guess presented as a fact. The console renders both (`Sources: [1] page 5 — 994 tokens`).

### Widget memory — Redis, not Postgres, and that is the design

A visitor gets memory too, but stored differently, and the difference is the point. `WidgetSessionClaims` carries no user id, no membership and no permissions *by construction*, so there is nobody for a stored thread to belong to and nobody with standing to delete it. Three consequences, each a reason rather than a convenience:

* **It expires by itself.** The Redis key's TTL is the session token's own lifetime, so memory and the right to read it end together. Persisting a stranger's questions past the session would be storing personal data with no owner, no retention policy and no deletion path — on the tenant's behalf, without either party asking.
* **`conversations` would not take it anyway.** `membership_id` and `assistant_id` are both NOT NULL and both meaningless for a visitor; making them nullable to fit an anonymous session would weaken a constraint that currently guarantees every stored conversation has a real owner.
* **It fails open, where the quota store beside it fails closed.** An unconfirmable quota must never become unlimited spending. An unavailable memory just means a context-free answer — what the visitor would have had anyway — and refusing would take a working widget down over a cache blip.

Keyed by `session_id` from the token, never by widget or IP: two visitors on the same page hold different sessions and must not see each other's questions. Four turns, shorter than the authenticated window — a widget exchange is a support question and its follow-ups, and every stored turn is prompt budget spent on someone who may have closed the tab. `ConversationMemory` is reused rather than a second prompt formatter written, so the "history is a record, not an instruction" fencing has one implementation.

**A deployment with no store wired takes the original code path**, not "the new path with an empty memory" — those look identical today and diverge the moment the parameter grows a default. A test asserts it.

**Driven live against the real widget:** two turns on one session where *"Give me one example of it"* resolved to the primary key from turn 1, `widget:memory:<session>` present in Redis with **TTL 1760s of 1800** and exactly 4 turns stored.

**Still not built:** nothing outstanding from this pass.

## Tenant entitlements, chatbot configuration, teams and human handoff

A large pass turning the platform into a governed multi-tenant AI *product*:
the platform now decides what each tenant may have and spend, the tenant now
configures its own chatbot, and a conversation can leave the AI for a human
and come back.

**New tables** (migration `c9f2a4d81b57`): `tenant_entitlements`,
`tenant_chatbot_settings`, `tenant_teams`, `tenant_team_members`. Extended:
`model_configurations` (provider + encrypted `api_key_ciphertext` + embedding/
chat model, timeout, reasoning effort, `enabled`), `ai_assistants` (role/avoid/
personality/response_length), `chat_widgets` (assistant binding + presentation),
`conversations` (state machine + handoff fields + visitor ownership),
`conversation_messages` (three new roles + author), `data_sources`
(`direct_text`).

**Six decisions worth not re-litigating:**

1. **A limit governs creation, never existence.** Withdrawing
   `allow_create_assistant` from a tenant with three assistants leaves those
   three working -- `may_create_*` is asked at the point of creation and
   nowhere else. A platform that silently disables working resources when an
   operator edits a number is one nobody can administer safely.
2. **`None` means uncapped and is not `0`.** Zero is a real, enforceable limit
   meaning "none at all". Collapsing them would make "unlimited" unexpressible
   and turn an unset field into a total lockout.
3. **`tenant_entitlements` needs two mechanisms to be read-only to tenants.**
   RLS says *which rows*; it cannot say "no writes". `ALTER DEFAULT PRIVILEGES`
   had already granted `app_tenant` full CRUD, so the migration REVOKEs
   INSERT/UPDATE/DELETE explicitly. **Fourth recurrence** of the Phase 8
   `audit_logs` lesson.
4. **`conversations` losing `NOT NULL` on `membership_id` is a narrowing.**
   A CHECK now requires *exactly one* of `membership_id` / `visitor_session_id`
   -- which the old constraint could not express, since it permitted a row
   claiming both. This is what lets a visitor thread live in the same table as
   a member's rather than in a second chat system.
5. **Conversation state is one column, not a spread of booleans.**
   `is_handed_off` + `is_claimed` + `is_resolved` admit combinations that mean
   nothing, and every reader then invents its own precedence rule.
6. **Only OpenAI is marked `supported`.** Anthropic/Gemini/xAI are modelled and
   **refused at write time** with a message naming the supported set, rather
   than offered and failing on the first question.

**The concurrency-safe claim is in the repository, not the entity.** Two agents
can both load an unassigned conversation and both call `claim()`; nothing in a
domain object can prevent that, because by then the read is stale. A conditional
`UPDATE ... WHERE state = 'unassigned'` makes the check and the write one
statement -- Postgres serialises it, exactly one sees rowcount 1. **Proven live:
two simultaneous claims returned 204 and 409.**

**Quota shapes differ on purpose.** A *message* costs one unit, known in
advance, so the daily counter **reserves** with atomic `INCR` (the naive
`SELECT count` then write loses under concurrency in the direction that costs
money). *Tokens* cost an unknown amount known only after the answer, so the
monthly counter **reads before and records after**. Both reads fail closed;
recording fails open; the console's *display* fails open too (`None` renders
`?`, never `0`).

**Prompt layering states standing rather than relying on position.** Each
tenant-authored block is introduced with "does not override the rules above",
and `avoid` is declared additive-only. Position alone establishes nothing: a
model has no way to know which of two contradictory instructions came from the
platform unless it is told. Personality and response length are **enums mapped
to fixed internal strings** -- a stored value outside the enum degrades to the
default, so a hand-written row cannot smuggle instruction text through a field
the UI presents as a dropdown.

### Two defects only the live run could show

1. **The daily-limit ceiling returned 500.** `UpdateChatbotSettings` raised a
   bare `ValueError`, which no handler maps -- and
   `test_exception_mapping_is_exhaustive.py` could not see it either, because
   it only scans `AiResourceError` subclasses. **Third time** this codebase has
   learned that an exception declared outside the module base class is
   invisible to the very test written to prevent it. Now
   `ChatbotSettingsInvalidError` -> 400, naming the ceiling. Mutation-tested:
   reverting to `ValueError` fails the new test.
2. **Claiming a nonexistent conversation said "another agent already has it".**
   The conditional UPDATE returns zero rows for *both* "already claimed" and
   "does not exist", and the use case reported both as 409 -- sending an agent
   looking for a colleague who was never there. The failure path now
   disambiguates: absent (or cross-tenant, invisible under RLS) -> 404,
   genuinely taken -> 409.

**Driven live end to end** against real Postgres/Redis/Qdrant: entitlements
backfilled; tenant admin writing entitlements **403**; KB creation at the limit
**409** naming the numbers; assistant creation with the capability withdrawn
**403**; teams created from tenant-supplied names; handoff -> inbox -> two
concurrent claims (204/409) -> agent reply + internal note -> return-to-AI ->
inbox empty. All five message roles verified coexisting in one conversation,
and the audit trail carries entitlement, chatbot-settings, handoff, claim and
return-to-AI entries.

**Deliberately not built, and stated rather than implied:** the frontend
console for any of this; the Direct Text ingestion job (schema and domain
only); provider-config CRUD routes (columns and capability validation exist,
the adapters still read `.env`); quota enforcement wired into the answer path
(the store is built and concurrency-safe, nothing calls it yet); and the AI
handoff summariser (the best-effort hook and `INTERNAL_COMMENT` write path
exist, the summarising implementation does not).

**No WebSocket layer exists on this platform** and none was added. Realtime
inbox updates ride the SSE transport already used for streamed answers and
already proven to pass through the console BFF proxy unbuffered, with Redis
pub/sub fanning events between API processes. An in-memory fan-out would be
correct in development and silently broken behind a load balancer.

## Queue scoping, widget persistence and retention

Two product decisions the user made explicitly, plus the defects found
implementing them.

**Agents see only their own teams' queues.** New permission
`tenant.conversations.view_all` separates "may work the inbox" from "may see
the whole tenant's inbox"; without it the queue is scoped to the teams the
caller staffs, resolved by `resolve_queue_team_scope()`. The scope goes *into*
the query, so another team's conversation is never loaded rather than loaded
and hidden — and **the SSE stream applies the same scope**, or an Admissions
agent is chimed for a billing dispute they then cannot see. The repository had
accepted a `team_ids` filter since the handoff work and **nothing had ever
passed it**: team routing was real in the data and decorative in the
notification. `tenant_team_members` has no lead/supervisor column, so
leadership is currently *team membership*; recorded in the code rather than
faked with a column nothing reads. **Re-run `scripts/bootstrap_tenant_catalog.py`**
to seed the new permission.

**Widget conversations are persisted from the first question**, reversing the
original "persist only on escalation" decision. What changed is that keeping
them now has an expiry date: `tenant_chatbot_settings.conversation_retention_days`
(migration `e1b6d70c94af`, NOT NULL, default 30, CHECK 1–3650) with
`PurgeExpiredConversations` and `scripts/purge_expired_conversations.py`
enforcing it. **NOT NULL on purpose** — a nullable column makes "keep forever"
expressible by accident, which is the outcome the default exists to avoid.

`visitor_conversation.py` is the single creator both paths use; two creators
would produce two shapes of visitor thread, and the `exactly_one_owner` CHECK
only catches the one that is outright wrong. Redis memory is kept *alongside*
Postgres and that is deliberate: Redis is the bounded working window the
prompt is built from, Postgres is the record retention deletes. Persistence is
**best effort** — it runs after the visitor already has their answer, so
raising would show an error for work that succeeded, on the one surface with
nobody to explain it to.

**Four defects, each found by running it rather than reading it:**
1. **The purge landed on the wrong repository class** — `AttributeError` at
   runtime, invisible to every test that used a fake.
2. **A broad `except` turned that into "purged 0"** — a wiring bug reading as
   a clean sweep, which is exactly how a retention job silently stops
   retaining. Failures are now counted on the summary line and the exit status
   is non-zero.
3. **The synthetic system actor violated an FK.** `audit_logs.actor_user_id`
   references `users`, so an all-zero uuid was refused and the transaction
   rolled back — correctly atomic, but nothing was deleted. The column is
   nullable: a scheduled deletion records **no actor** rather than a fake one.
4. **The purge is a script, not a Celery task.** Workers connect only as
   `app_tenant` by design, so enumerating tenants from one would mean handing
   a background job BYPASSRLS.

Live: retention 1 day + a back-dated row → 12 → 9 conversations, 27 → 15
messages (cascade), audit row surviving with the counts. Two ordinary widget
questions → one conversation, 4 turns, titled from the first question,
`membership_id` null. Stored text verified real UTF-8 (38 chars / 40 bytes, no
U+FFFD) rather than assumed.

**Status:** `ruff` clean; **681 tests** (`tests/unit` + `tests/security`, up
from 510); `mypy src/iam_platform` **20 pre-existing errors**, none in the
changed files. **Not built:** push notifications, badges and vibration for a
closed or minimised console — the agent chime still requires an open tab.

## Ground rules for continuing this project

- Never generate the entire project in one response — follow the phase sequence.
- For code phases: full file path before every code block, complete imports, executable code (not pseudocode), Alembic migrations included, tests included with each module, complete type hints, PEP 8 / Pydantic 2 / SQLAlchemy 2.0 conventions.
- Every tenant-owned query must be tenant-scoped; platform and tenant permissions must stay strictly separated at the schema level, not just in application logic.
- State assumptions before implementing anything non-obvious; explain security-critical decisions inline.
