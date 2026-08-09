-- Dev-environment role setup, mirroring the production role split in
-- docs/18-schema-rls-and-migrations.md: migrations run as a superuser-like
-- owner role; the application connects as a genuinely restricted,
-- non-superuser role so Row-Level Security actually applies to it.
--
-- (`postgres`, the bootstrap superuser created from POSTGRES_USER/
-- POSTGRES_PASSWORD, plays the `app_migrator` role for local dev instead of
-- a separate identically-privileged account -- no meaningful difference in
-- a single-developer Postgres container, and it keeps this script short.)

CREATE ROLE app_tenant LOGIN PASSWORD 'dev_only_password' NOSUPERUSER NOBYPASSRLS;
GRANT CONNECT ON DATABASE iam_platform TO app_tenant;
GRANT USAGE ON SCHEMA public TO app_tenant;

-- Tables/sequences created later by migrations (running as `postgres`)
-- automatically grant these rights to app_tenant -- no per-table GRANT
-- needed in the migration itself.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_tenant;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_tenant;

-- app_platform: the Platform Service Layer's BYPASSRLS role
-- (docs/18-schema-rls-and-migrations.md). Used only by the small, reviewed
-- set of platform-scope application code, never the default connection.
CREATE ROLE app_platform LOGIN PASSWORD 'dev_only_password' NOSUPERUSER BYPASSRLS;
GRANT CONNECT ON DATABASE iam_platform TO app_platform;
GRANT USAGE ON SCHEMA public TO app_platform;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_platform;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_platform;
