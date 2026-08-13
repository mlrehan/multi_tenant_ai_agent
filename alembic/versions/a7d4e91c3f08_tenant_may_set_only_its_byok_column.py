"""Let a tenant set its own BYOK column -- and nothing else on that row.

`tenant_model_configurations` had a SELECT-only policy, which was right while
the platform was the only writer: a grant is something done *to* a tenant. The
BYOK column changes that, because one column on the row is now genuinely the
tenant's own decision. Without this migration the update matches zero rows and
the API answers 404 for a model the tenant can plainly see -- found by running
the real endpoint, not by reading the code.

**Two independent mechanisms, because widening either one alone would be a
privilege escalation.**

1. **A column privilege says *what*.** `ALTER DEFAULT PRIVILEGES` in
   `docker/postgres-init/01-roles.sql` granted `app_tenant` INSERT/UPDATE/DELETE
   on every column of every table created since -- including `tenant_id` and
   `model_configuration_id` here. With an UPDATE policy added and nothing else
   done, a tenant could repoint their grant at a model they were never granted,
   or at another tenant. So the blanket write grants are revoked and exactly one
   column is granted back. RLS cannot express "only this column"; only a column
   privilege can.
2. **An RLS policy says *which row*.** `USING` confines which rows are visible
   to update, `WITH CHECK` confines what they may become -- both required, since
   `USING` alone would permit rewriting a row *into* another tenant.

INSERT and DELETE stay revoked: creating or removing an entitlement is the
platform's authority, and a tenant granting themselves a model is the exact
escalation the entitlement table exists to prevent. `app_platform` (BYPASSRLS)
keeps full write access and is unaffected -- it is what performs grants.

Same lesson as migration `937a69c41b65`, which found `app_tenant` holding
UPDATE and DELETE on `audit_logs` for the same reason: default privileges are
granted once and never narrowed unless a migration does it deliberately.

Revision ID: a7d4e91c3f08
Revises: f3c8a1d5be62
"""

from __future__ import annotations

from alembic import op

revision = "a7d4e91c3f08"
down_revision = "f3c8a1d5be62"
branch_labels = None
depends_on = None

_TENANT_MATCHES = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)


def upgrade() -> None:
    # 1. Take back the blanket write access default privileges handed out.
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON tenant_model_configurations FROM app_tenant"
    )
    # 2. Give back exactly the one column a tenant owns.
    op.execute(
        "GRANT UPDATE (provider_credential_id) "
        "ON tenant_model_configurations TO app_tenant"
    )
    # 3. And confine it to their own row, in both directions.
    op.execute(
        f"""
        CREATE POLICY tenant_model_configurations_set_credential
        ON tenant_model_configurations
        FOR UPDATE
        USING ({_TENANT_MATCHES})
        WITH CHECK ({_TENANT_MATCHES})
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_model_configurations_set_credential "
        "ON tenant_model_configurations"
    )
    op.execute(
        "REVOKE UPDATE (provider_credential_id) "
        "ON tenant_model_configurations FROM app_tenant"
    )
    op.execute(
        "GRANT INSERT, UPDATE, DELETE ON tenant_model_configurations TO app_tenant"
    )
