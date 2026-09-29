"""How each AI answer turned out, for the "couldn't answer" feed.

Additive only -- one nullable column on `conversation_messages`.

`answer_status` is set on assistant turns at the moment the pipeline knows it:
`no_sources` when retrieval found nothing (the model is never called),
`uncited` when the model answered without citing any source, `grounded` when
it cited one. NULL for every other role, and for answers stored before this
column existed -- those are simply not in the feed, rather than guessed at.

Recording it at answer time rather than inferring it later from the text is
the point: refusal wording varies with the prompt and the model, and a feed
built on matching phrases would quietly break the next time either changed.

Revision ID: b4e8d1a6c2f5
Revises: a9d7c2e4f1b3
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b4e8d1a6c2f5"
down_revision = "a9d7c2e4f1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_messages", sa.Column("answer_status", sa.Text(), nullable=True))
    op.create_check_constraint(
        "answer_status_valid",
        "conversation_messages",
        "answer_status IS NULL OR answer_status IN ('grounded','uncited','no_sources')",
    )
    # The feed reads "this tenant's unanswered turns, newest first".
    op.create_index(
        "ix_conversation_messages_unanswered",
        "conversation_messages",
        ["tenant_id", "created_at"],
        postgresql_where=sa.text("answer_status IN ('uncited','no_sources')"),
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_unanswered", table_name="conversation_messages")
    op.drop_constraint(
        "ck_conversation_messages_answer_status_valid", "conversation_messages", type_="check"
    )
    op.drop_column("conversation_messages", "answer_status")
