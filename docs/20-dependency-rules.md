# Dependency Rules

## Layer dependency matrix

| Layer | May import | Must NOT import |
|---|---|---|
| `domain` | stdlib, `pydantic` (as a data-modeling library only) | `core`, `application`, `infrastructure`, `api`, `workers` — domain is fully self-contained |
| `core` | stdlib, generic third-party libs with no project meaning (`pyjwt`, `argon2-cffi`, `pydantic-settings`, `structlog`, `opentelemetry-*`) | `domain`, `application`, `infrastructure`, `api`, `workers` |
| `application` | `domain`, `core` | `infrastructure`, `api`, `workers` |
| `infrastructure` | `domain`, `application` (to implement its ports), `core` | `api`, `workers` |
| `api` | `application`, `core`; `domain` types for read-only response shaping only (never instantiates domain policy logic directly) | `infrastructure` directly (always receives concrete implementations via `Depends()` injection, never imports e.g. `infrastructure.db.repositories` itself) |
| `workers` | `application`, `infrastructure`, `core` | `api` |
| `tests` | everything | — (tests are the exception, by design) |

**Why domain has zero project-internal dependencies:** it's the one layer that should be testable with no I/O, no event loop, no settings object — just plain Python function calls. If `domain` needed `core.config`, a domain-logic unit test would require constructing a `Settings` object; keeping it dependency-free means `tests/unit/` never touches configuration, database, or network at all.

**Why `api` doesn't import `infrastructure` directly:** a route handler should never be able to `from infrastructure.db.repositories.identity import SqlUserRepository` and call it directly, because that bypasses the `application`-layer use case (and therefore bypasses authorization checks, audit hooks, and transaction boundaries that live in the use case). The only way `api` code touches a repository is through an `application` service that was itself injected via `Depends()`.

## Enforcement: import-linter

Layer rules are enforced mechanically in CI, not left to code review discipline alone. `pyproject.toml`:

```toml
[tool.importlinter]
root_package = "iam_platform"

[[tool.importlinter.contracts]]
name = "Layered architecture"
type = "layers"
layers = [
    "iam_platform.api",
    "iam_platform.workers",
    "iam_platform.infrastructure",
    "iam_platform.application",
    "iam_platform.core",
    "iam_platform.domain",
]

[[tool.importlinter.contracts]]
name = "Domain has no project-internal dependencies"
type = "forbidden"
source_modules = ["iam_platform.domain"]
forbidden_modules = [
    "iam_platform.core",
    "iam_platform.application",
    "iam_platform.infrastructure",
    "iam_platform.api",
    "iam_platform.workers",
]

[[tool.importlinter.contracts]]
name = "API does not import infrastructure directly"
type = "forbidden"
source_modules = ["iam_platform.api"]
forbidden_modules = ["iam_platform.infrastructure"]
```

`import-linter`'s `layers` contract type enforces strict one-directional imports (a "higher" layer may import a "lower" one, never the reverse) — this is exactly the clean-architecture dependency rule, checked automatically on every CI run rather than relying on reviewers to catch a stray import. A CI job fails the build on any violation, the same way a linter fails on a style violation.

## Ports and the repository/Unit-of-Work pattern

Repository **interfaces** (ports) are declared in `application/<context>/ports.py` as `typing.Protocol` classes, not ABCs — structural typing means an `infrastructure` implementation satisfies the port just by matching its method signatures, with no explicit inheritance coupling `infrastructure` back to `application` at the class-definition level (though the *module* dependency from infrastructure → application still exists and is intentional, per the matrix above, since infrastructure needs the Protocol type to type-check against).

```python
# application/tenancy/ports.py
class TenantMembershipRepository(Protocol):
    async def get_active_membership(self, tenant_id: UUID, user_id: UUID) -> TenantMembership | None: ...
    async def list_by_tenant(self, tenant_id: UUID, status: str | None = None) -> list[TenantMembership]: ...
    async def save(self, membership: TenantMembership) -> None: ...
```

The **Unit of Work** wraps one request's (or one job's) transaction boundary — it is not a generic abstraction layered over every possible query, only over the specific commit/rollback lifecycle:

