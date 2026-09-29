"""Make eleven "when" columns default to the time of the insert, not of the migration.

Migration `c1178e6fb886` declared these with `server_default='now()'` -- a
bare Python string. SQLAlchemy quotes it, so PostgreSQL evaluated `'now()'`
once, as a literal, when the table was created, and stored that frozen
instant as the default. Every role grant since then records the moment the
database was built: an administrator's platform role reads as granted
before their account existed. For an identity platform, "when was this
person given this power?" is an audit question, and the answer was wrong.

The same trap is written up in CLAUDE.md (Phase 5); this is the Phase 6
migration that still carried it.

**Defaults only. No stored row is changed.** Existing values cannot be
recovered -- the true grant times were never recorded anywhere -- and
rewriting them to a guess (say, the membership's creation time) would put
invented timestamps into audit-relevant rows. They are left as they are and
stated as unreliable for rows created before this revision.

Revision ID: c7f2a9e3d815
Revises: b4e8d1a6c2f5
"""

from __future__ import annotations

from alembic import op

revision = "c7f2a9e3d815"
down_revision = "b4e8d1a6c2f5"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("platform_permissions", "created_at"),
    ("role_hierarchy", "created_at"),
    ("tenant_permissions", "created_at"),
    ("platform_role_permissions", "granted_at"),
    ("platform_user_roles", "granted_at"),
    ("authorization_overrides", "created_at"),
    ("impersonation_sessions", "started_at"),
    ("tenant_features", "created_at"),
    ("tenant_invitations", "created_at"),
    ("tenant_membership_roles", "granted_at"),
    ("tenant_role_permissions", "granted_at"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT now()")


def downgrade() -> None:
    # There is no correct value to go back to: the frozen literal was the bug.
    # Downgrading leaves the live default in place rather than reinstating it.
    pass
