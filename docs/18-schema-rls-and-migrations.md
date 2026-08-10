# Row-Level Security & Migration Strategy

Concrete implementation of the strategy summarized in [07-tenant-isolation-and-rls.md](07-tenant-isolation-and-rls.md). Actual Alembic migration files are written in Phase 3's implementation pass / Phase 5 onward — this document is the specification they must satisfy.

## Database roles

| Role | Used by | RLS behavior | Grants |
|---|---|---|---|
| `app_migrator` | Alembic, run at deploy time only | N/A — DDL owner | `CREATEDB`-free superuser-adjacent owner role; owns all tables; the only role permitted to run DDL |
| `app_tenant` | API pods and worker pods for all normal tenant/platform-adjacent request handling | RLS **enforced** (`FORCE ROW LEVEL SECURITY`) | `SELECT, INSERT, UPDATE, DELETE` on tenant-owned tables per policy below; `SELECT, INSERT` only on `audit_logs`/`security_events` (no `UPDATE`/`DELETE` — see [17-schema-security-audit.md](17-schema-security-audit.md)); no grants on tables it has no business touching |
| `app_platform` | Platform Service Layer only ([04-architecture-overview.md](04-architecture-overview.md)) | `BYPASSRLS` | Broader cross-tenant `SELECT`, narrowly scoped `INSERT/UPDATE` for platform operations (tenant suspension, impersonation session creation, etc.); every statement executed under this role is wrapped in an application-level audit call |

Application code and worker code **only ever** connect as `app_tenant`. `app_platform` is used exclusively inside the small, reviewed set of platform service classes — it is never the default connection for a route handler.

## Per-request/per-job context

Immediately after opening a transaction:

```sql
BEGIN;
SELECT set_config('app.tenant_id', '3fa85f64-5717-4562-b3fc-2c963f66afa6', true);
SELECT set_config('app.user_id', '9c858901-8a57-4791-81fe-4c455b099bc9', true);
-- ... queries ...
COMMIT;  -- or ROLLBACK
```

`set_config(name, value, is_local => true)` is the parameterized equivalent of `SET LOCAL name = value` — used instead of literal `SET LOCAL app.tenant_id = '...'` string interpolation because it's a normal function call, so it accepts a real bind parameter through the driver rather than requiring the application to format a value into SQL text itself. Both forms are transaction-scoped and automatically undone at `COMMIT`/`ROLLBACK`.

> **Pitfall confirmed the hard way (Phase 6 implementation, verified empirically against a live pooled connection):** a *reused* pooled connection does **not** report `current_setting('app.tenant_id', true)` as `NULL` on a transaction that never sets it, if some *earlier* transaction on that same physical connection ever did. PostgreSQL "remembers" that the custom GUC now exists at the session level, and it reverts to `''` (empty string), not back to undefined, once the `LOCAL` scope that set it ends. A naive policy that casts directly — `current_setting('app.tenant_id', true)::uuid` — works perfectly on a fresh connection and then **raises `invalid input syntax for type uuid: ""`** on the very next transaction that reuses that connection without re-setting the value. This is invisible in any test that opens exactly one transaction per connection (which is the common case in simple test setups) and only surfaces under real pooled reuse — exactly the kind of thing that must be tested against a real engine with a small pool, not assumed. The fix is `NULLIF(current_setting('app.tenant_id', true), '')::uuid`, which normalizes the stale empty string back to `NULL` before casting. Every policy below uses this form; the plain-cast form from the original Phase 3 draft of this document was wrong and has been corrected here.

For requests/jobs with no tenant context (platform-scope routes, pre-tenant-resolution auth endpoints), `app.tenant_id` is simply never set for that transaction; the RLS policies below fail closed in that case (see next section).

## RLS policy template (applied per tenant-owned table)

```sql
ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_memberships FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tenant_memberships
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```

