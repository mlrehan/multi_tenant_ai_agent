"""Tenant entitlements, provider configuration, chatbot settings, teams and handoff.

Revision ID: c9f2a4d81b57
Revises: b8e2f4a71c93

Six related changes, in one migration because they are one feature and a
half-applied subset leaves the platform inconsistent (entitlements with no
chatbot settings to bound, or a handoff state with no team to route to).

**The one destructive-looking step, and why it is not.** `conversations`
loses `NOT NULL` on `membership_id` and `assistant_id`. That is not a
loosening: a CHECK constraint replaces it, requiring *exactly one* of
`membership_id` and `visitor_session_id`, which the old NOT NULL could not
express -- it permitted a row claiming to be owned by a member and a visitor
at once. Every existing row satisfies the new constraint unchanged (they all
have a membership and no visitor session), verified by the migration itself:
the CHECK is added without NOT VALID, so Postgres refuses the migration rather
than accepting bad data if that assumption is wrong.

**`tenant_entitlements` is readable but not writable by tenants**, and that
needs two mechanisms because neither alone says it. `ALTER DEFAULT PRIVILEGES`
has granted `app_tenant` full CRUD on every new table since Phase 5, so the
grant is narrowed explicitly here (RLS cannot express "no writes at all"), and
an RLS policy scopes the read to the tenant's own row. Third recurrence of the
Phase 8 `audit_logs` lesson: default privileges are granted once and never
narrowed unless a migration deliberately does it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9f2a4d81b57"
down_revision = "b8e2f4a71c93"
branch_labels = None
depends_on = None

#: The pooled-connection safe form. `current_setting(..., true)` returns '' --
#: not NULL -- on a reused connection whose previous transaction set the value,
#: and a bare ::uuid cast on '' raises instead of cleanly denying (docs/18).
_TENANT_MATCHES = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

def _drop_check(table: str, name: str) -> None:
    """Drops a CHECK whose stored name may carry a doubled prefix.

    See the note at the `conversation_messages` call site. Both spellings are
    attempted with IF EXISTS so this works against the live database (doubled)
    and against one built from scratch by a future squashed baseline (single).
    """
    for candidate in (f"ck_{table}_ck_{table}_{name}", f"ck_{table}_{name}", name):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {candidate}")


_NEW_TENANT_TABLES = (
    "tenant_entitlements",
    "tenant_chatbot_settings",
    "tenant_teams",
    "tenant_team_members",
)


def upgrade() -> None:
    _create_tenant_entitlements()
    _create_tenant_chatbot_settings()
    _create_teams()
    _extend_model_configurations()
    _extend_assistants_and_widgets()
    _extend_conversations_and_messages()
    _extend_data_sources()
    _apply_rls()


# ---------------------------------------------------------------------------


def _create_tenant_entitlements() -> None:
    op.create_table(
        "tenant_entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # NULL => uncapped, deliberately distinct from 0 ("none at all").
        sa.Column("max_knowledge_bases", sa.Integer()),
        sa.Column("max_chat_widgets", sa.Integer()),
        sa.Column("max_messages_per_day", sa.Integer()),
        sa.Column("max_tokens_per_month", sa.BigInteger()),
        sa.Column(
            "allow_own_provider_credentials",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "allow_create_assistant",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "allow_invite_members",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "allow_create_roles",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_entitlements_tenant"),
        sa.CheckConstraint(
            "max_knowledge_bases IS NULL OR max_knowledge_bases >= 0",
            name="max_knowledge_bases_nonneg",
        ),
        sa.CheckConstraint(
            "max_chat_widgets IS NULL OR max_chat_widgets >= 0",
            name="max_chat_widgets_nonneg",
        ),
        sa.CheckConstraint(
            "max_messages_per_day IS NULL OR max_messages_per_day >= 0",
            name="max_messages_per_day_nonneg",
        ),
        sa.CheckConstraint(
            "max_tokens_per_month IS NULL OR max_tokens_per_month >= 0",
            name="max_tokens_per_month_nonneg",
        ),
    )
    op.create_index("ix_tenant_entitlements_tenant_id", "tenant_entitlements", ["tenant_id"])

    # Every existing tenant gets the documented defaults rather than silently
    # escaping every limit. Written as a real row (not left to the
    # application's in-memory default) so a platform operator opening the
    # screen sees the numbers that are actually being enforced.
    op.execute(
        """
        INSERT INTO tenant_entitlements (
            id, tenant_id, max_knowledge_bases, max_chat_widgets,
            max_messages_per_day, max_tokens_per_month
        )
        SELECT gen_random_uuid(), id, 1, 1, 1000, 100000 FROM tenants
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )


