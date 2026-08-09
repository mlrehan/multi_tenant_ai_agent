"""Widen the refresh-token revoke-reason allow-list.

Administrative account lifecycle (create/suspend/delete/rename a user, change
your own password) revokes sessions for reasons the original Phase 5 CHECK
constraint didn't anticipate. Writing any of them raised
`CheckViolationError`, surfacing as a 500 from every one of those endpoints.

The alternative -- reusing the generic 'admin' value -- was rejected because
`revoked_reason` exists to answer "why did this session end?" during incident
review, and four distinct causes collapsed into one word doesn't answer it.

Revision ID: a4d2f81c9b30
Revises: 937a69c41b65
"""

from __future__ import annotations

from alembic import op

revision = "a4d2f81c9b30"
down_revision = "937a69c41b65"
branch_labels = None
depends_on = None

_OLD = "('rotated','reuse_detected','logout','logout_all','password_reset','admin')"
_NEW = (
    "('rotated','reuse_detected','logout','logout_all','password_reset',"
    "'password_change','account_suspended','account_deleted','email_changed','admin')"
)


def upgrade() -> None:
    # Bare name, not the ck_-prefixed one: the metadata naming convention
    # expands it, and passing the full name yields
    # `ck_refresh_tokens_ck_refresh_tokens_revoked_reason_valid`.
    op.drop_constraint("revoked_reason_valid", "refresh_tokens", type_="check")
    op.create_check_constraint(
        "revoked_reason_valid",
        "refresh_tokens",
        f"revoked_reason IS NULL OR revoked_reason IN {_NEW}",
    )


def downgrade() -> None:
    # Rows carrying one of the new reasons would violate the narrower
    # constraint, so fold them into 'admin' first -- a lossy but valid
    # reduction, and the only way this direction can succeed at all.
    op.execute(
        "UPDATE refresh_tokens SET revoked_reason = 'admin' "
        "WHERE revoked_reason IN "
        "('password_change','account_suspended','account_deleted','email_changed')"
    )
    # Bare name, not the ck_-prefixed one: the metadata naming convention
    # expands it, and passing the full name yields
    # `ck_refresh_tokens_ck_refresh_tokens_revoked_reason_valid`.
    op.drop_constraint("revoked_reason_valid", "refresh_tokens", type_="check")
    op.create_check_constraint(
        "revoked_reason_valid",
        "refresh_tokens",
        f"revoked_reason IS NULL OR revoked_reason IN {_OLD}",
    )