- `current_setting('app.tenant_id', true)` with `missing_ok = true` returns `NULL` instead of raising when never set on this connection at all; `NULLIF(..., '')` additionally normalizes the "previously set on this pooled connection, not re-set this transaction" case (see the pitfall above) to `NULL` as well, so both a fresh and a reused connection behave identically.
- `tenant_id = NULL` evaluates to `UNKNOWN`, which `USING`/`WITH CHECK` treat as **false** — no tenant context set means **zero rows visible and zero rows writable**. This is the fail-closed guarantee: a code path that forgets to call the Tenant Resolver simply sees nothing, rather than accidentally seeing everything (or raising a confusing cast error).
- `FORCE ROW LEVEL SECURITY` ensures the policy applies even to the table owner (`app_migrator`) when connected in a non-DDL capacity — RLS is normally bypassed for table owners by default in PostgreSQL, and `FORCE` closes that gap.
- One table in the actual Phase 6 schema (`tenant_memberships`) extends this template with a **self-lookup exception** on the read side only: `USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)`, `WITH CHECK` unchanged (write still requires the active-tenant match). This is what lets "which tenants am I a member of?" work as a normal RLS-subject query *before* any tenant has been resolved — otherwise resolving the very first tenant to activate would need a chicken-and-egg bypass. See `application/tenancy/list_memberships.py` and `docs/07-tenant-isolation-and-rls.md` §2.

## System-catalog tables (nullable tenant ownership)

