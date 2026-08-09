# Database Schema — Conventions

These conventions apply to every table in [11-schema-global-identity.md](11-schema-global-identity.md) through [17-schema-security-audit.md](17-schema-security-audit.md). They are decided once here rather than repeated per table.

## Primary keys: UUIDv7

**Decision:** every table's `id` is `UUID PRIMARY KEY DEFAULT uuidv7()` (PostgreSQL 18 native function; on PostgreSQL 17 use the `pg_uuidv7` extension, or generate UUIDv7 in the application layer as a fallback).

- **Alternatives considered:** UUIDv4 (fully random), bigint identity columns.
- **Why chosen:** UUIDv7 is time-ordered, which keeps B-tree index locality good at insert-heavy, millions-of-rows scale (avoids the random-insert index fragmentation UUIDv4 causes). Bigint identities are even more index-friendly but leak row counts/creation order through sequential IDs and don't work well for client-generated IDs or merge/replication scenarios across a distributed future.
- **IDOR consideration:** UUIDv7 is *not* the IDOR defense — tenant-scoped queries, RLS, and generic-404 responses are (see [03-threat-model.md](03-threat-model.md) scenario 2). A time-ordered ID reveals relative creation order but not enough entropy to be guessable (74 random bits), and it's never treated as a secret.
- **Trade-off accepted:** slight information leak of creation-time ordering via ID comparison, judged acceptable since it's not a security boundary.

## Timestamps

Every table has `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`. Mutable tables also have `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.

**Implementation note (Phase 5):** `updated_at` is maintained via SQLAlchemy's `onupdate=func.now()` (see `infrastructure/db/base.py`'s `TimestampMixin`) rather than a database `BEFORE UPDATE` trigger as originally sketched here — equivalent for this codebase since every write goes through the ORM, and it avoids a stored procedure in the migration for no benefit given that constraint. If a future phase ever writes to these tables outside the ORM (a raw-SQL maintenance job, for instance), revisit this and add the trigger.

**Pitfall confirmed the hard way (Phase 5 integration testing):** `server_default` must be passed `func.now()` (or `sa.text("now()")`), never a bare Python string `"now()"`. SQLAlchemy accepts a plain string for `server_default` too, but emits it as `DEFAULT 'now()'` — a **quoted string literal**, which PostgreSQL evaluates **once**, at the moment the column's default is established (i.e., migration time), not per-row. Every row would silently get the same frozen timestamp from when the table was created. This is invisible in unit tests (fakes don't touch real DDL) and easy to miss in code review (`"now()"` and `func.now()` look almost identical); it only surfaces as a real bug — e.g., login-attempt rate limiting silently never triggering because every `login_attempts.occurred_at` was stuck in the past — once tested against a live database. Always use `func.now()`.

## Soft delete policy

Hard delete is the default. Soft delete (`deleted_at TIMESTAMPTZ NULL`) is used **only** where a deletion has downstream legal/audit/right-to-erasure implications: `users`, `tenants`, `documents`. Everywhere else, a hard `DELETE` (with `ON DELETE CASCADE`/`RESTRICT` as specified per table) is correct, because the *fact* that something existed and was deleted is preserved separately in `audit_logs`, not by keeping a tombstone row in the operational table.

Where soft delete is used, an asynchronous anonymization/purge job (Phase 9 concern) enforces the actual retention window — `deleted_at` alone is a grace-period marker, not the end state.

## Composite foreign keys for tenant consistency

Every tenant-owned child table that references another tenant-owned table denormalizes `tenant_id` onto itself and declares a **composite foreign key** against `(tenant_id, id)` on the parent, e.g.:

```sql
-- parent table carries a matching composite unique/PK target
ALTER TABLE knowledge_bases ADD CONSTRAINT uq_kb_tenant_id UNIQUE (tenant_id, id);

-- child table's FK can only point at a row with the SAME tenant_id
ALTER TABLE documents
  ADD CONSTRAINT fk_documents_kb
  FOREIGN KEY (tenant_id, knowledge_base_id) REFERENCES knowledge_bases (tenant_id, id);
```

This makes it a **schema-level impossibility** for `documents.tenant_id` to disagree with its `knowledge_base_id`'s real tenant — the exact mechanism Phase 1 (§6/§7) calls for, independent of application code correctness. This pattern is applied wherever a table references another tenant-owned table (documented per-table in the domain docs as "Composite FK").

Tables that can be referenced by a system-wide (`tenant_id IS NULL`) row *or* a tenant-specific row — `tenant_roles`, `model_configurations` — are called out individually since the composite-FK pattern needs a nullable-aware variant there (documented in [14-schema-tenant-authorization.md](14-schema-tenant-authorization.md)).

## Enums via CHECK constraints, not PostgreSQL ENUM types

Status/kind columns use `TEXT NOT NULL CHECK (col IN (...))` rather than native `CREATE TYPE ... AS ENUM`. Native enums are cheaper to store but expensive to alter (`ALTER TYPE ... ADD VALUE` can't run inside a transaction in older PostgreSQL and complicates zero-downtime deploys); a CHECK constraint is a trivial `ALTER TABLE` migration. At this scale the storage difference is negligible.

## RLS policy template

Every tenant-owned table gets, at minimum:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

`current_setting(..., true)` (missing_ok=true) returns NULL rather than raising when unset, which combined with `tenant_id = NULL` evaluating to unknown/false means **no tenant context set = no rows visible**, i.e. fails closed. Full policy SQL, the `app_tenant`/`app_platform` role definitions, and the `SET LOCAL` transaction pattern are in [18-schema-rls-and-migrations.md](18-schema-rls-and-migrations.md).

## Naming conventions

- Tables: `snake_case`, plural.
- Columns: `snake_case`.
- Foreign key columns: `<singular_table>_id` (e.g. `tenant_id`, `membership_id`).
- Junction/link tables: `<parent>_<child>` (e.g. `tenant_role_permissions`).
- Indexes: `ix_<table>_<columns>`; unique constraints: `uq_<table>_<columns>`; foreign keys: `fk_<table>_<referenced>`; checks: `ck_<table>_<column>`.

## Per-table documentation format

Each table in the domain docs is documented with: purpose, a column table (name/type/constraints), primary/foreign keys, unique/check constraints, indexes, tenant-scoping rule, deletion behavior, audit requirements, and retention considerations — matching the Phase 1 requirement.
