"""Per-conversation switch that stops the AI taking a thread back.

The automatic fallback exists to rescue a visitor from an unworked queue. But
an agent who is genuinely handling a conversation -- reading a long document,
checking with a colleague, waiting on a customer to find an order number --
must be able to say "this one is mine" and have that outrank the timer. Without
a stored flag the only way to hold a thread was to keep typing into it.

NOT NULL with a `false` default, deliberately: three-valued logic here would
make "nobody has decided" a state the fallback has to interpret, and the
interpretation would be "false" anyway. Every existing row means exactly what
the default says -- the fallback applies.

Revision ID: a2f7b40e18c5
Revises: f4a8c31d7e90
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a2f7b40e18c5"
down_revision = "f4a8c31d7e90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "ai_fallback_disabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "ai_fallback_disabled")