`tenant_roles` needs "visible to everyone, writable by no tenant" for its `tenant_id IS NULL` (system role) rows. Same nullable-tenant pattern applies to `tenant_role_permissions.tenant_id` and `role_hierarchy.tenant_id`, both denormalized onto those tables specifically so this template applies directly instead of a slower subquery-based policy (see docs/14-schema-tenant-authorization.md's Phase 6 amendment note in `infrastructure/db/models/tenant_authz.py`):

```sql
CREATE POLICY tenant_roles_read ON tenant_roles
  FOR SELECT
  USING (tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY tenant_roles_write ON tenant_roles
  FOR INSERT, UPDATE, DELETE
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```

Splitting `SELECT` from the write policies is what allows the `tenant_id IS NULL` system rows to be readable tenant-wide while remaining un-writable by `app_tenant` entirely (system rows are only ever inserted via `app_migrator`-run seed migrations).

## Platform-only tables

`platform_roles`, `platform_permissions`, `platform_role_permissions`, `platform_user_roles` have RLS **enabled** but with no tenant-isolation policy at all — instead a single policy denies `app_tenant` entirely:

```sql
ALTER TABLE platform_user_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_user_roles FORCE ROW LEVEL SECURITY;
-- no policy created for app_tenant => default-deny (RLS with zero matching policies means zero visible rows)
GRANT ALL ON platform_user_roles TO app_platform;  -- app_platform has BYPASSRLS anyway; grant is the real gate
REVOKE ALL ON platform_user_roles FROM app_tenant;
```

`app_tenant` gets no `GRANT` on these tables at all — belt-and-suspenders alongside RLS, since a `GRANT`-level block is the stronger of the two barriers.

## Tightening a foreign key breaks every hand-built test fixture

Adding a constraint is not only a data-migration question. `tenant_model_configurations` repointed `ai_assistants`' composite FK at an entitlement table, and **70 assertions across `tests/integration/db/test_ai_resources_rls.py` failed at once** — not because tenant isolation regressed, but because that suite builds its object graph with raw SQL (`user → tenant → membership → model_configuration → assistant`) and the graph now needs one more edge.

That is the correct failure. A fixture that hand-assembles rows encodes the schema's shape, so a stronger invariant *should* invalidate it, and the fix is to build the missing row rather than to relax the constraint.

Two things this is worth remembering for:

1. **Budget for it.** The application code, the use cases and the unit tests can all be complete and green while the integration fixtures still describe the old schema. The full suite is where that surfaces, ~27 minutes in.
2. **The migration and the fixtures are separate problems.** The migration backfills *existing production rows*; fixtures create *new* rows from scratch and get no such help. Both need doing, and doing one does not hint that the other is outstanding.

Same family as the composite-FK/UNIQUE lesson below, which this codebase has now hit three times: a constraint that is right for production is also a constraint every test seeder has to satisfy.

## A rollback pitfall every Unit of Work implementation must avoid

Discovered during Phase 5 integration testing against a real Postgres instance (invisible to unit tests running against the in-memory fakes, which have no real transaction/rollback semantics): a Unit of Work's `__aexit__` rolls back the transaction on **any** exception raised from inside its `async with` block. That's correct and desired for genuine failures, but it is a trap for use cases where the *expected, successful outcome of detecting a problem* is itself a database write that must survive — for example:

- Refresh-token reuse detection ([05-authentication-flows.md](05-authentication-flows.md)): the whole point of detecting reuse is to revoke the token family and log a security event, then tell the caller "reuse detected." If the code naively does the revocation writes and then `raise RefreshReuseDetectedError` *from inside* the `async with uow:` block, `__aexit__` rolls the revocation back — the caller still gets the exception, but the attacker's stolen token family was never actually revoked in the database.
- Login lockout: recording a failed attempt and creating the resulting `account_lockouts` row, then raising `InvalidCredentialsError` from inside the block, silently discards the lockout.

**The fix, applied throughout `application/identity/*.py`:** never raise from inside the `async with uow_factory() as uow:` block once that block has performed a write that must survive the exception. Instead, set a local flag (or hold the result value) and let the block exit **normally** — reaching the end of the `async with` body without a raised exception is what makes `__aexit__` commit — then raise afterward, outside the block, based on the flag. See `refresh_session.py::RefreshSession.execute`, `login_user.py::LoginUser.execute`, and `mfa.py::VerifyMfaChallenge.execute` for the pattern.

This applies to every future module with a Unit of Work (tenant management, tenant authorization, etc.) — any use case that writes an audit/security record or other side effect on an error path needs the same "exit normally, raise after" structure, not "write then raise." It's also why the in-memory fakes used for unit tests (`tests/unit/*/fakes.py`) deliberately snapshot each repository on `__aenter__` and restore it on exception, rather than being a no-op on `__aexit__` — a fake with no rollback semantics can't catch this class of bug, so the fakes now mirror the real transactional behavior closely enough to catch it too.

## Worker context

Workers follow the identical pattern per job, not per worker-process lifetime:

```python
async def process_job(job: Job) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": job.verified_tenant_id})
            await session.execute(text("SET LOCAL app.user_id = :uid"), {"uid": job.verified_actor_id})
            await handle(job, session)
        # transaction commits/rolls back here; SET LOCAL values are gone before the connection is reused
```

`job.verified_tenant_id`/`verified_actor_id` are re-validated against the database (tenant still active, membership still valid) at the start of `handle()`, not merely trusted from the enqueue-time payload — matching the Phase 1 §6/§8 requirement that background jobs re-check authorization at execution time.

## Migrations and maintenance

- Alembic runs as `app_migrator`, which owns every table and is exempt from `FORCE ROW LEVEL SECURITY` only in the sense that DDL statements aren't subject to row policies at all (RLS governs DML, not `ALTER TABLE`/`CREATE INDEX`).
- Maintenance jobs (partition creation for `audit_logs`, expired-token purges, anonymization) run as dedicated roles scoped to exactly the tables they need, separate from both `app_tenant` and `app_migrator` — a purge job does not need table-owner privileges.
- No migration ever runs as `app_tenant` or `app_platform`.

## Proving RLS actually works (test strategy, executed in Phase 8)

1. **Direct-SQL leak test:** connect as `app_tenant`, `SET LOCAL app.tenant_id` to Tenant A, attempt `SELECT * FROM tenant_memberships` with a `WHERE tenant_id = '<Tenant B>'` clause deliberately included in the test query (i.e. the application-layer filter is present but RLS is the thing actually being exercised) — assert zero rows, proving RLS isn't merely redundant with the app filter but independently sufficient.
2. **No-context test:** open a transaction, skip `SET LOCAL` entirely, assert every tenant-owned table returns zero rows (fail-closed proof).
3. **Cross-tenant write test:** attempt an `UPDATE`/`INSERT` with a `tenant_id` mismatching the session's `app.tenant_id` and assert it's rejected by `WITH CHECK`, not just silently filtered.
4. **Pool-reuse test:** run two sequential transactions on the *same physical connection* (bypassing the pool's usual connection rotation to force reuse) with different `app.tenant_id` values, and assert the second transaction sees no residual state from the first — proving the `SET LOCAL` reset guarantee holds under connection pooling.
5. **Platform-bypass scope test:** assert `app_platform` can read across tenants, and assert `app_tenant` cannot access `platform_*` tables under any `SET LOCAL` value.

These become the concrete pytest cases backing the mandatory cross-tenant tests listed in [03-threat-model.md](03-threat-model.md).
