"""Drop the length CHECKs on the assistant's role and avoid instructions.

The caps were a guess at how long a brief needs to be, and the guess was wrong
twice: 1000 could not hold the platform's own shipped default, and 2000 is the
same guess with a bigger number. A tenant writing the instructions their
assistant runs on is the person best placed to decide how much detail that
takes, and being refused mid-edit by a limit nobody chose for a reason is a
worse failure than a long prompt.

Both columns are already `Text`, so nothing about storage changes -- only the
refusal goes. The real bound on a prompt is the model's context window, which
is enforced by the provider at request time and moves as models change; a
column constraint cannot track that and would only ever disagree with it.

`response_length`, `personality` and the industry/description caps are
deliberately untouched: those are enums and short identifiers where a bound
means something.
"""

from __future__ import annotations

from alembic import op

revision = "e9c47b13f0a2"
down_revision = "d6a83f21e574"
branch_labels = None
depends_on = None

_COLUMNS = ("role_instructions", "avoid_instructions")


def upgrade() -> None:
    for column in _COLUMNS:
        op.drop_constraint(f"{column}_bounded", "ai_assistants", type_="check")


def downgrade() -> None:
    # Restoring the bound will refuse if any row has since used the room this
    # migration granted. Truncating on the way down would silently rewrite a
    # tenant's instructions -- the assistant would keep answering, following a
    # brief that had been cut off mid-sentence -- so the rows are left alone
    # and the constraint creation is allowed to fail loudly instead.
    for column in _COLUMNS:
        op.create_check_constraint(
            f"{column}_bounded",
            "ai_assistants",
            f"{column} IS NULL OR length({column}) <= 2000",
        )
