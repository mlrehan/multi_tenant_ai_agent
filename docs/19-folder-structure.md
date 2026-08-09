# Folder Structure

Package name below is `iam_platform` as a placeholder — trivially renamed at Phase 5 when the project is actually scaffolded. Layout uses the `src/` convention so the package can never be accidentally imported unqualified/uninstalled during development.

## Top-level layout

```
.
├── pyproject.toml
├── alembic.ini
├── .env.example
├── docker/
│   ├── api.Dockerfile
│   └── worker.Dockerfile
├── alembic/
│   ├── env.py
│   └── versions/
├── scripts/
│   ├── seed_system_roles.py        # platform_roles/tenant_roles/permissions catalog seed
│   └── create_dev_tenant.py
├── src/
│   └── iam_platform/
│       ├── api/
│       ├── application/
│       ├── domain/
│       ├── infrastructure/
│       ├── core/
│       └── workers/
└── tests/
    ├── unit/
    ├── integration/
    ├── api/
    ├── security/
    └── conftest.py
```

Each of `api/application/domain/infrastructure` is internally organized **by bounded context**, using the same seven domains as the schema docs ([13](13-schema-tenant-management.md)–[17](17-schema-security-audit.md)) plus `identity` for the global-identity tables in [11-schema-global-identity.md](11-schema-global-identity.md):

| Bounded-context module | Corresponds to schema domain |
|---|---|
| `identity` | [11-schema-global-identity.md](11-schema-global-identity.md) |
| `platform_authz` | [12-schema-platform-authorization.md](12-schema-platform-authorization.md) |
| `tenancy` | [13-schema-tenant-management.md](13-schema-tenant-management.md) |
| `tenant_authz` | [14-schema-tenant-authorization.md](14-schema-tenant-authorization.md) |
| `organization` | [15-schema-organization.md](15-schema-organization.md) |
| `ai_resources` | [16-schema-ai-resources.md](16-schema-ai-resources.md) |
| `audit` | [17-schema-security-audit.md](17-schema-security-audit.md) (also covers `security_events`, `login_attempts`, `account_lockouts`, `policy_decisions`) |
| `impersonation` | `impersonation_sessions` (split out from `audit` because it has its own API surface and workflow, not just log rows) |

This keeps the top-level split matching the explicit `api/application/domain/infrastructure/core/workers/tests` requirement, while the *internal* organization stays navigable by feature rather than becoming eight flat folders of same-named files.

## `domain/`

Pure business logic — entities, value objects, domain-level policy rules (e.g. "a role's rank must exceed all its children's ranks"), domain exceptions. No FastAPI, no SQLAlchemy, no Redis import anywhere under this tree.

```
domain/
├── shared/
│   ├── entity.py            # base Entity/AggregateRoot, DomainEvent base
│   ├── value_objects.py     # Email, TenantSlug, PermissionCode, etc.
│   └── exceptions.py        # DomainError and subclasses
├── identity/
│   ├── entities.py          # User, Session, RefreshToken, MfaMethod...
│   └── policies.py          # password policy, lockout policy
├── platform_authz/
│   ├── entities.py          # PlatformRole, PlatformPermission
│   └── policies.py          # self-escalation guard, rank comparison
├── tenancy/
│   ├── entities.py          # Tenant, TenantMembership, TenantInvitation
│   └── policies.py          # membership status transitions
├── tenant_authz/
│   ├── entities.py          # TenantRole, TenantPermission, RoleHierarchy, AuthorizationOverride
│   └── policies.py          # hierarchy depth/cycle rules, effective-permission conflict resolution (pure function)
├── organization/
│   └── entities.py          # Department, Team, Group
├── ai_resources/
│   └── entities.py          # AiAssistant, KnowledgeBase, Document, Conversation
└── audit/
    └── entities.py          # AuditLogEntry, SecurityEvent (value objects, immutable)
```

The effective-permission **algorithm** from [06-authorization-model.md](06-authorization-model.md) lives in `domain/tenant_authz/policies.py` (and `domain/platform_authz/policies.py` for the platform variant) as a pure function operating on already-loaded role/permission/override data — it does not query the database itself. Loading that data is an `application` concern.

## `application/`

