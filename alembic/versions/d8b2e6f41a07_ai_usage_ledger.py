"""AI usage ledger: one durable row per answered question.

Additive only -- one new table, no existing column or constraint touched.

**Why it exists.** Every usage figure on both dashboards, and every quota the
answer path enforces, was held only in Redis. Redis ran with persistence off,
so a restart zeroed them all: the dashboards read "0 used" for a tenant that
had spent sixty thousand tokens, and -- worse -- the monthly token allowance
and the daily message cap were silently handed back in full. Redis stays the
fast counter the answer path reads; this table is what it is rebuilt from when
a key is missing, and the record a bill can be reconciled against.

**What a row holds.** Counts, never content: no question, no answer, no
visitor or member identity. A usage record outlives the conversation it came
from (retention deletes those after 30 days by default) because a month's bill
must still add up after the thread is gone -- so it must not carry anything
retention exists to remove.

**Tenant-confined by RLS on both ``USING`` and ``WITH CHECK``**, so a row can
neither be read across tenants nor written into another one.

**Append-only for both app roles.** ``ALTER DEFAULT PRIVILEGES`` grants full
CRUD on every new table; a usage record that the application can edit is not a
record. The REVOKE covers ``app_platform`` too: the platform reads usage, it
never rewrites it. Deletion still happens where it should -- the tenant FK
cascades, and referential actions run as the table owner.

Revision ID: d8b2e6f41a07
Revises: c4a91e7d2f36
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d8b2e6f41a07"
down_revision = "c4a91e7d2f36"
branch_labels = None
depends_on = None

_TENANT_MATCHES = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        # The configuration the answer resolved, when it resolved one. NULL is
        # the platform default model -- every widget answer today.
        sa.Column(
            "model_configuration_id",
            sa.Uuid(),
            sa.ForeignKey("model_configurations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("channel IN ('console','widget')", name="channel_valid"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="tokens_non_negative",
        ),
    )
    # Every read is "this tenant, since this instant": the month for tokens,
    # the tenant's local day for messages.
    op.create_index(
        "ix_ai_usage_events_tenant_occurred", "ai_usage_events", ["tenant_id", "occurred_at"]
    )

    op.execute("ALTER TABLE ai_usage_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_usage_events FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY ai_usage_events_isolation ON ai_usage_events
        USING ({_TENANT_MATCHES}) WITH CHECK ({_TENANT_MATCHES})
        """
    )
    op.execute("REVOKE UPDATE, DELETE ON ai_usage_events FROM app_tenant")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON ai_usage_events FROM app_platform")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS ai_usage_events_isolation ON ai_usage_events")
    op.drop_table("ai_usage_events")
