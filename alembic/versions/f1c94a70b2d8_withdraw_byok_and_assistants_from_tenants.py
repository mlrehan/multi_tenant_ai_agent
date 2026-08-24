"""Withdraw BYOK and assistant management from the tenant surface.

Three cleanups that belong together because they are one product decision: a
tenant admin configures their chatbot and nothing else about how it is
powered. The platform owns provider credentials, model choice and token
budgets; the tenant owns the brief, the identity and the handoff policy.

**1. The chatbot's brief moves from `ai_assistants` to
`tenant_chatbot_settings`.** Role, avoid-rules, personality and response
length were assistant fields, reachable only by owning an assistant -- which
tenants no longer do. They are added here and backfilled from whichever
assistant each tenant was actually using, so a tenant that had written a brief
keeps it and does not silently revert to the platform default.

**2. Two entitlement flags are dropped.** `allow_own_provider_credentials` and
`allow_create_assistant` gated capabilities that no longer have a route to
reach. A toggle in the platform console that governs nothing is worse than an
absent one: an operator sets it and believes they have changed something.

**3. Five permission rows are deleted.** Editing the seed list in
`scripts/bootstrap_tenant_catalog.py` only governs a *fresh* deployment; rows
already inserted here -- and any custom tenant role that was built to include
them -- survive it. Deleting the `tenant_permissions` row cascades through
`tenant_role_permissions`, which is what actually strips the grant from every
role holding it.

**Nothing tenant-authored is destroyed.** `ai_assistants`, `assistant_members`
and `provider_credentials` are all left in place with their rows intact, as are
the historical `conversations.assistant_id` / `chat_widgets.assistant_id`
values that record how past answers were produced. They become unreachable,
not deleted -- an unreachable row costs nothing, and a conversation's record of
which assistant answered it is exactly the history a "simplify the UI" change
has no business erasing.

**No length CHECK on the two new text columns**, deliberately, and this is not
an oversight: migration `e9c47b13f0a2` removed exactly those constraints from
`ai_assistants` because the cap was a guess that was wrong twice. Re-adding one
here under a different table name would repeat the mistake it documented.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1c94a70b2d8"
down_revision = "e9c47b13f0a2"
branch_labels = None
depends_on = None

#: Permissions whose routes were removed. `tenant.assistants.*` gated the
#: assistant CRUD endpoints; `tenant.provider_credentials.manage` gated the
#: bring-your-own-key endpoints. All eight routes are gone.
_DEAD_PERMISSIONS = (
    "tenant.assistants.create",
    "tenant.assistants.publish",
    "tenant.assistants.manage",
    "tenant.assistants.view_all",
    "tenant.provider_credentials.manage",
)


def upgrade() -> None:
    # --- 1. the brief moves to the tenant --------------------------------
    #
    # Nullable text with no server default: NULL means "never written", which
    # `TenantChatbotSettings.resolved_role()` turns into the platform default
    # named for this company. A server default of '' would make every existing
    # row look like a tenant who had deliberately cleared their brief.
    op.add_column(
        "tenant_chatbot_settings",
        sa.Column("role_instructions", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenant_chatbot_settings",
        sa.Column("avoid_instructions", sa.Text(), nullable=True),
    )
    # These two are NOT NULL with a default, matching the columns they came
    # from: an absent tone is not a meaningful state, and the prompt builder
    # would substitute the same value anyway.
    op.add_column(
        "tenant_chatbot_settings",
        sa.Column(
            "personality",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'neutral'"),
        ),
    )
    op.add_column(
        "tenant_chatbot_settings",
        sa.Column(
            "response_length",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'balanced'"),
        ),
    )

    # Backfill from the assistant each tenant was actually using.
    #
    # `DISTINCT ON (tenant_id) ... ORDER BY tenant_id, ...` picks exactly one
    # assistant per tenant, deterministically: a published one before a draft
    # (published is what was answering), then most recently updated (the one
    # someone last cared about), then by id so the result never depends on
    # physical row order. Archived assistants are excluded outright -- a brief
    # its owner retired is not the brief to promote to tenant-wide.
    #
    # Only rows that actually said something are copied: COALESCE/NULLIF leave
    # `role_instructions` NULL when the assistant never had one, so the tenant
    # still resolves to the platform default rather than to an empty string.
    op.execute(
        """
        WITH chosen AS (
            SELECT DISTINCT ON (tenant_id)
                   tenant_id,
                   NULLIF(TRIM(COALESCE(role_instructions, '')), '')  AS role_instructions,
                   NULLIF(TRIM(COALESCE(avoid_instructions, '')), '') AS avoid_instructions,
                   COALESCE(NULLIF(TRIM(personality), ''), 'neutral')      AS personality,
                   COALESCE(NULLIF(TRIM(response_length), ''), 'balanced') AS response_length
              FROM ai_assistants
             WHERE status <> 'archived'
             ORDER BY tenant_id,
                      (status = 'published') DESC,
                      updated_at DESC,
                      id
        )
        UPDATE tenant_chatbot_settings AS s
           SET role_instructions  = chosen.role_instructions,
               avoid_instructions = chosen.avoid_instructions,
               personality        = chosen.personality,
               response_length    = chosen.response_length
          FROM chosen
         WHERE s.tenant_id = chosen.tenant_id
        """
    )

    # A tenant with assistants but no settings row has nowhere for the backfill
    # above to land, and would lose its brief. Create the row from the same
    # choice. `ON CONFLICT DO NOTHING` because the UPDATE has already handled
    # every tenant that did have a row.
    op.execute(
        """
        WITH chosen AS (
            SELECT DISTINCT ON (tenant_id)
                   tenant_id,
                   NULLIF(TRIM(COALESCE(role_instructions, '')), '')  AS role_instructions,
                   NULLIF(TRIM(COALESCE(avoid_instructions, '')), '') AS avoid_instructions,
                   COALESCE(NULLIF(TRIM(personality), ''), 'neutral')      AS personality,
                   COALESCE(NULLIF(TRIM(response_length), ''), 'balanced') AS response_length
              FROM ai_assistants
             WHERE status <> 'archived'
             ORDER BY tenant_id,
                      (status = 'published') DESC,
                      updated_at DESC,
                      id
        )
        INSERT INTO tenant_chatbot_settings
            (id, tenant_id, role_instructions, avoid_instructions,
             personality, response_length, created_at, updated_at)
        SELECT gen_random_uuid(), chosen.tenant_id, chosen.role_instructions,
               chosen.avoid_instructions, chosen.personality,
               chosen.response_length, now(), now()
          FROM chosen
         WHERE NOT EXISTS (
                   SELECT 1 FROM tenant_chatbot_settings s
                    WHERE s.tenant_id = chosen.tenant_id
               )
        ON CONFLICT DO NOTHING
        """
    )

    # `app_tenant` needs UPDATE on the four new columns. The table is already
    # tenant-writable (unlike `tenant_entitlements`), so the default grant
    # covers them -- asserted here rather than assumed, because a column added
    # after `ALTER DEFAULT PRIVILEGES` ran is covered by the table-level grant
    # the table already holds, not by a new one.

    # --- 2. dead entitlement flags ---------------------------------------
    op.drop_column("tenant_entitlements", "allow_own_provider_credentials")
    op.drop_column("tenant_entitlements", "allow_create_assistant")

    # --- 3. dead permissions ---------------------------------------------
    #
    # Deleted rather than left inert. A permission code nothing checks is a
    # trap for the next operator building a custom role: it appears in the
    # picker, they grant it, and it buys nothing. The cascade to
    # `tenant_role_permissions` is what removes it from roles that already
    # hold it -- including custom roles a tenant built themselves.
    op.execute(
        sa.text("DELETE FROM tenant_permissions WHERE code = ANY(:codes)").bindparams(
            sa.bindparam("codes", value=list(_DEAD_PERMISSIONS))
        )
    )


def downgrade() -> None:
    # The permission rows come back without their role grants: which roles held
    # them is not recorded anywhere once the cascade has run, and inventing an
    # assignment would hand roles a permission nobody granted them.
    for code in _DEAD_PERMISSIONS:
        resource, action = code.removeprefix("tenant.").rsplit(".", 1)
        op.execute(
            sa.text(
                "INSERT INTO tenant_permissions "
                "(id, code, resource, action, risk_level, is_system, tenant_customizable) "
                "VALUES (gen_random_uuid(), :code, :resource, :action, 'medium', true, true) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(
                sa.bindparam("code", value=code),
                sa.bindparam("resource", value=resource),
                sa.bindparam("action", value=action),
            )
        )

    op.add_column(
        "tenant_entitlements",
        sa.Column(
            "allow_create_assistant",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "tenant_entitlements",
        sa.Column(
            "allow_own_provider_credentials",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # The brief is *not* copied back onto `ai_assistants`. Those rows still
    # hold whatever they held before the upgrade -- the backfill read them and
    # never wrote to them -- so writing the tenant's value back would overwrite
    # an assistant's own brief with a tenant-wide one it never had.
    op.drop_column("tenant_chatbot_settings", "response_length")
    op.drop_column("tenant_chatbot_settings", "personality")
    op.drop_column("tenant_chatbot_settings", "avoid_instructions")
    op.drop_column("tenant_chatbot_settings", "role_instructions")