Use-case orchestration: one module per bounded context, one file (or small set of files) per use case. Defines the **ports** (repository/service interfaces) that `infrastructure` implements, as `typing.Protocol` classes — not ABCs, to keep infrastructure implementations decoupled without needing explicit inheritance.

```
application/
├── identity/
│   ├── ports.py             # UserRepository, SessionRepository, RefreshTokenRepository (Protocols)
│   ├── register_user.py
│   ├── login_user.py
│   ├── refresh_session.py
│   ├── link_oauth_account.py
│   └── ...
├── platform_authz/
│   ├── ports.py
│   ├── grant_platform_role.py
│   └── resolve_platform_permissions.py
├── tenancy/
│   ├── ports.py
│   ├── create_tenant.py
│   ├── invite_member.py
│   ├── accept_invitation.py
│   └── resolve_active_tenant.py     # implements the Phase 2 §2 algorithm
├── tenant_authz/
│   ├── ports.py
│   ├── assign_membership_role.py
│   ├── resolve_effective_permissions.py   # orchestrates: load data -> domain policy -> cache
│   └── create_custom_role.py
├── organization/
│   └── ...
├── ai_resources/
│   ├── ports.py
│   ├── create_assistant.py
│   ├── upload_document.py           # generates storage_path/vector_namespace server-side
│   └── query_knowledge_base.py      # always injects tenant+kb filter server-side
├── audit/
│   ├── ports.py                     # AuditWriter Protocol
│   └── record_audit_event.py
└── impersonation/
    ├── start_impersonation.py
    └── end_impersonation.py
```

## `infrastructure/`

Implements the `application`-layer ports against real systems. Organized first by *technical concern*, then by bounded context within `db/`.

```
infrastructure/
├── db/
│   ├── session.py                   # AsyncSession factory, session-per-request helper
│   ├── unit_of_work.py              # UnitOfWork context manager (begin/commit/rollback)
│   ├── rls.py                       # apply_tenant_context(session, tenant_id, user_id) -> SET LOCAL
│   ├── models/                      # SQLAlchemy 2.0 declarative models, mirrors domain/ modules
│   │   ├── identity.py
│   │   ├── platform_authz.py
│   │   ├── tenancy.py
│   │   ├── tenant_authz.py
│   │   ├── organization.py
│   │   ├── ai_resources.py
│   │   └── audit.py
│   └── repositories/                # implements application/*/ports.py
│       ├── identity.py
│       ├── platform_authz.py
│       ├── tenancy.py
│       ├── tenant_authz.py
│       ├── organization.py
│       ├── ai_resources.py
│       └── audit.py
├── cache/
│   ├── redis_client.py
│   └── permission_cache.py          # implements the caching strategy in 06-authorization-model.md
├── queue/
│   ├── producer.py
│   └── job_payloads.py              # typed job payload schemas (Pydantic)
├── security/
│   ├── password_hasher.py           # Argon2id wrapper (infra because it may call out to a KMS-backed pepper)
│   ├── jwt_service.py               # sign/verify access tokens
│   └── encryption.py                # envelope encryption via KMS client, used by provider_credentials/oauth tokens
├── oauth/
│   ├── base.py                      # OAuthProvider Protocol (start/callback/verify)
│   ├── google.py
│   └── facebook.py
├── storage/
│   └── object_storage_client.py     # tenant-scoped path generation lives here, per 16-schema-ai-resources.md
├── vector/
│   └── vector_store_client.py       # tenant-scoped namespace generation
├── email/
│   └── email_sender.py
└── secrets/
    ├── base.py                      # SecretProvider Protocol
    ├── env_provider.py
    ├── aws_secrets_manager.py
    ├── vault_provider.py
    └── azure_key_vault.py
```

## `core/`

Framework-agnostic, project-specific-dependency-free primitives. Everything else depends on `core`; `core` depends on nothing else in this project.

```
core/
├── config.py                        # typed Settings (see 21-configuration-and-secrets.md)
├── context.py                       # RequestContext dataclass + contextvars.ContextVar
├── logging.py                       # structured logging setup
├── tracing.py                       # OpenTelemetry setup
├── correlation.py                   # correlation/request ID generation
├── errors.py                        # AppError hierarchy shared across layers (not DomainError — see 20-dependency-rules.md)
└── clock.py                         # now() indirection for deterministic tests
```

