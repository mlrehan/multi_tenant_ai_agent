"""One more answer outcome: `restricted`.

The model declined because of the tenant's own avoid rules (it opened with the
`[RESTRICTED]` marker -- see `application/ai_resources/answer_gate.py`).
Before this, such a decline was recorded as `not_in_sources` and listed in the
"couldn't answer" feed as "Not covered by your sources", although the sources
may well cover it. It is deliberately *not* added to the feed's partial index:
the feed does not list it, because adding a document cannot change a
restriction the tenant chose.

Revision ID: b5d2f8a4c1e7
Revises: a3c6e9f2b7d4
"""

from __future__ import annotations

from alembic import op

revision = "b5d2f8a4c1e7"
down_revision = "a3c6e9f2b7d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversation_messages DROP CONSTRAINT ck_conversation_messages_answer_status_valid"
    )
    op.execute(
        "ALTER TABLE conversation_messages ADD CONSTRAINT ck_conversation_messages_answer_status_valid "
        "CHECK (answer_status IS NULL OR answer_status IN "
        "('grounded','uncited','no_sources','not_in_sources','withheld','restricted'))"
    )


def downgrade() -> None:
    # A restricted decline is closest to "not in the sources" among the older
    # values; fold it there rather than failing the downgrade.
    op.execute(
        "UPDATE conversation_messages SET answer_status = 'not_in_sources' "
        "WHERE answer_status = 'restricted'"
    )
    op.execute(
        "ALTER TABLE conversation_messages DROP CONSTRAINT ck_conversation_messages_answer_status_valid"
    )
    op.execute(
        "ALTER TABLE conversation_messages ADD CONSTRAINT ck_conversation_messages_answer_status_valid "
        "CHECK (answer_status IS NULL OR answer_status IN "
        "('grounded','uncited','no_sources','not_in_sources','withheld'))"
    )
