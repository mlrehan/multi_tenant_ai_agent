"""Meter the knowledge-base retrieval tester.

The console's search box embeds the typed query through the provider on every
test, and that spend was the last one recorded nowhere. It gets its own
channel, `search`: one row per test, embedding only, costed at the embedding
model's price, and -- like `ingestion` -- outside the chat allowance and the
daily message count, whose Redis seeds read `console`/`widget` only.

Revision ID: f4b8d2c6e1a9
Revises: e7a3c1f9d2b4
"""

from __future__ import annotations

from alembic import op

revision = "f4b8d2c6e1a9"
down_revision = "e7a3c1f9d2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_usage_events DROP CONSTRAINT ck_ai_usage_events_channel_valid")
    op.execute(
        "ALTER TABLE ai_usage_events ADD CONSTRAINT ck_ai_usage_events_channel_valid "
        "CHECK (channel IN ('console','widget','ingestion','search'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ai_usage_events WHERE channel = 'search'")
    op.execute("ALTER TABLE ai_usage_events DROP CONSTRAINT ck_ai_usage_events_channel_valid")
    op.execute(
        "ALTER TABLE ai_usage_events ADD CONSTRAINT ck_ai_usage_events_channel_valid "
        "CHECK (channel IN ('console','widget','ingestion'))"
    )