## `api/`

FastAPI routers, request/response DTOs (Pydantic 2 models distinct from domain entities), and the dependency chain from [06-authorization-model.md](06-authorization-model.md).

```
api/
├── main.py                          # app factory: create_app(settings) -> FastAPI
├── deps/
│   ├── db.py                        # get_session dependency (session-per-request)
│   ├── authn.py                     # verify JWT -> AuthenticatedUser
│   ├── tenant_resolver.py           # implements 07-tenant-isolation-and-rls.md §2 algorithm
│   ├── permission_resolver.py       # implements 06-authorization-model.md algorithm
│   └── require_permission.py        # require_permission("tenant.assistants.publish") dependency factory
├── middleware/
│   ├── correlation_id.py
│   ├── security_headers.py
│   └── rate_limit.py
└── v1/
    ├── auth/
    │   ├── router.py                # /v1/auth/register, /login, /refresh, /logout, /oauth/{provider}/*
    │   └── schemas.py
    ├── tenants/
    │   ├── router.py                # /v1/tenants, /v1/tenants/{id}/domains
    │   └── schemas.py
    ├── memberships/
    │   ├── router.py                # /v1/tenants/{id}/memberships, /invitations
    │   └── schemas.py
    ├── platform/
    │   ├── router.py                # /v1/platform/tenants, /v1/platform/users, /v1/platform/roles
    │   └── schemas.py
    ├── rbac/
    │   ├── router.py                # /v1/tenants/{id}/roles, /v1/me/effective-permissions
    │   └── schemas.py
    ├── assistants/
    │   ├── router.py                # /v1/tenants/{id}/assistants, /knowledge-bases
    │   └── schemas.py
    ├── audit/
    │   ├── router.py                # /v1/tenants/{id}/audit, /v1/platform/audit
    │   └── schemas.py
    ├── impersonation/
    │   ├── router.py                # /v1/platform/impersonation/start, /end
    │   └── schemas.py
    └── system/
        └── router.py                 # /healthz, /readyz, /livez, /metrics — unauthenticated, no tenant resolution
```

Every versioned router is mounted under `/v1`; a future breaking change ships as `/v2` alongside `/v1` rather than an in-place change, per standard API-versioning practice — old versions are deprecated on a published timeline, not silently removed.

## `workers/`

Background job consumers — depends on `application` and `infrastructure`, never on `api`.

```
workers/
├── main.py                          # worker process entrypoint, composition root
├── bootstrap.py                     # constructs application services without FastAPI's DI
├── job_context.py                   # per-job SET LOCAL + re-validation wrapper (18-schema-rls-and-migrations.md)
└── jobs/
    ├── send_email.py
    ├── process_document_upload.py   # embedding pipeline
    ├── sync_data_source.py
    ├── purge_expired_tokens.py
    ├── anonymize_deleted_users.py
    └── audit_log_partition_maintenance.py
```

## `tests/`

Mirrors the domains, not the layers, at the top of each subfolder — a test for "tenant invitation acceptance" should be easy to find regardless of which layer it exercises.

```
tests/
├── conftest.py                      # shared fixtures: db session, redis, test client, factories
├── unit/                            # domain + application, no real DB/Redis (fakes/in-memory repos)
│   ├── identity/
│   ├── tenant_authz/
│   └── ...
├── integration/                     # real Postgres + Redis (via testcontainers), one bounded context per file
│   ├── db/
│   │   ├── test_rls_isolation.py    # the 5 RLS proof tests from 18-schema-rls-and-migrations.md
│   │   └── test_composite_fk_constraints.py
│   ├── cache/
│   └── workers/
├── api/                             # httpx AsyncClient against the full FastAPI app
│   ├── test_auth_flows.py
│   ├── test_tenant_resolution.py
│   └── ...
└── security/                        # the 12 cross-tenant/privilege-escalation scenarios from 03-threat-model.md
    ├── test_cross_tenant_isolation.py
    ├── test_privilege_escalation.py
    ├── test_token_replay.py
    └── test_impersonation_boundaries.py
```
