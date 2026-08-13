"""Bring-your-own-key, attached to the *entitlement* rather than the model.

`model_configurations.provider_credential_id` has existed since Phase 7 and was
never read by anything. Wiring it as-is would have been wrong as well as
incomplete: a configuration is platform-owned and granted to *many* tenants, so
one credential column on it cannot express "bill tenant A's key when tenant A
asks" -- it can only name a single key for everyone. Worse, `provider_credentials`
is under tenant RLS, so a platform admin's own credential is invisible from any
tenant's answer path.

The grant row is where the pair (tenant, model) already lives, so it is where
"which key pays for this" belongs.

**The tenant-confinement is enforced by Postgres, not by application code.** The
new FK is composite -- `(tenant_id, provider_credential_id)` references
`provider_credentials(tenant_id, id)` -- and `tenant_model_configurations.tenant_id`
is NOT NULL. A tenant therefore *cannot* attach another tenant's credential, nor
a platform-owned one (`tenant_id IS NULL` can never match a NOT NULL column), no
matter what id the request carries or which application-layer check is
forgotten. Same posture as `fk_ai_assistants_model_configuration`, and the same
reason: docs/18's "do not rely solely on RLS".

This is the third composite FK in this schema and the prerequisite was already
in place -- `uq_provider_credentials_tenant_id_id` exists, so Postgres accepts
the reference. The two previous times it did not, and the migration was refused
until the UNIQUE was added.

Nullable, no backfill, no data change: every existing grant keeps using the
platform's key exactly as before.

Revision ID: f3c8a1d5be62
Revises: e2b9f4c07a13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3c8a1d5be62"
down_revision = "e2b9f4c07a13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_model_configurations",
        sa.Column("provider_credential_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tenant_model_configurations_provider_credential",
        "tenant_model_configurations",
        "provider_credentials",
        ["tenant_id", "provider_credential_id"],
        ["tenant_id", "id"],
        # RESTRICT, not CASCADE: deleting a credential that is still paying for
        # a model must be refused loudly, not silently move the bill back to the
        # platform. Revocation (`revoked_at`) is the intended off switch and the
        # answer path refuses on it.
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_tenant_model_configurations_provider_credential",
        "tenant_model_configurations",
        type_="foreignkey",
    )
    op.drop_column("tenant_model_configurations", "provider_credential_id")
