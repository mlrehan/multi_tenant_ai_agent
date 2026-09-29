"""What each unit of AI usage cost: the model it used, and the price of that model.

Two things the ledger could not answer, and a cost needs both:

* **Which model was paid for.** A row recorded tokens and, at most, a model
  *configuration*; the platform default -- every widget answer -- is not a
  configuration, so most rows named no model at all. `chat_model` and
  `embedding_model` are now written by the adapters themselves, with the model
  actually sent to the provider.
* **Which input tokens were embeddings.** One answer spends tokens embedding
  the question and tokens prompting the chat model, priced very differently,
  and both were summed into `input_tokens`. `embedding_tokens` is the part of
  `input_tokens` that was embedding; `input_tokens` keeps its meaning, so every
  existing dashboard figure is unchanged.

**Backfill, facts only.** An `ingestion` row is embeddings by construction, so
its `embedding_tokens` is its `input_tokens`. Nothing else is inferred: the
model a past row used was never recorded, and guessing it from today's
settings would price history at whatever the configuration says now. Those
rows are reported as *unpriced*, not as free.

**`ai_model_prices`** is platform-owned price history, entered by an operator:
USD per million input and output tokens, effective from a moment. A row is
priced at its model's price in force when it occurred, so a price change never
rewrites last month's cost. Tenants have no access to it at all -- what the
platform pays its provider is not tenant information. `app_platform` may add
and delete (a mistaken entry is removed and re-entered, audited), never update:
editing a price in place would silently change costs already reported.

Revision ID: e7a3c1f9d2b4
Revises: d9e4b7a1c6f2
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e7a3c1f9d2b4"
down_revision = "d9e4b7a1c6f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_usage_events", sa.Column("embedding_tokens", sa.BigInteger(), nullable=True))
    op.add_column("ai_usage_events", sa.Column("chat_model", sa.Text(), nullable=True))
    op.add_column("ai_usage_events", sa.Column("embedding_model", sa.Text(), nullable=True))
    op.create_check_constraint(
        "embedding_tokens_valid",
        "ai_usage_events",
        "embedding_tokens IS NULL OR (embedding_tokens >= 0 AND embedding_tokens <= input_tokens)",
    )
    op.execute(
        "UPDATE ai_usage_events SET embedding_tokens = input_tokens "
        "WHERE channel = 'ingestion' AND embedding_tokens IS NULL"
    )

    op.create_table(
        "ai_model_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("input_usd_per_million", sa.Numeric(14, 6), nullable=False),
        sa.Column("output_usd_per_million", sa.Numeric(14, 6), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "length(model_name) BETWEEN 1 AND 200", name="model_name_length"
        ),
        sa.CheckConstraint(
            "input_usd_per_million >= 0 AND output_usd_per_million >= 0",
            name="non_negative",
        ),
        sa.UniqueConstraint(
            "model_name", "effective_from", name="uq_ai_model_prices_model_effective"
        ),
    )
    # Serves the per-row "price in force at this moment" lookup.
    op.create_index(
        "ix_ai_model_prices_lookup",
        "ai_model_prices",
        ["model_name", sa.text("effective_from DESC")],
    )
    op.execute("REVOKE ALL ON ai_model_prices FROM app_tenant")
    op.execute("REVOKE UPDATE ON ai_model_prices FROM app_platform")


def downgrade() -> None:
    op.drop_index("ix_ai_model_prices_lookup", table_name="ai_model_prices")
    op.drop_table("ai_model_prices")
    op.drop_constraint("embedding_tokens_valid", "ai_usage_events", type_="check")
    op.drop_column("ai_usage_events", "embedding_model")
    op.drop_column("ai_usage_events", "chat_model")
    op.drop_column("ai_usage_events", "embedding_tokens")
