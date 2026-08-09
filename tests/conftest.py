"""Forces the whole suite onto a dedicated test database, and refuses to run
if that redirection ever fails.

**Why this file exists.** Teardown in `tests/integration/conftest.py` and
`tests/api/conftest.py` runs `TRUNCATE` across every table. Read the DSN from
`.env` like the application does — which is what those fixtures did, for the
good reason that it exercises the real pooling configuration — and the suite
wipes whatever database the developer is actually using. That happened three
times during Phases 11–12: real accounts, tenants and knowledge bases,
destroyed by a routine test run, with the mechanism documented and warned
about each time and repeated anyway.

Documenting a hazard does not remove it. This does.

**How.** `DATABASE__NAME` is set in the process environment at import time,
before pytest collects anything and therefore before any `Settings()` is
constructed. Every construction site picks it up — including ones added later
that nobody thought to update, which is the property a per-fixture override
would not have.

**The guard is the point.** Redirection alone is a convention, and conventions
fail silently. `_assert_isolated_database` runs once at session start and stops
the run outright if the resolved database is not the test one, so a future
change that breaks the redirection fails loudly on the first test rather than
quietly on the developer's data.
"""

from __future__ import annotations

import os

import pytest

#: Deliberately a distinct database, not a schema or a prefix. Postgres
#: TRUNCATE, RLS policies and role grants are all database-scoped here, so a
#: separate database is the only boundary that makes the suite's teardown
#: incapable of touching development data.
TEST_DATABASE_NAME = "iam_platform_test"

# Set before pytest imports any test module, so no `Settings()` anywhere can be
# built against the development database. An `os.environ` write is unusual in a
# conftest and justified here: it is the only interception point that precedes
# every construction site rather than the ones a fixture happens to cover.
os.environ["DATABASE__NAME"] = TEST_DATABASE_NAME


@pytest.fixture(scope="session", autouse=True)
def _assert_isolated_database() -> None:
    """Refuses to run the suite against anything but the test database.

    Runs before the first test. If the redirection above ever stops working --
    a fixture that hardcodes a DSN, a `Settings(_env_file=...)` that re-reads
    `.env`, an environment that pre-sets `DATABASE__NAME` -- this stops the run
    instead of letting teardown truncate real data.
    """
    from iam_platform.core.config import Settings

    resolved = Settings().database.name  # type: ignore[call-arg]
    if resolved != TEST_DATABASE_NAME:
        pytest.exit(
            f"refusing to run: tests resolve to database {resolved!r}, not "
            f"{TEST_DATABASE_NAME!r}. The suite TRUNCATEs every table, so "
            "running it against a development database destroys real data. "
            "Check tests/conftest.py and any fixture that builds its own DSN.",
            returncode=1,
        )
