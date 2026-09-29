"""Let the usage ledger record embedding spend on ingestion.

`ai_usage_events.channel` admitted only `console` and `widget` -- answers. Every
document upload, crawled page, retry and re-sync embeds its chunks through the
provider too, and none of it was recorded anywhere: on the dev tenant the
dashboards showed 39,445 tokens against 319,895 spent indexing.

`ingestion` rows are written by the worker, one per indexed document, after
the document's own transaction has finished (a failed document still paid for
its embeddings). They are **not** part of the chat allowance or the daily
message count: the Redis seeds read `channel IN ('console','widget')` only.

No backfill. The chunks' stored `token_count` approximates what indexing cost,
but it is not a record of when or whether it was billed, and inventing ledger
rows from it would put estimates into a table whose point is that it holds
what happened. Metering starts from this revision.

Revision ID: d9e4b7a1c6f2
Revises: c7f2a9e3d815
"""

from __future__ import annotations

from alembic import op

revision = "d9e4b7a1c6f2"
down_revision = "c7f2a9e3d815"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_usage_events DROP CONSTRAINT ck_ai_usage_events_channel_valid")
    op.execute(
        "ALTER TABLE ai_usage_events ADD CONSTRAINT ck_ai_usage_events_channel_valid "
        "CHECK (channel IN ('console','widget','ingestion'))"
    )


def downgrade() -> None:
    # Ingestion rows would violate the narrower check; they go first.
    op.execute("DELETE FROM ai_usage_events WHERE channel = 'ingestion'")
    op.execute("ALTER TABLE ai_usage_events DROP CONSTRAINT ck_ai_usage_events_channel_valid")
    op.execute(
        "ALTER TABLE ai_usage_events ADD CONSTRAINT ck_ai_usage_events_channel_valid "
        "CHECK (channel IN ('console','widget'))"
    )
