"""Answer feedback: a person's rating of one AI answer.

Additive only -- one new table, no existing column or constraint touched.

**Tenant-confined three ways**, none of them relying on the application:
RLS (``USING`` *and* ``WITH CHECK``, so a row can neither be read across
tenants nor written *into* another one), and composite foreign keys to the
knowledge base, widget and membership, so feedback cannot name another
tenant's resource whatever ids a caller supplies.

**Append-only for the app role.** ``ALTER DEFAULT PRIVILEGES`` grants
``app_tenant`` full CRUD on every new table, so without an explicit REVOKE the
table would be editable -- the Phase 8 ``audit_logs`` lesson, which has now
recurred four times in this schema. Deletion still happens where it should:
the foreign keys cascade when a tenant, knowledge base, widget or membership
is removed, and referential actions run as the table owner, not as
``app_tenant``.

Revision ID: c4a91e7d2f36
Revises: b3e7f52a9c14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4a91e7d2f36"
down_revision = "b3e7f52a9c14"
branch_labels = None
depends_on = None

_TENANT_MATCHES = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "answer_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=True),
        sa.Column("widget_id", sa.Uuid(), nullable=True),
        sa.Column("visitor_session_id", sa.Uuid(), nullable=True),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "rating IN ('up','down')", name="rating_valid"
        ),
        sa.CheckConstraint(
            "(membership_id IS NOT NULL AND widget_id IS NULL "
            "AND visitor_session_id IS NULL) OR "
            "(membership_id IS NULL AND widget_id IS NOT NULL "
            "AND visitor_session_id IS NOT NULL)",
            name="exactly_one_author",
        ),
        sa.CheckConstraint(
            "length(question) <= 4000", name="question_bounded"
        ),
        sa.CheckConstraint(
            "length(answer) <= 20000", name="answer_bounded"
        ),
        sa.CheckConstraint(
            "comment IS NULL OR length(comment) <= 1000",
            name="comment_bounded",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_answer_feedback_knowledge_base",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "widget_id"],
            ["chat_widgets.tenant_id", "chat_widgets.id"],
            name="fk_answer_feedback_widget",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_answer_feedback_membership",
            ondelete="CASCADE",
        ),
    )
    # The review query: this tenant's feedback, newest first.
    op.create_index(
        "ix_answer_feedback_tenant_created", "answer_feedback", ["tenant_id", "created_at"]
    )
    # The per-session cap is checked on every widget rating.
    op.create_index(
        "ix_answer_feedback_visitor_session",
        "answer_feedback",
        ["tenant_id", "visitor_session_id"],
    )

    op.execute("ALTER TABLE answer_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE answer_feedback FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY answer_feedback_isolation ON answer_feedback
        USING ({_TENANT_MATCHES}) WITH CHECK ({_TENANT_MATCHES})
        """
    )
    op.execute("REVOKE UPDATE, DELETE ON answer_feedback FROM app_tenant")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS answer_feedback_isolation ON answer_feedback")
    op.drop_table("answer_feedback")
