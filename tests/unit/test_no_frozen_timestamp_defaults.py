"""No column may default to the string "now()".

`server_default="now()"` is quoted by SQLAlchemy, so PostgreSQL evaluates it
once, when the table is created, and every later row gets that frozen
instant. Eleven columns carried it until migration c7f2a9e3d815 -- every role
grant recorded the moment the database was built. `func.now()` is the form
that stays a function call.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import iam_platform.infrastructure.db.models as models_package
from iam_platform.infrastructure.db.base import Base

# The package's __init__ imports none of its modules, so each is imported here
# -- otherwise the metadata is empty and this test passes by checking nothing.
for _module in pkgutil.iter_modules(models_package.__path__):
    importlib.import_module(f"{models_package.__name__}.{_module.name}")

pytestmark = pytest.mark.unit


def test_every_table_was_actually_scanned() -> None:
    assert len(Base.metadata.sorted_tables) > 40


def test_no_server_default_is_a_quoted_function_call() -> None:
    offenders = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            default = column.server_default
            arg = getattr(default, "arg", None)
            if isinstance(arg, str) and arg.strip().lower().endswith("()"):
                offenders.append(f"{table.name}.{column.name} = {arg!r}")
    assert offenders == []
