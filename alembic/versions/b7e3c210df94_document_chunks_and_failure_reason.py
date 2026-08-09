"""document_chunks table + documents.failure_reason

Phase 11 (knowledge-base ingestion). Adds:

- ``document_chunks`` -- the record of truth for what was indexed. Qdrant is a
  search index that can be dropped and rebuilt from these rows without
  re-parsing the original documents, so the tenant-owned copy of the chunk
  text lives here under RLS like every other tenant-owned row.
- ``documents.failure_reason`` -- why ingestion failed, surfaced to the tenant
  so a bad upload is self-diagnosable rather than an opaque red badge.

RLS follows the standard tenant-owned template from
docs/18-schema-rls-and-migrations.md, including the ``NULLIF(...)`` guard that
a pooled connection needs (a reused connection reports ``''``, not ``NULL``,
for a GUC an earlier transaction set -- a bare ``::uuid`` cast raises
``invalid input syntax for type uuid: ""`` on the second transaction).

Revision ID: b7e3c210df94
Revises: a4d2f81c9b30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7e3c210df94"
down_revision = "a4d2f81c9b30"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column("documents", sa.Column("failure_reason", sa.Text(), nullable=True))

    # `document_chunks` is the first table to reference `documents` by the
    # composite (tenant_id, id) key, and PostgreSQL requires a matching UNIQUE
    # constraint on the *referenced* columns. Every other composite-FK target
    # in this schema already carries one (`uq_knowledge_bases_tenant_id_id`,
    # `uq_ai_assistants_tenant_id_id`, ...); `documents` had simply never been
    # a target before, so it was never given one.
    #
    # The composite form is deliberate, not incidental: a plain
    # `document_id`-only FK would let a chunk row name a document from another
    # tenant, and the mismatch would only be caught by RLS at read time rather
    # than refused at write time (docs/10-schema-conventions.md).
    op.create_unique_constraint(
        "uq_documents_tenant_id_id", "documents", ["tenant_id", "id"]
    )

    op.create_table(
        "document_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("source_location", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            # `func.now()`, never the string "now()" -- a bare Python string
            # becomes a frozen literal default instead of a live function call
            # (the Phase 5 pitfall recorded in docs/18).
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_chunks_document",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "uq_document_chunks_document_ordinal",
        "document_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )
    op.create_index("ix_document_chunks_tenant_id", "document_chunks", ["tenant_id"])
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index(
        "ix_document_chunks_knowledge_base_id", "document_chunks", ["knowledge_base_id"]
    )

    op.execute("ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY document_chunks_isolation ON document_chunks "
        f"USING (tenant_id = {_TENANT_ID_EXPR}) "
        f"WITH CHECK (tenant_id = {_TENANT_ID_EXPR})"
    )

    # The app role needs DML here like any other tenant-owned table. Explicit
    # rather than relying on ALTER DEFAULT PRIVILEGES, so the grant is
    # reviewable in the migration that creates the table.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON document_chunks TO app_tenant")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON document_chunks TO app_platform")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS document_chunks_isolation ON document_chunks")
    op.drop_index("ix_document_chunks_knowledge_base_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_tenant_id", table_name="document_chunks")
    op.drop_index("uq_document_chunks_document_ordinal", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_constraint("uq_documents_tenant_id_id", "documents", type_="unique")
    op.drop_column("documents", "failure_reason")
