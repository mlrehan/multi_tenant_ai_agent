"""Fixtures for the security-validation suite.

Re-exports the integration suite's engine fixtures rather than redefining
them: these tests need exactly the same three connections (RLS-subject,
BYPASSRLS, table-owner) against the same dev database, and a second
hand-rolled copy would be one more thing to keep in sync with
``core.config.Settings``.

The plain ``import *`` is deliberate -- pytest discovers fixtures by name in
the module namespace, so re-exporting is how a conftest inherits from a
sibling package's conftest without a plugin.
"""

from __future__ import annotations

from tests.integration.conftest import (  # noqa: F401
    engine,
    migrator_engine,
    platform_engine,
    platform_uow_factory,
    settings,
    tenant_uow_factory,
    uow_factory,
)
