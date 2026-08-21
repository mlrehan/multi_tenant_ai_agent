#!/bin/bash
# Role setup, mirroring the production role split in
# docs/18-schema-rls-and-migrations.md: migrations run as a superuser-like
# owner role; the application connects as a genuinely restricted,
# non-superuser role so Row-Level Security actually applies to it.
#
# (`postgres`, the bootstrap superuser created from POSTGRES_USER/
# POSTGRES_PASSWORD, plays the `app_migrator` role for local dev instead of
# a separate identically-privileged account -- no meaningful difference in
# a single Postgres container, and it keeps this script short.)
#
# ---------------------------------------------------------------------------
# **This was a `.sql` file with `PASSWORD 'dev_only_password'` written into
# it, and that was a production defect, not just an untidy default.**
#
# Files in `/docker-entrypoint-initdb.d` ending in `.sql` are handed straight
# to psql, which performs no environment expansion -- so there was no way for
# that file to pick up the passwords compose already had. Two consequences,
# and the second is worse than the first:
#
#   1. Postgres created `app_tenant` and `app_platform` with a password the
#      application does not use, so every API and worker connection failed
#      with `password authentication failed`. The migration job survived only
#      because it connects as the superuser.
#   2. A production database ended up holding two login roles whose password
#      is published in this repository.
#
# A `.sh` file *is* executed (or sourced) by the entrypoint with the
# environment intact, which is why this is now a script.
#
# Passwords are passed as psql variables and interpolated with `:'name'`,
# never pasted into the SQL text. `:'name'` emits a correctly escaped string
# literal, so a password containing a quote or a backslash cannot end the
# literal and change the statement -- the same class of problem as the URL
# escaping in `core/config.py`, in a different syntax.
# ---------------------------------------------------------------------------
set -euo pipefail

: "${APP_TENANT_PASSWORD:?APP_TENANT_PASSWORD must be set for the postgres container}"
: "${APP_PLATFORM_PASSWORD:?APP_PLATFORM_PASSWORD must be set for the postgres container}"

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    -v db_name="$POSTGRES_DB" \
    -v tenant_password="$APP_TENANT_PASSWORD" \
    -v platform_password="$APP_PLATFORM_PASSWORD" <<-'EOSQL'
    CREATE ROLE app_tenant LOGIN PASSWORD :'tenant_password' NOSUPERUSER NOBYPASSRLS;
    GRANT CONNECT ON DATABASE :"db_name" TO app_tenant;
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
    CREATE ROLE app_platform LOGIN PASSWORD :'platform_password' NOSUPERUSER BYPASSRLS;
    GRANT CONNECT ON DATABASE :"db_name" TO app_platform;
    GRANT USAGE ON SCHEMA public TO app_platform;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_platform;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO app_platform;
EOSQL
