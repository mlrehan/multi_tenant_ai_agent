"""Widen the role and avoid caps to 2000 characters.

The shipped default `avoid` text is 1072 characters, so the 1000-character
CHECK made the platform's *own* default unstorable: a tenant pressing Save on
the Behaviour tab without editing anything would have been refused by the
database. The caps still exist, and still exist as constraints rather than
validation alone -- the prompt builder must never have to silently truncate
tenant-authored instructions -- they are simply set where real briefs fit.

Both columns are widened together even though only `avoid` overflows. They are
two halves of one form and a tenant who can write 2000 characters of
restrictions and only 1000 of role would reasonably read that as a bug.

Widening a CHECK is safe in both directions here: no existing row can violate
the larger bound, so this needs no backfill and no lock beyond the validation
scan Postgres does on the new constraint.
"""

from __future__ import annotations

from alembic import op

revision = "d6a83f21e574"
down_revision = "a2f7b40e18c5"
branch_labels = None
depends_on = None

_COLUMNS = ("role_instructions", "avoid_instructions")


def upgrade() -> None:
    for column in _COLUMNS:
        op.drop_constraint(f"{column}_bounded", "ai_assistants", type_="check")
        op.create_check_constraint(
            f"{column}_bounded",
            "ai_assistants",
            f"{column} IS NULL OR length({column}) <= 2000",
        )


def downgrade() -> None:
    # Narrowing again would fail against any row that has used the extra room,
    # including one holding the platform's own default. Truncating on the way
    # down would silently rewrite a tenant's instructions, so the rows are left
    # alone and the constraint simply returns to its old bound -- a downgrade
    # on a database that has stored longer text will refuse, which is the
    # honest outcome.
    for column in _COLUMNS:
        op.drop_constraint(f"{column}_bounded", "ai_assistants", type_="check")
        op.create_check_constraint(
            f"{column}_bounded",
            "ai_assistants",
            f"{column} IS NULL OR length({column}) <= 1000",
        )
