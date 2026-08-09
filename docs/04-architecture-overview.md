# System Architecture

```mermaid
graph TB
    subgraph Clients
        WEB[Web SPA]
        MOB[Mobile]
        SVC[Server-to-server / API keys]
    end

    subgraph Edge
        LB[Load Balancer / TLS termination / WAF]
    end

    subgraph "API Layer (FastAPI, stateless, N pods)"
        MW[Middleware: correlation-id, security headers, rate limit]
        AUTHN[AuthN dependency: verify JWT / API key]
        TENANT[Tenant Resolver dependency:<br/>domain/header/path → verified membership]
        AUTHZ[AuthZ dependency: effective permission check]
        ROUTES[Versioned routers:<br/>/auth /tenants /memberships /platform /rbac /assistants /audit /impersonation]
    end

    subgraph "Application / Domain"
        SVCS[Application services<br/>use-case orchestration]
        DOM[Domain layer<br/>entities, policy rules, invariants]
    end

    subgraph "Infrastructure"
        REPO[Repositories / Unit of Work]
        CACHE[Redis client<br/>tenant-namespaced]
        QUEUE[Job queue producer]
    end

    subgraph "Data"
        PG[(PostgreSQL<br/>RLS enforced)]
        RD[(Redis)]
    end

    subgraph "Workers (separate pods)"
        WCTX[Job context validator:<br/>re-verify tenant/actor/resource]
        WPROC[Processors: embedding, notifications,<br/>audit fanout, provisioning]
    end

    subgraph External
        OIDC[Google / Facebook / OIDC providers]
        VEC[Vector store]
        OBJ[Object storage]
        AIPROV[AI model providers]
    end

    WEB & MOB & SVC --> LB --> MW --> AUTHN --> TENANT --> AUTHZ --> ROUTES
    ROUTES --> SVCS --> DOM
    SVCS --> REPO --> PG
    SVCS --> CACHE --> RD
    SVCS --> QUEUE --> WPROC
    WPROC --> WCTX --> PG
    WPROC --> VEC
    WPROC --> OBJ
    WPROC --> AIPROV
    AUTHN <-.OIDC code exchange, validated.-> OIDC
```

## Module boundary rules (binding for Phase 4 folder structure)

- `api` depends on `application`.
- `application` depends on `domain`.
- `domain` depends on nothing (pure — entities, policy rules, invariants only).
- `infrastructure` implements ports defined by `domain`/`application` (repositories, Redis client, queue producer).
- `core` (config, security primitives, request-context) is depended on by everything but depends on nothing project-specific.
- `workers` depends on `application`/`infrastructure`, never on `api`.

Repositories and a light Unit-of-Work pattern are used where they provide real value for centralizing tenant-scoping enforcement — not as a heavy generic abstraction hiding SQLAlchemy 2.0's async session semantics. See the trade-off entry in [08-decisions-log.md](08-decisions-log.md).

## Request-scoped context

Every request carries a context object populated by the dependency chain (see [06-authorization-model.md](06-authorization-model.md)) holding: current user, session, active tenant, membership, platform and tenant roles, authentication method, request/correlation IDs, IP and user agent, impersonation context, and other security attributes. No global mutable state is used anywhere in the system.
