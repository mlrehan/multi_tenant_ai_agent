# Architectural Decisions Log

ADR-style record of key decisions. Update this file whenever a significant new decision is made or an existing one is revisited — do not silently change course without adding an entry here.

## ADR-001: Shared schema + tenant_id + RLS (not DB/schema-per-tenant)

- **Status:** Confirmed by user.
- **Alternatives considered:** database-per-tenant, schema-per-tenant.
- **Why chosen:** scales to thousands of tenants with normal ops tooling (single migration path, single connection pool, single backup strategy); RLS gives a DB-level backstop independent of application code correctness.
- **Trade-off accepted:** slightly more complex query/session-context plumbing (mandatory `SET LOCAL` per transaction); noisy-neighbor risk exists and is deferred to read replicas/partitioning later rather than solved on day one.
- **Future scalability impact:** if a specific tenant later requires dedicated isolation (e.g., a regulated enterprise customer), a dedicated-tier database-per-tenant path can be added without touching the shared-tenant data model, since `tenant_id` scoping is already the unit of isolation everywhere.
- Detail: [07-tenant-isolation-and-rls.md](07-tenant-isolation-and-rls.md)

## ADR-002: Tenant resolution — verified subdomain/custom domain primary, authenticated selection fallback

- **Status:** Confirmed by user.
- **Alternatives considered:** server-controlled header + authenticated selection as the sole mechanism; JWT-embedded tenant claim.
- **Why chosen:** best UX for a B2B SaaS (branded subdomains/custom domains), while still supporting mobile/API clients that can't route by domain via a validated fallback.
- **Trade-off accepted:** requires a domain verification subsystem (DNS TXT/CNAME challenge, detailed in Phase 3) before custom domains can resolve tenants.
- Detail: [07-tenant-isolation-and-rls.md](07-tenant-isolation-and-rls.md)

## ADR-003: Stateless short-lived JWT + stateful rotating refresh tokens

- **Status:** Confirmed by user (via A3).
- **Alternatives considered:** fully stateful sessions (DB-checked every request); fully stateless JWT with no refresh-side revocation.
- **Why chosen:** balances scalability (no DB hit for every access-token check) with real revocation (refresh layer + permission-version cache invalidation).
- **Trade-off accepted:** up to the access-token TTL, a just-suspended user can still call cached, unauthorized endpoints unless the permission-version bump forces a cache miss — mitigated by short TTL (10–15 min) and immediate version bump on suspension.
- Detail: [05-authentication-flows.md](05-authentication-flows.md)

## ADR-004: Active tenant resolved per-request, never a long-lived JWT claim

- **Status:** Confirmed by user (via A4).
- **Alternatives considered:** baking `tenant_id` into the JWT at login.
- **Why chosen:** a stale claim on a revoked membership is a live privilege-escalation vector.
- **Trade-off accepted:** one extra DB/cache lookup per request — mitigated by Redis cache keyed on permission version.
- Detail: [06-authorization-model.md](06-authorization-model.md), [07-tenant-isolation-and-rls.md](07-tenant-isolation-and-rls.md)

## ADR-005: Redis fails closed, never fails open

- **Status:** Confirmed by user (via A5).
- **Alternatives considered:** fail open to preserve uptime during Redis outages.
- **Why chosen:** availability of a wrong "allow" is a security incident; availability of a wrong "deny" is a support ticket.
- **Trade-off accepted:** Redis outage degrades throughput (DB fallback) rather than being invisible.
- Detail: [06-authorization-model.md](06-authorization-model.md)

## ADR-006: Repository + light Unit-of-Work, not a heavy generic ORM abstraction

- **Status:** Decided (design-time, not separately confirmed by user — follows explicit user instruction to avoid hiding SQLAlchemy 2.0 behind excessive indirection).
- **Alternatives considered:** raw SQLAlchemy everywhere with no repository layer; a heavy generic repository framework.
- **Why chosen:** keeps tenant-scoping enforcement centralized and testable without hiding SQLAlchemy 2.0's async session semantics behind excessive indirection.
- **Trade-off accepted:** some boilerplate per aggregate; accepted for auditability.
- Detail: [04-architecture-overview.md](04-architecture-overview.md)

## ADR-007: Impersonation as a distinct session/token type, not "login as"

- **Status:** Decided (derived from stated requirement to never silently replace the platform administrator's identity).
- **Alternatives considered:** directly issuing the target user's normal token.
- **Why chosen:** prevents impersonation from silently becoming indistinguishable, persistent access; keeps original platform identity visible everywhere via the `act` claim.
- **Trade-off accepted:** more moving parts (separate token claims, indicator, expiry).
- Detail: [06-authorization-model.md](06-authorization-model.md)

## Confirmation record

On 2026-08-03, the user was asked to confirm three groups of decisions before Phase 2 began:

1. Tenancy model → **shared schema + RLS for all tenants** (ADR-001).
2. Tenant resolution primary strategy → **verified subdomain/custom domain** (ADR-002).
3. Assumptions A5–A10 → **confirmed as-is, no adjustments**.

All three are binding for the remainder of the project unless the user explicitly revisits them.
