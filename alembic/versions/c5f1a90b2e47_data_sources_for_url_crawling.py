"""data_sources for URL and website crawling

Phase 12. Creates the `data_sources` table docs/16 specified in Phase 3 but
which was never built (Phase 7 deferred it), **plus the columns crawling
actually needs and the spec does not have**: the Phase 3 shape has `kind`,
`knowledge_base_id`, `sync_status` and `last_synced_at`, and nowhere to record
*what to crawl*. `kind='url_crawl'` was unsatisfiable as specified. See
`DataSourceModel` for the full reasoning.

Also adds two columns to `documents`, because a crawled page is a `documents`
row like any other and needs to say where it came from:

- `data_source_id` -- which crawl produced it, so a re-crawl replaces its own
  previous pages rather than accumulating a second copy of the site, and so
  deleting a source can find its documents.
- `source_url` -- the page's address, for citation. Deliberately not folded
  into `filename`: that field is a display name shown in a list, while this is
  a link a person may follow, and conflating them means neither can change
  independently.

RLS follows docs/18-schema-rls-and-migrations.md, including the `NULLIF(...)`
guard for the pooled-connection empty-string case.

Revision ID: c5f1a90b2e47
Revises: b7e3c210df94
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c5f1a90b2e47"
down_revision = "b7e3c210df94"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        # No FK: `integrations` is still unbuilt, and a constraint cannot
        # reference a table that does not exist. Added with that table.
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "sync_status", sa.Text(), nullable=False, server_default=sa.text("'idle'")
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "pages_discovered", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "pages_indexed", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_membership_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        # `func.now()`, never the bare string "now()" -- docs/18's pitfall: a
        # plain string becomes a frozen literal default evaluated once at DDL
        # time, so every row would share the migration's timestamp.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('upload','url_crawl','integration_sync')",
            name="ck_data_sources_kind_valid",
        ),
        sa.CheckConstraint(
            "sync_status IN ('idle','syncing','ready','error')",
            name="ck_data_sources_sync_status_valid",
        ),
        # A crawl with no URLs is a row that can never do anything. The domain
        # entity refuses it too; this means a fixture or a hand-written INSERT
        # cannot bypass that.
        sa.CheckConstraint(
            "kind <> 'url_crawl' OR jsonb_array_length(config -> 'urls') > 0",
            name="ck_data_sources_url_crawl_needs_urls",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_data_sources_knowledge_base",
        ),
        # `documents.(tenant_id, data_source_id)` references this pair, and a
        # composite FK requires a matching UNIQUE on the referenced side.
        # Postgres refuses the FK below without it -- the same finding as
        # Phase 11's `uq_documents_tenant_id_id`, hit again here.
        sa.UniqueConstraint("tenant_id", "id", name="uq_data_sources_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_data_sources_created_by",
        ),
    )
    op.create_index("ix_data_sources_tenant_id", "data_sources", ["tenant_id"])
    op.create_index(
        "ix_data_sources_knowledge_base_id", "data_sources", ["knowledge_base_id"]
    )

    op.execute("ALTER TABLE data_sources ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY data_sources_isolation ON data_sources "
        f"USING (tenant_id = {_TENANT_ID_EXPR}) "
        f"WITH CHECK (tenant_id = {_TENANT_ID_EXPR})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON data_sources TO app_tenant")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON data_sources TO app_platform")

    # --- documents gains its crawl provenance -------------------------------
    op.add_column(
        "documents",
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("documents", sa.Column("source_url", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_documents_data_source",
        "documents",
        "data_sources",
        ["tenant_id", "data_source_id"],
        ["tenant_id", "id"],
        # A crawl source can be deleted without destroying the pages it
        # already produced -- they are real indexed content the tenant may
        # still be relying on. They simply lose their link back.
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_data_source_id",
        "documents",
        ["data_source_id"],
        postgresql_where=sa.text("data_source_id IS NOT NULL"),
    )
    # A crawl must not index the same page twice, and a re-crawl must update
    # rather than duplicate. Partial, for the same reason `storage_path`'s
    # uniqueness is: a soft-deleted document must not permanently reserve a URL.
    op.create_index(
        "uq_documents_source_url_per_kb",
        "documents",
        ["knowledge_base_id", "source_url"],
        unique=True,
        postgresql_where=sa.text("source_url IS NOT NULL AND deleted_at IS NULL"),
    )

    # `data_sources.tenant_id` must match the FK target's; the composite FK
    # above enforces it for documents -> data_sources. Nothing further needed.


def downgrade() -> None:
    op.drop_index("uq_documents_source_url_per_kb", table_name="documents")
    op.drop_index("ix_documents_data_source_id", table_name="documents")
    op.drop_constraint("fk_documents_data_source", "documents", type_="foreignkey")
    op.drop_column("documents", "source_url")
    op.drop_column("documents", "data_source_id")

    op.execute("DROP POLICY IF EXISTS data_sources_isolation ON data_sources")
    op.drop_index("ix_data_sources_knowledge_base_id", table_name="data_sources")
    op.drop_index("ix_data_sources_tenant_id", table_name="data_sources")
    op.drop_table("data_sources")
