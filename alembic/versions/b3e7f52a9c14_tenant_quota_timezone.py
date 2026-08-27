"""Give each tenant the timezone its daily message allowance resets on.

The daily counter was keyed by the UTC date, so a nursery in British Summer
Time had its allowance reset at 01:00 local rather than midnight -- and a
message sent at 00:30 counted against the previous day. Consistent, never
wrong by more than an hour, and not what anyone means by "messages today".

**NOT NULL with a default of 'UTC', deliberately.** A nullable column would
make "no timezone" expressible, and every reader would then have to invent a
fallback -- which is exactly how the read path and the write path end up
disagreeing about which day a message belongs to. One of those decides what is
enforced and the other decides what the dashboard shows; they must never
differ.

The value is an IANA name (`Europe/London`), not an offset. An offset cannot
express daylight saving, which is the entire problem being fixed here.

**No backfill of existing counters, and none is possible.** The Redis keys
carry the date they were written under; changing the format orphans yesterday's
UTC-keyed values rather than moving them. They expire on their own within 26
hours, so the only visible effect is that a tenant switching timezone may see
today's count restart once. Recorded here because it will look like data loss
to anyone who does not know to expect it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3e7f52a9c14"
down_revision = "f1c94a70b2d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_chatbot_settings",
        sa.Column(
            "quota_timezone",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'UTC'"),
        ),
    )
    # Bounded like every other free-text column on this table. Not a CHECK
    # against a list of valid zones: the IANA database changes, and a
    # constraint enumerating it would start refusing legitimate values the
    # moment a zone is added or renamed. The application validates against the
    # zoneinfo database actually installed, which is the only authority that
    # matters at read time.
    op.create_check_constraint(
        "quota_timezone_bounded",
        "tenant_chatbot_settings",
        "length(quota_timezone) <= 64",
    )


def downgrade() -> None:
    op.drop_constraint(
        "quota_timezone_bounded", "tenant_chatbot_settings", type_="check"
    )
    op.drop_column("tenant_chatbot_settings", "quota_timezone")
