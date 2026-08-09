"""append-only audit tables

Revision ID: 937a69c41b65
Revises: 79c1357c7aeb
Create Date: 2026-08-04 21:40:59.132677

Phase 8 security-validation finding. docs/03-threat-model.md's STRIDE table
gives the repudiation mitigation as "append-only ``audit_logs`` (DB-level
revoke UPDATE/DELETE grants on that table; app role has INSERT-only)", but the
grants were never actually applied -- ``ALTER DEFAULT PRIVILEGES`` in
docker/postgres-init/01-roles.sql hands every new table full CRUD to
``app_tenant``, and no later migration narrowed it. Verified against the live
database before writing this migration: ``app_tenant`` held DELETE, INSERT,
SELECT and UPDATE on ``audit_logs``.

That made the audit trail forgeable by exactly the role an attacker reaches
first -- the one ordinary request handling runs as. A compromised application
connection could erase evidence of what it did.

``security_events`` gets the same treatment: it is the other append-only
forensic record (docs/17-schema-security-audit.md), and an attacker who can
rewrite the security-event stream can hide a reuse-detection or lockout
signal just as effectively as an audit entry.

SELECT is deliberately retained -- tenant-facing audit views are a product
feature (docs/17), and reading history cannot destroy it.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '937a69c41b65'
down_revision: Union[str, None] = '79c1357c7aeb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APPEND_ONLY_TABLES = ("audit_logs", "security_events")

# Both application roles, not just the RLS-subject one: app_platform has
# BYPASSRLS and is the more privileged connection, so leaving it able to
# rewrite history would defeat the point of revoking it from app_tenant.
_APP_ROLES = ("app_tenant", "app_platform")


def upgrade() -> None:
    for table in _APPEND_ONLY_TABLES:
        for role in _APP_ROLES:
            op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {role}")
        # Future-proofing: the default-privileges grant in
        # docker/postgres-init/01-roles.sql applies at table-creation time, so
        # revoking here is enough for these tables -- but a re-created table
        # would silently regain full CRUD. The append_only regression test in
        # tests/security/ is what catches that if it ever happens.


def downgrade() -> None:
    for table in _APPEND_ONLY_TABLES:
        for role in _APP_ROLES:
            op.execute(f"GRANT UPDATE, DELETE ON {table} TO {role}")
