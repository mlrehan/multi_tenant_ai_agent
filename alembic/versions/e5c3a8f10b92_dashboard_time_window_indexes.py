"""Time-window indexes for the operator dashboard.

Additive only -- four btree indexes, no data or constraint touched.

`GET /v1/platform/activity` reads "everything since <instant>" across *all*
tenants from four tables, and is polled every minute by each open dashboard.
Every existing index on these tables leads with `tenant_id` (the right shape
for the tenant-scoped reads that dominate), so a cross-tenant time window
could only be answered by scanning the whole table -- including
`conversation_messages`, the largest in the schema. A timestamp-only index
turns each read into a range scan over the window.

Plain `CREATE INDEX` rather than `CONCURRENTLY`: migrations here run in a
transaction, and at the table sizes this ships against the build lock is
brief. A deployment whose `conversation_messages` has grown large should build
this index concurrently by hand first; `IF NOT EXISTS` then makes this
migration a no-op for it.

Revision ID: e5c3a8f10b92
Revises: d8b2e6f41a07
"""

from __future__ import annotations

from alembic import op

revision = "e5c3a8f10b92"
down_revision = "d8b2e6f41a07"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_conversation_messages_created_at", "conversation_messages", "created_at"),
    ("ix_conversations_created_at", "conversations", "created_at"),
    ("ix_answer_feedback_created_at", "answer_feedback", "created_at"),
    ("ix_ai_usage_events_occurred_at", "ai_usage_events", "occurred_at"),
)


def upgrade() -> None:
    for name, table, column in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")


def downgrade() -> None:
    for name, _table, _column in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
