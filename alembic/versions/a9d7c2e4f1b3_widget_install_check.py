"""Widget install check: where the chatbot was last seen, and last refused.

Additive only -- four nullable columns on `chat_widgets`.

`last_seen_*` is written when a visitor's browser opens a session from an
allowed website: it is how a tenant admin learns that pasting the install code
actually worked. `last_refused_*` is written when a page on a website *not* in
the allowlist tries: the single most common install mistake (`www.` vs bare
domain, `http` vs `https`) otherwise fails invisibly -- visitors see no
chatbot, and the admin sees nothing wrong.

Both are informational. The refused origin comes from an unauthenticated
request, so it is only ever displayed as "a page on X tried to load your
chatbot" -- never trusted, never used to decide anything.

Revision ID: a9d7c2e4f1b3
Revises: e5c3a8f10b92
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a9d7c2e4f1b3"
down_revision = "e5c3a8f10b92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_widgets", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_widgets", sa.Column("last_seen_origin", sa.Text(), nullable=True))
    op.add_column("chat_widgets", sa.Column("last_refused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_widgets", sa.Column("last_refused_origin", sa.Text(), nullable=True))
    op.create_check_constraint(
        "origins_bounded",
        "chat_widgets",
        "(last_seen_origin IS NULL OR length(last_seen_origin) <= 255) AND "
        "(last_refused_origin IS NULL OR length(last_refused_origin) <= 255)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_chat_widgets_origins_bounded", "chat_widgets", type_="check")
    for column in ("last_refused_origin", "last_refused_at", "last_seen_origin", "last_seen_at"):
        op.drop_column("chat_widgets", column)
