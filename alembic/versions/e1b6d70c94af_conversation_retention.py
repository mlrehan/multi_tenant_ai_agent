"""Tenant-configurable conversation retention.

Widget conversations -- including anonymous visitor sessions -- are now
persisted rather than living only in Redis for the length of a session. That
makes retention a real question rather than a theoretical one: without a limit
the platform would accumulate strangers' conversations indefinitely, on the
tenant's behalf, with nobody having decided how long to keep them.

**30 days by default, and the column is NOT NULL on purpose.** A nullable
column would make "keep forever" expressible by accident -- an unset field
would silently mean indefinite storage, which is precisely the outcome the
default exists to avoid. A tenant with a genuine legal reason to keep more can
raise it; the CHECK bounds it at ten years so a typo cannot mean "effectively
forever" either.

Retention is stored as *days* rather than an expiry timestamp per row: the
tenant edits one number and every existing conversation honours it
immediately, where stamped-at-write expiries would keep the old policy for
everything already stored.

Revision ID: e1b6d70c94af
Revises: c9f2a4d81b57
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1b6d70c94af"
down_revision = "c9f2a4d81b57"
branch_labels = None
depends_on = None

DEFAULT_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650


def upgrade() -> None:
    op.add_column(
        "tenant_chatbot_settings",
        sa.Column(
            "conversation_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_RETENTION_DAYS),
        ),
    )
    op.create_check_constraint(
        "conversation_retention_days_range",
        "tenant_chatbot_settings",
        f"conversation_retention_days BETWEEN 1 AND {MAX_RETENTION_DAYS}",
    )

    # The purge sweeps by tenant and age. Without this it is a sequential scan
    # over every conversation in the platform, run on a schedule -- the shape
    # of job that is fine on day one and pages someone in month six.
    op.create_index(
        "ix_conversations_tenant_last_activity",
        "conversations",
        ["tenant_id", "last_message_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_tenant_last_activity", table_name="conversations")
    op.drop_constraint(
        "ck_tenant_chatbot_settings_conversation_retention_days_range",
        "tenant_chatbot_settings",
        type_="check",
    )
    op.drop_column("tenant_chatbot_settings", "conversation_retention_days")