def _create_tenant_chatbot_settings() -> None:
    op.create_table(
        "tenant_chatbot_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ai_chatbot_enabled", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        # Chatbot-facing only. Renaming the company here must never rename the
        # tenant: `tenants.display_name` is account identity, this is what the
        # bot calls the company when talking to a parent.
        sa.Column("company_name", sa.Text()),
        sa.Column(
            "company_description", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column(
            "industry", sa.Text(), nullable=False,
            server_default=sa.text(
                "'Early Years Education and Childcare (UK Nursery / Preschool)'"
            ),
        ),
        sa.Column(
            "allow_human_handoff", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "add_ai_summary_as_internal_comment", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "allow_ai_for_unassigned_conversations", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        # NULL => inherit the platform ceiling. A stored value can only ever
        # lower it; the domain enforces that on read as well as write, so a
        # row written before the write-side check existed still cannot raise it.
        sa.Column("daily_message_limit", sa.Integer()),
        sa.Column(
            "share_visitor_location", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_chatbot_settings_tenant"),
        sa.CheckConstraint(
            "length(company_description) <= 2000", name="company_description_bounded"
        ),
        sa.CheckConstraint("length(industry) <= 100", name="industry_bounded"),
        sa.CheckConstraint(
            "daily_message_limit IS NULL OR daily_message_limit >= 0",
            name="daily_message_limit_nonneg",
        ),
    )
    op.create_index(
        "ix_tenant_chatbot_settings_tenant_id", "tenant_chatbot_settings", ["tenant_id"]
    )


def _create_teams() -> None:
    op.create_table(
        "tenant_teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_teams_name"),
        # Required before `conversations.(tenant_id, assigned_team_id)` can
        # reference it: a composite FK needs a matching UNIQUE on the
        # referenced side. Postgres refuses the FK outright without this --
        # the fifth time this rule has bitten this schema.
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_teams_tenant_id_id"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 100", name="team_name_bounded"),
    )
    op.create_index("ix_tenant_teams_tenant_id", "tenant_teams", ["tenant_id"])

    op.create_table(
        "tenant_team_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("team_id", "membership_id", name="uq_tenant_team_members_pair"),
        # Composite both ways: a membership from another tenant cannot staff
        # this tenant's team whatever id the request carries.
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            ["tenant_teams.tenant_id", "tenant_teams.id"],
            name="fk_tenant_team_members_team",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_tenant_team_members_membership",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_tenant_team_members_tenant_id", "tenant_team_members", ["tenant_id"])
    op.create_index("ix_tenant_team_members_team_id", "tenant_team_members", ["team_id"])


def _extend_model_configurations() -> None:
    """Provider configuration, replacing deployment-wide `OPENAI__*` env vars.

    Every column is nullable with `.env` as the fallback, so this migration
    changes no behaviour on its own -- an existing deployment keeps answering
    with exactly the model it answered with yesterday until an operator fills
    a configuration in.
    """
    op.add_column(
        "model_configurations",
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("'openai'")),
    )
    # Encrypted at rest under a key held in the environment, never here.
    op.add_column(
        "model_configurations", sa.Column("api_key_ciphertext", sa.LargeBinary())
    )
    op.add_column("model_configurations", sa.Column("api_key_hint", sa.Text()))
    op.add_column("model_configurations", sa.Column("embedding_model", sa.Text()))
    op.add_column("model_configurations", sa.Column("embedding_dimensions", sa.Integer()))
    op.add_column("model_configurations", sa.Column("chat_model", sa.Text()))
    op.add_column(
        "model_configurations", sa.Column("request_timeout_seconds", sa.Integer())
    )
    op.add_column("model_configurations", sa.Column("chat_reasoning_effort", sa.Text()))
    op.add_column(
        "model_configurations",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_check_constraint(
        "provider_valid",
        "model_configurations",
        "provider IN ('openai','anthropic','gemini','xai')",
    )
    op.create_check_constraint(
        "embedding_dimensions_bounded",
        "model_configurations",
        "embedding_dimensions IS NULL OR embedding_dimensions BETWEEN 1 AND 8192",
    )
    op.create_check_constraint(
        "request_timeout_bounded",
        "model_configurations",
        "request_timeout_seconds IS NULL OR request_timeout_seconds BETWEEN 1 AND 600",
    )


def _extend_assistants_and_widgets() -> None:
    op.add_column("ai_assistants", sa.Column("role_instructions", sa.Text()))
    op.add_column("ai_assistants", sa.Column("avoid_instructions", sa.Text()))
    op.add_column(
        "ai_assistants",
        sa.Column("personality", sa.Text(), nullable=False, server_default=sa.text("'neutral'")),
    )
    op.add_column(
        "ai_assistants",
        sa.Column(
            "response_length", sa.Text(), nullable=False, server_default=sa.text("'balanced'")
        ),
    )
    # The caps are constraints, not just validation: the prompt builder must
    # never have to silently truncate tenant-authored instructions.
    op.create_check_constraint(
        "role_instructions_bounded",
        "ai_assistants",
        "role_instructions IS NULL OR length(role_instructions) <= 1000",
    )
    op.create_check_constraint(
        "avoid_instructions_bounded",
        "ai_assistants",
        "avoid_instructions IS NULL OR length(avoid_instructions) <= 1000",
    )
    op.create_check_constraint(
        "personality_valid",
        "ai_assistants",
        "personality IN ('neutral','friendly','reassuring','professional')",
    )
    op.create_check_constraint(
        "response_length_valid",
        "ai_assistants",
        "response_length IN ('concise','balanced','detailed')",
    )

    op.add_column("chat_widgets", sa.Column("assistant_id", postgresql.UUID(as_uuid=True)))
    op.add_column("chat_widgets", sa.Column("chatbot_name", sa.Text()))
    op.add_column("chat_widgets", sa.Column("chatbot_title", sa.Text()))
    op.add_column("chat_widgets", sa.Column("avatar_key", sa.Text()))
    op.add_column("chat_widgets", sa.Column("greeting", sa.Text()))
    op.add_column(
        "chat_widgets",
        sa.Column(
            "show_quick_reply_suggestions",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # Composite, so a widget cannot be pointed at another tenant's assistant.
    op.create_foreign_key(
        "fk_chat_widgets_assistant",
        "chat_widgets",
        "ai_assistants",
        ["tenant_id", "assistant_id"],
        ["tenant_id", "id"],
    )


def _extend_conversations_and_messages() -> None:
    op.add_column("conversations", sa.Column("visitor_session_id", postgresql.UUID(as_uuid=True)))
    op.add_column("conversations", sa.Column("widget_id", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "conversations",
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'ai_active'")),
    )
    op.add_column("conversations", sa.Column("assigned_team_id", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "conversations", sa.Column("assigned_membership_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column("conversations", sa.Column("handoff_reason", sa.Text()))
    op.add_column("conversations", sa.Column("handoff_at", sa.DateTime(timezone=True)))
    op.add_column("conversations", sa.Column("handoff_initiated_by", sa.Text()))
    op.add_column("conversations", sa.Column("claimed_at", sa.DateTime(timezone=True)))

    # See the module docstring: this is a *narrowing* despite dropping NOT
    # NULL, because the CHECK below forbids the both-owners row the old
    # constraint permitted.
    op.alter_column("conversations", "membership_id", nullable=True)
    op.alter_column("conversations", "assistant_id", nullable=True)
    op.create_check_constraint(
        "exactly_one_owner",
        "conversations",
        "(membership_id IS NOT NULL AND visitor_session_id IS NULL) OR "
        "(membership_id IS NULL AND visitor_session_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "state_valid",
        "conversations",
        "state IN ('ai_active','handoff_requested','unassigned','assigned',"
        "'human_active','resolved')",
    )
    op.create_check_constraint(
        "handoff_initiated_by_valid",
        "conversations",
        "handoff_initiated_by IS NULL OR handoff_initiated_by IN ('visitor','ai','agent')",
    )
    op.create_foreign_key(
        "fk_conversations_assigned_team",
        "conversations",
        "tenant_teams",
        ["tenant_id", "assigned_team_id"],
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        "fk_conversations_assigned_membership",
        "conversations",
        "tenant_memberships",
        ["tenant_id", "assigned_membership_id"],
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        "fk_conversations_widget",
        "conversations",
        "chat_widgets",
        ["tenant_id", "widget_id"],
        ["tenant_id", "id"],
    )
    # Partial: the Unassigned inbox asks about one state and no other, so an
    # index covering every conversation would be mostly dead weight.
    op.execute(
        """
        CREATE INDEX ix_conversations_unassigned
            ON conversations (tenant_id, assigned_team_id, handoff_at)
            WHERE state = 'unassigned'
        """
    )

    op.add_column(
        "conversation_messages",
        sa.Column("author_membership_id", postgresql.UUID(as_uuid=True)),
    )
    # Dropped by literal name, not through `op.drop_constraint`. This
    # metadata's naming convention is `ck_%(table_name)s_%(constraint_name)s`
    # and the *original* migrations passed names that were already prefixed,
    # so the stored names are doubled (`ck_x_ck_x_role_valid`). Letting Alembic
    # render the name here produces the single-prefixed form, which does not
    # exist -- the migration then fails halfway through. Rendering both forms
    # with IF EXISTS is the only version that works against this database and
    # a freshly built one.
    _drop_check("conversation_messages", "role_valid")
    op.create_check_constraint(
        "role_valid",
        "conversation_messages",
        "role IN ('user','assistant','agent_message','internal_comment','system_event')",
    )


def _extend_data_sources() -> None:
    op.add_column("data_sources", sa.Column("name", sa.Text()))
    op.add_column("data_sources", sa.Column("direct_text", sa.Text()))
    _drop_check("data_sources", "kind_valid")
    op.create_check_constraint(
        "kind_valid",
        "data_sources",
        "kind IN ('upload','url_crawl','integration_sync','direct_text')",
    )
    op.create_check_constraint(
        "direct_text_bounded",
        "data_sources",
        "kind <> 'direct_text' OR "
        "(direct_text IS NOT NULL AND length(direct_text) BETWEEN 1 AND 5000)",
    )
    # `url_crawl_needs_urls` reads `config -> 'urls'`, which a direct-text row
    # does not have. Rewritten so the two kinds do not constrain each other --
    # left alone, inserting direct text would fail a crawl-shaped check.
    _drop_check("data_sources", "url_crawl_needs_urls")
    op.create_check_constraint(
        "url_crawl_needs_urls",
        "data_sources",
        "kind <> 'url_crawl' OR jsonb_array_length(config -> 'urls') > 0",
    )


def _apply_rls() -> None:
    for table in _NEW_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # Three of the four are ordinary tenant-owned tables: the tenant reads and
    # writes its own rows and nothing else.
    for table in ("tenant_chatbot_settings", "tenant_teams", "tenant_team_members"):
        op.execute(
            f"""
            CREATE POLICY {table}_isolation ON {table}
            USING ({_TENANT_MATCHES}) WITH CHECK ({_TENANT_MATCHES})
            """
        )

    # `tenant_entitlements` is the exception, and needs *two* mechanisms.
    #
    # RLS says which rows: the tenant's own. It cannot say "and no writes" --
    # a policy governs row visibility, not the set of permitted commands. So
    # the grant does that half, and it has to REVOKE first because
    # `ALTER DEFAULT PRIVILEGES` handed `app_tenant` full CRUD when the table
    # was created moments ago. Without the REVOKE a tenant admin could raise
    # their own limits, which is the one thing this table exists to prevent.
    op.execute(
        f"""
        CREATE POLICY tenant_entitlements_read ON tenant_entitlements
        FOR SELECT USING ({_TENANT_MATCHES})
        """
    )
    op.execute("REVOKE INSERT, UPDATE, DELETE ON tenant_entitlements FROM app_tenant")
    # The platform role writes them; it holds BYPASSRLS, so no policy is
    # needed for it and adding one would be misleading.


def downgrade() -> None:
    for table in _NEW_TENANT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    _drop_check("data_sources", "kind_valid")
    op.create_check_constraint(
        "kind_valid", "data_sources", "kind IN ('upload','url_crawl','integration_sync')"
    )
    _drop_check("data_sources", "direct_text_bounded")
    op.drop_column("data_sources", "direct_text")
    op.drop_column("data_sources", "name")

    _drop_check("conversation_messages", "role_valid")
    op.create_check_constraint(
        "role_valid", "conversation_messages", "role IN ('user','assistant')"
    )
    op.drop_column("conversation_messages", "author_membership_id")

    op.execute("DROP INDEX IF EXISTS ix_conversations_unassigned")
    for name in ("exactly_one_owner", "state_valid", "handoff_initiated_by_valid"):
        _drop_check("conversations", name)
    op.drop_constraint("fk_conversations_widget", "conversations", type_="foreignkey")
    for column in (
        "claimed_at",
        "handoff_initiated_by",
        "handoff_at",
        "handoff_reason",
        "assigned_membership_id",
        "assigned_team_id",
        "state",
        "widget_id",
        "visitor_session_id",
    ):
        op.drop_column("conversations", column)
    # Restoring NOT NULL is only safe once visitor conversations are gone --
    # they are, because the CHECK above guaranteed every remaining row has a
    # membership.
    op.execute("DELETE FROM conversations WHERE membership_id IS NULL")
    op.alter_column("conversations", "membership_id", nullable=False)
    op.alter_column("conversations", "assistant_id", nullable=False)

    op.drop_constraint("fk_chat_widgets_assistant", "chat_widgets", type_="foreignkey")
    for column in (
        "show_quick_reply_suggestions",
        "greeting",
        "avatar_key",
        "chatbot_title",
        "chatbot_name",
        "assistant_id",
    ):
        op.drop_column("chat_widgets", column)

    for name in (
        "role_instructions_bounded",
        "avoid_instructions_bounded",
        "personality_valid",
        "response_length_valid",
    ):
        _drop_check("ai_assistants", name)
    for column in ("response_length", "personality", "avoid_instructions", "role_instructions"):
        op.drop_column("ai_assistants", column)

    for name in ("provider_valid", "embedding_dimensions_bounded", "request_timeout_bounded"):
        _drop_check("model_configurations", name)
    for column in (
        "enabled",
        "chat_reasoning_effort",
        "request_timeout_seconds",
        "chat_model",
        "embedding_dimensions",
        "embedding_model",
        "api_key_hint",
        "api_key_ciphertext",
        "provider",
    ):
        op.drop_column("model_configurations", column)
