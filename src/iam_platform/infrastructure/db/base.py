"""Declarative base + shared column conventions -- docs/10-schema-conventions.md."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention so every constraint/index gets a deterministic
# name (ix_/uq_/fk_/ck_<table>_<columns>) instead of a driver-generated one --
# required for Alembic autogenerate to produce stable, diffable migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Every bare `Mapped[datetime]` column becomes TIMESTAMPTZ, matching
    # docs/10-schema-conventions.md and the domain layer's use of
    # timezone-aware datetimes throughout (SystemClock/FixedClock both
    # produce tz-aware values) -- without this, SQLAlchemy's default mapping
    # is `TIMESTAMP WITHOUT TIME ZONE`, which asyncpg rejects tz-aware
    # values against at insert time (caught by integration testing).
    type_annotation_map = {datetime: DateTime(timezone=True)}


class TimestampMixin:
    """``created_at``/``updated_at`` per docs/10-schema-conventions.md.

    ``updated_at`` is maintained via SQLAlchemy's ``onupdate`` (evaluated
    server-side through ``func.now()``) rather than a database trigger --
    equivalent for this codebase since every write goes through the ORM, and
    it avoids a stored procedure in the migration for no added benefit here.
    """

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
