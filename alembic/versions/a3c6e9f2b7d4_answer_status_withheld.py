"""Two more answer outcomes: `not_in_sources` and `withheld`.

`not_in_sources`: the model opened its reply with the `[NO_ANSWER]` marker --
the sources don't answer the question, though it may still cite related
material (a contact page). Before, citing anything made such an answer
`grounded`, so the gap never reached the "couldn't answer" feed.

`withheld`: the model answered without citing anything, and the pipeline
replaced the reply with an honest "I couldn't find that" (see
`application/ai_resources/answer_gate.py`).

Both are unanswered questions, so the feed's partial index covers them too.
Existing rows are untouched.

Revision ID: a3c6e9f2b7d4
Revises: f4b8d2c6e1a9
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a3c6e9f2b7d4"
down_revision = "f4b8d2c6e1a9"
branch_labels = None
depends_on = None

_UNANSWERED = "('uncited','no_sources','not_in_sources','withheld')"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversation_messages DROP CONSTRAINT ck_conversation_messages_answer_status_valid"
    )
    op.execute(
        "ALTER TABLE conversation_messages ADD CONSTRAINT ck_conversation_messages_answer_status_valid "
        "CHECK (answer_status IS NULL OR answer_status IN "
        "('grounded','uncited','no_sources','not_in_sources','withheld'))"
    )
    op.drop_index("ix_conversation_messages_unanswered", table_name="conversation_messages")
    op.create_index(
        "ix_conversation_messages_unanswered",
        "conversation_messages",
        ["tenant_id", "created_at"],
        postgresql_where=sa.text(f"answer_status IN {_UNANSWERED}"),
    )


def downgrade() -> None:
    # Rows carrying a new status would violate the old constraint; fold them
    # into the nearest old meaning first rather than failing the downgrade.
    op.execute(
        "UPDATE conversation_messages SET answer_status = 'uncited' "
        "WHERE answer_status IN ('not_in_sources','withheld')"
    )
    op.drop_index("ix_conversation_messages_unanswered", table_name="conversation_messages")
    op.create_index(
        "ix_conversation_messages_unanswered",
        "conversation_messages",
        ["tenant_id", "created_at"],
        postgresql_where=sa.text("answer_status IN ('uncited','no_sources')"),
    )
    op.execute(
        "ALTER TABLE conversation_messages DROP CONSTRAINT ck_conversation_messages_answer_status_valid"
    )
    op.execute(
        "ALTER TABLE conversation_messages ADD CONSTRAINT ck_conversation_messages_answer_status_valid "
        "CHECK (answer_status IS NULL OR answer_status IN ('grounded','uncited','no_sources'))"
    )
