"""Each tenant's assistant answers under a sector profile the platform chooses.

`tenants.assistant_profile` is `nursery`, `education` or `general`. It selects
the sector half of the platform system policy and the shipped defaults a
tenant's chatbot starts from.

**Every existing tenant becomes `nursery`**, the server default, and the
nursery policy is byte-for-byte the prompt the platform sent before this
column existed -- so this migration changes no tenant's answers. Only a
platform administrator moving a tenant to another profile does.

On `tenants` rather than `tenant_chatbot_settings` because `tenants` is
already read-only to `app_tenant` (a SELECT-only RLS policy plus
`REVOKE INSERT, UPDATE, DELETE` in `c1178e6fb886`): the tenant can read which
profile it is on and cannot change it. Settings rows are tenant-writable.

Additive only: one NOT NULL column with a default, and a CHECK.

Revision ID: d4a7e2c9f1b6
Revises: c8e1f4a7b2d9
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d4a7e2c9f1b6"
down_revision = "c8e1f4a7b2d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "assistant_profile",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nursery'"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_tenants_assistant_profile_valid"),
        "tenants",
        "assistant_profile IN ('nursery','education','general')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tenants_assistant_profile_valid"), "tenants", type_="check")
    op.drop_column("tenants", "assistant_profile")
