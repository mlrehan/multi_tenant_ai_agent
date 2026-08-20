"""Conversation messages -- the content docs/16 deliberately left undecided.

`conversations` has existed since Phase 7 carrying only *metadata* (who, which
assistant, when it was last touched). The turns themselves lived nowhere, so a
conversation could be listed but never reopened, and every question reached the
model with no memory of the one before it.

**Memory is stored, not recomputed.** `conversations.summary` holds a rolling
precis of the turns already compacted, and `summary_through_seq` records how far
it reaches -- so assembling context is "the summary, plus messages after
`summary_through_seq`", never a re-read of the whole thread. That is what keeps
a long conversation's prompt bounded.

`seq` is a per-conversation ordinal rather than a timestamp ordering: two
messages written in the same millisecond must still have a defined order, and
"messages after N" is an index range rather than a time comparison.

The FK is composite -- `(tenant_id, conversation_id)` -> `conversations(tenant_id,
id)` -- so a message cannot be attached to another tenant's conversation
whatever id a caller supplies. That needs a UNIQUE on the referenced pair, which
`conversations` did not have; this is the fourth time in this schema that a
composite FK has required adding one first.

Revision ID: b8e2f4a71c93
Revises: a7d4e91c3f08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b8e2f4a71c93"
down_revision = "a7d4e91c3f08"
branch_labels = None
depends_on = None

_TENANT_MATCHES = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_conversations_tenant_id_id", "conversations", ["tenant_id", "id"]
    )
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("summary_through_seq", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        #: Position within the conversation, 1-based and gapless.
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        #: What the assistant cited, kept beside the answer so reopening a
        #: thread shows the same sources the reader originally saw.
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_conversation_messages_role_valid"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_conversation_messages_conversation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "conversation_id", "seq", name="uq_conversation_messages_seq"
        ),
    )
    # The only access pattern: one conversation's turns, in order.
    op.create_index(
        "ix_conversation_messages_thread",
        "conversation_messages",
        ["tenant_id", "conversation_id", "seq"],
    )
    # Search across a tenant's own message text. GIN over to_tsvector rather
    # than a trigram index: this is word search over prose, and the expression
    # index keeps the column itself free of a stored tsvector to maintain.
    op.execute(
        "CREATE INDEX ix_conversation_messages_search ON conversation_messages "
        "USING gin (to_tsvector('english', content))"
    )

    op.execute("ALTER TABLE conversation_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversation_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY conversation_messages_isolation ON conversation_messages
        USING ({_TENANT_MATCHES}) WITH CHECK ({_TENANT_MATCHES})
        """
    )
    # Messages are append-only from the application's point of view: a turn
    # that was said cannot be un-said, only the whole conversation deleted.
    op.execute(
        "REVOKE UPDATE ON conversation_messages FROM app_tenant"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS conversation_messages_isolation ON conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_column("conversations", "summary_through_seq")
    op.drop_column("conversations", "summary")
    op.drop_constraint("uq_conversations_tenant_id_id", "conversations", type_="unique")
