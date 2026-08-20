"""Web Push subscriptions, so an agent is reachable with the console closed.

The chime and the toast both require an open tab. A handoff that arrives while
nobody is looking at the inbox reaches nobody, which is the whole point of a
handoff queue failing quietly.

**A subscription belongs to a membership, not a user.** A browser holds one
push endpoint per origin, but a person may staff two tenants -- so the same
endpoint legitimately appears once per membership, and each tenant notifies
only its own. Keying on the user instead would either leak one tenant's queue
into another's notification or need a tenant filter at send time that a future
edit could drop. RLS enforces the boundary here because `tenant_id` is on the
row.

**`endpoint` is unique per membership, not globally.** Re-subscribing the same
browser must update rather than duplicate (the browser hands back the same
endpoint), and two memberships sharing an endpoint is the normal multi-tenant
case rather than a conflict.

The keys stored are the browser's *public* ECDH key and an auth secret it
generated for this subscription. They are not platform credentials: they let
this server encrypt a payload only that browser can read, and they are useless
to anyone who cannot also present the VAPID private key. Stored plainly for
that reason -- unlike `provider_credentials`, which genuinely is a secret.

Revision ID: f4a8c31d7e90
Revises: e1b6d70c94af
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f4a8c31d7e90"
down_revision = "e1b6d70c94af"
branch_labels = None
depends_on = None

_TENANT_MATCHES = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The push service's URL for this browser. Long: Apple's run to
        # several hundred characters, so TEXT rather than a guessed varchar.
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh_key", sa.Text(), nullable=False),
        sa.Column("auth_key", sa.Text(), nullable=False),
        # Diagnostics only. A subscription that stops working is usually one
        # browser, and knowing which helps without identifying a person.
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        # Bumped on every successful send. A subscription that has not worked
        # for months is a browser nobody uses; this is what a future sweep
        # would prune on.
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        # Composite FK, so a membership id from another tenant is refused by
        # Postgres rather than by application code that might be edited.
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_push_subscriptions_membership",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "membership_id", "endpoint", name="uq_push_subscriptions_membership_endpoint"
        ),
    )
    op.create_index(
        "ix_push_subscriptions_tenant_id", "push_subscriptions", ["tenant_id"]
    )
    op.create_index(
        "ix_push_subscriptions_membership_id", "push_subscriptions", ["membership_id"]
    )

    op.execute("ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE push_subscriptions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY push_subscriptions_isolation ON push_subscriptions
        USING ({_TENANT_MATCHES}) WITH CHECK ({_TENANT_MATCHES})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS push_subscriptions_isolation ON push_subscriptions")
    op.drop_index("ix_push_subscriptions_membership_id", table_name="push_subscriptions")
    op.drop_index("ix_push_subscriptions_tenant_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
