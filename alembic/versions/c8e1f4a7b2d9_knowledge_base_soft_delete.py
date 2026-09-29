"""Knowledge bases can be deleted -- softly.

A knowledge base is referenced by its documents (soft-deleted, rows kept), by
data sources, by chat widgets and by answer feedback (append-only history). A
hard delete would either be refused by those foreign keys or destroy records
this platform keeps on purpose, so deletion sets `deleted_at` and the
repository treats such a row as not existing. Everything searchable -- vectors,
passages, stored files -- is removed by the use case before the row is marked.

Additive only: one nullable column.

Revision ID: c8e1f4a7b2d9
Revises: b5d2f8a4c1e7
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c8e1f4a7b2d9"
down_revision = "b5d2f8a4c1e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "deleted_at")
