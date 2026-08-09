"""chat_widgets: the public question-answering surface

Phase 13 Part B. A tenant embeds a `<script>` on their own website; visitors
ask questions and get answers grounded in one knowledge base.

**The public key is an identifier, not a secret, and the schema is shaped
around that.** It ships inside a script tag on a public page, so anyone who can
view source can read it. Nothing here treats it as confidential: it is not
hashed at rest (hashing a value the whole internet can read protects nothing
and would prevent showing it back to the tenant in the console), and it is not
what authorizes an answer. What binds a widget to a site is `allowed_origins`,
and what bounds abuse is `daily_question_limit`.

That is a real departure from how `provider_credentials` treats a key, and the
distinction is deliberate: that one is a secret this platform holds on a
tenant's behalf, this one is a public name for a public endpoint.

Revision ID: d1a4c73e59b8
Revises: c5f1a90b2e47
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d1a4c73e59b8"
down_revision = "c5f1a90b2e47"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "chat_widgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # Globally unique: it is the *only* thing the public endpoint is given,
        # so it must identify one widget across every tenant. Unique across the
        # table rather than per-tenant, because the lookup happens before any
        # tenant is known -- that is the whole point of it.
        sa.Column("public_key", sa.Text(), nullable=False, unique=True),
        # Origins allowed to mint a session. Empty means none: a widget with no
        # allowlist is unusable rather than open to everyone. Fail closed --
        # the opposite default would make a half-configured widget a public
        # question-answering endpoint for anyone who found its key.
        sa.Column(
            "allowed_origins",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        # Cost control, not rate limiting: each question costs an embedding, a
        # rerank and a generation. A widget on a busy page with no cap is an
        # unbounded bill, and the tenant who embedded it is not the one paying.
        sa.Column(
            "daily_question_limit",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("500"),
        ),
        sa.Column(
            "created_by_membership_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active','disabled')", name="ck_chat_widgets_status_valid"
        ),
        sa.CheckConstraint(
            "daily_question_limit > 0", name="ck_chat_widgets_limit_positive"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_chat_widgets_knowledge_base",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_chat_widgets_created_by",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_chat_widgets_tenant_id_id"),
    )
    op.create_index("ix_chat_widgets_tenant_id", "chat_widgets", ["tenant_id"])

    op.execute("ALTER TABLE chat_widgets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_widgets FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY chat_widgets_isolation ON chat_widgets "
        f"USING (tenant_id = {_TENANT_ID_EXPR}) "
        f"WITH CHECK (tenant_id = {_TENANT_ID_EXPR})"
    )
    # `app_tenant` manages widgets from the console under RLS as usual.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON chat_widgets TO app_tenant")
    # `app_platform` (BYPASSRLS) is what the *public* endpoint uses for the key
    # lookup, because that lookup happens before any tenant is known -- there
    # is no RLS context to set yet. This is the one read that legitimately
    # crosses the tenant boundary, and it is narrow by construction: one row,
    # by unique public key, and the tenant it returns is then used to scope
    # everything that follows.
    op.execute("GRANT SELECT ON chat_widgets TO app_platform")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS chat_widgets_isolation ON chat_widgets")
    op.drop_index("ix_chat_widgets_tenant_id", table_name="chat_widgets")
    op.drop_table("chat_widgets")
