"""Tenant entitlements for model configurations.

Separates *who owns a model configuration* from *which tenants may use it*.

Before this migration `ai_assistants` referenced
`model_configurations(tenant_id, id)`. Because `ai_assistants.tenant_id` is
NOT NULL, that composite FK could only ever match a configuration owned by the
same tenant -- so a platform-owned row (`tenant_id IS NULL`) was unreachable
by any assistant, and the product's "platform provides models to tenants"
rule was unimplementable. The console said so out loud, in a message that
explained a foreign key to a tenant administrator.

`tenant_model_configurations` carries the grant, and `ai_assistants` now
references *that*. The invariant gets stronger rather than weaker: the old FK
enforced "the configuration belongs to my tenant", the new one enforces "the
configuration has been granted to my tenant", which is the actual rule and
which platform-owned configurations can satisfy.

**Nothing is dropped and nothing is rewritten.** `model_configurations.tenant_id`
stays (it is the only ownership marker, and tenant-owned rows are in use), all
four of its RLS policies stay, and every existing assistant keeps its
configuration because the backfill grants exactly the pairs already in use
before the new constraint is applied.

Revision ID: e2b9f4c07a13
Revises: d1a4c73e59b8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e2b9f4c07a13"
down_revision = "d1a4c73e59b8"
branch_labels = None
depends_on = None

#: Same NULLIF guard as every other policy in this schema -- a pooled
#: connection that previously set app.tenant_id returns '' rather than NULL in
#: a later transaction that never set it, and a bare ::uuid cast on '' raises
#: instead of cleanly evaluating to "no context, deny".
#: See docs/18-schema-rls-and-migrations.md.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column(
        "model_configurations",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "tenant_model_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("model_configuration_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["users.id"],
            name=op.f("fk_tenant_model_configurations_granted_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_model_configurations_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_configuration_id"],
            ["model_configurations.id"],
            name="fk_tenant_model_configurations_model_configuration",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_model_configurations")),
        sa.UniqueConstraint(
            "tenant_id",
            "model_configuration_id",
            name="uq_tenant_model_configurations_pair",
        ),
    )
    op.create_index(
        "ix_tenant_model_configurations_tenant_id",
        "tenant_model_configurations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_tenant_model_configurations_model_configuration_id",
        "tenant_model_configurations",
        ["model_configuration_id"],
    )

    # --- Backfill, before the new constraint exists -------------------------
    #
    # Two sources, unioned, so no currently-valid assistant can be orphaned:
    #
    #   1. Every (tenant_id, model_configuration_id) pair an assistant is
    #      already using. This is the set the new FK will validate, so it is
    #      the set that must exist first. Derived from the assistants
    #      themselves rather than from ownership, because that is precisely
    #      what "do not break existing assistants" means.
    #   2. Every tenant-owned configuration, used or not. Before this change,
    #      owning a configuration *was* permission to use it; dropping unused
    #      ones would silently take away access a tenant already had.
    op.execute(
        """
        INSERT INTO tenant_model_configurations (id, tenant_id, model_configuration_id)
        SELECT gen_random_uuid(), pair.tenant_id, pair.model_configuration_id
        FROM (
            SELECT DISTINCT a.tenant_id, a.model_configuration_id
            FROM ai_assistants a
            UNION
            SELECT mc.tenant_id, mc.id
            FROM model_configurations mc
            WHERE mc.tenant_id IS NOT NULL
        ) AS pair
        ON CONFLICT (tenant_id, model_configuration_id) DO NOTHING
        """
    )

    # --- Swap the constraint ------------------------------------------------
    op.drop_constraint(
        "fk_ai_assistants_model_configuration", "ai_assistants", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_ai_assistants_model_configuration",
        "ai_assistants",
        "tenant_model_configurations",
        ["tenant_id", "model_configuration_id"],
        ["tenant_id", "model_configuration_id"],
    )

    # --- RLS ----------------------------------------------------------------
    #
    # Read-only to tenants, and only their own grants: a tenant learning which
    # models *another* tenant may use would leak the shape of that customer's
    # deployment. All writes are platform-side and run on the BYPASSRLS
    # `app_platform` connection, so no INSERT/UPDATE/DELETE policy is granted
    # here -- absent policy plus FORCE means the tenant role simply cannot
    # write, which is the intent stated positively.
    op.execute("ALTER TABLE tenant_model_configurations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_model_configurations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_model_configurations_read ON tenant_model_configurations "
        f"FOR SELECT USING (tenant_id = {_TENANT_ID_EXPR})"
    )


def downgrade() -> None:
    """Restores the previous constraint.

    Honest about one thing: the old FK cannot hold for an assistant using a
    platform-owned configuration, because that is exactly the defect this
    migration fixes. Rather than fail halfway through with a constraint
    violation, the downgrade refuses up front and names the rows to resolve --
    a rollback that stops before touching anything beats one that stops in the
    middle of it.
    """
    blocking = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM ai_assistants a "
                "JOIN model_configurations mc ON mc.id = a.model_configuration_id "
                "WHERE mc.tenant_id IS DISTINCT FROM a.tenant_id"
            )
        )
        .scalar_one()
    )
    if blocking:
        raise RuntimeError(
            f"cannot downgrade: {blocking} assistant(s) use a model configuration "
            "not owned by their own tenant, which the pre-entitlement foreign key "
            "cannot express. Repoint or delete those assistants first."
        )

    op.execute("DROP POLICY IF EXISTS tenant_model_configurations_read ON tenant_model_configurations")
    op.drop_constraint(
        "fk_ai_assistants_model_configuration", "ai_assistants", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_ai_assistants_model_configuration",
        "ai_assistants",
        "model_configurations",
        ["tenant_id", "model_configuration_id"],
        ["tenant_id", "id"],
    )
    op.drop_index(
        "ix_tenant_model_configurations_model_configuration_id",
        table_name="tenant_model_configurations",
    )
    op.drop_index(
        "ix_tenant_model_configurations_tenant_id",
        table_name="tenant_model_configurations",
    )
    op.drop_table("tenant_model_configurations")
    op.drop_column("model_configurations", "archived_at")