```python
# infrastructure/db/unit_of_work.py
class SqlUnitOfWork:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> "SqlUnitOfWork":
        self.session = self._session_factory()
        await self.session.begin()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            await self.session.rollback()
        else:
            await self.session.commit()
        await self.session.close()
```

Repositories are **not** wrapped in a heavier generic base class (`class BaseRepository(Generic[T]): ...`) that every concrete repository inherits from — each repository implements exactly the methods its Protocol declares, using plain SQLAlchemy 2.0 `select()`/`insert()` statements directly. This is the explicit instruction from Phase 1 ("do not hide SQLAlchemy behind excessive generic abstractions") applied concretely: a repository method body is a few lines of real SQLAlchemy, not a call into three layers of generic query-building indirection.

## Dependency injection: FastAPI `Depends()`, no DI container

**Decision:** the DI mechanism is FastAPI's native `Depends()` system. No third-party DI container (`dependency-injector`, `punq`, etc.) is introduced.

- **Alternatives considered:** a dedicated DI container library; a manually-written service locator.
- **Why chosen:** FastAPI's `Depends()` already provides request-scoped construction, caching within a request, and — critically — `app.dependency_overrides` for test doubles, which covers everything a DI container would add. Introducing a second DI mechanism for workers (which don't have FastAPI's request cycle) would mean maintaining two different wiring styles for no real benefit.
- **Trade-off accepted:** workers need their own lightweight composition root (`workers/bootstrap.py`) since they have no `Depends()` machinery — this is a small amount of manual wiring code, not a framework.

```python
# api/deps/tenant_resolver.py
async def get_tenant_context(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    membership_repo: TenantMembershipRepository = Depends(get_membership_repository),
) -> TenantContext:
    ...
```

```python
# workers/bootstrap.py
def build_document_processor(settings: Settings) -> ProcessDocumentUpload:
    session_factory = build_session_factory(settings.database)
    uow_factory = lambda: SqlUnitOfWork(session_factory)
    doc_repo = SqlDocumentRepository
    vector_client = VectorStoreClient(settings.vector_store)
    return ProcessDocumentUpload(uow_factory, doc_repo, vector_client)
```

Both are explicit, readable Python — no magic reflection-based autowiring, matching the instruction to avoid unnecessary abstraction.

## Request-scoped context: contextvars, used narrowly

**Decision:** a single `contextvars.ContextVar[RequestContext]` in `core/context.py`, populated by middleware and enriched by the `api/deps/*` chain, holds cross-cutting fields (correlation ID, request ID, IP, user agent) for **logging and tracing only**.

```python
# core/context.py
@dataclass(frozen=True)
class RequestContext:
    request_id: UUID
    correlation_id: UUID
    ip: str | None = None
    user_agent: str | None = None

_current: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)

def bind(ctx: RequestContext) -> Token: ...
def current() -> RequestContext | None: ...
```

**Explicit rule — this is not where authorization-relevant data lives.** `TenantContext`, the authenticated user, and the resolved `EffectivePermissionSet` are **not** stored in the contextvar; they are constructed by `api/deps/*` and passed as **explicit function arguments** into application-layer use cases. Two reasons:

1. **Testability:** an application service that takes `TenantContext` as a parameter can be unit-tested by constructing one directly, with no need to simulate the contextvar machinery or an active request.
2. **No hidden coupling:** a reviewer reading a use case's signature sees exactly what authorization state it depends on, rather than having to know it silently reaches into a global for `current_tenant()`.

This satisfies "no global mutable state" (Phase 2 / [04-architecture-overview.md](04-architecture-overview.md)) — a `ContextVar` is not global mutable state in the harmful sense (it's per-async-task, immutable-dataclass-valued, and used only for observability plumbing), but authorization decisions never depend on it implicitly.

## Composition roots

Two places construct the full dependency graph, and only two:

- **`api/main.py`** — `create_app(settings: Settings) -> FastAPI`, registers routers, sets up middleware, and wires the default (non-test) implementations into `app.dependency_overrides`-compatible provider functions.
- **`workers/main.py`** — reads `Settings`, calls `workers/bootstrap.py` functions to build each job handler, and starts the queue consumer loop.

No other module is allowed to construct a repository, a `UnitOfWork`, or a settings object from scratch — everything downstream receives its dependencies through a parameter, which is what makes the import-linter contracts above sufficient rather than merely aspirational.
