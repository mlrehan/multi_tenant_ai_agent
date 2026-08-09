"""Builds the real FastAPI app (real Postgres, real Redis, real crypto) and
exposes it via an ASGI-transport httpx client -- these tests exercise actual
HTTP request/response semantics (status codes, JSON bodies, headers), not
just the application layer directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from iam_platform.api.main import create_app
from iam_platform.bootstrap import build_container
from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.base import Base
from iam_platform.infrastructure.db.models import audit as _audit_models  # noqa: F401
from iam_platform.infrastructure.db.models import identity as _identity_models  # noqa: F401
from iam_platform.infrastructure.db.session import build_engine_from_dsn

pytestmark = pytest.mark.integration

_ALL_TABLES = [t.name for t in Base.metadata.sorted_tables]


class CapturingEmailSender:
    """Swapped in for the real (console-logging) sender so tests can read the
    raw verification/reset token without needing an actual mailbox."""

    def __init__(self) -> None:
        self.last_verification_token: str | None = None
        self.last_reset_token: str | None = None

    async def send_verification_email(self, *, to: str, token: str) -> None:
        self.last_verification_token = token

    async def send_password_reset_email(self, *, to: str, token: str) -> None:
        self.last_reset_token = token


@pytest.fixture
def email_sender() -> CapturingEmailSender:
    return CapturingEmailSender()


@pytest_asyncio.fixture
async def client(email_sender: CapturingEmailSender) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings()  # type: ignore[call-arg]
    # Async since Phase 9 -- build_container resolves `secret://` references
    # through the active SecretProvider before wiring anything.
    container = await build_container(settings)
    container.email_sender = email_sender
    app = create_app(container)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        # `build_container` opens two Postgres pools, a Redis connection pool
        # and an HTTP client, and this fixture runs once per test. The app's
        # lifespan normally releases them, but the fixture constructs the
        # container directly and never enters it -- so without this the suite
        # leaked all four on every test.
        #
        # That is not a tidiness point: by the back half of a ~290-test run the
        # leaked pools exhaust Redis and Postgres, and tests start failing with
        # `redis.exceptions.TimeoutError` and 503s from `/readyz` in places
        # that have nothing to do with what they're testing. Several failures
        # chased as logic bugs were this.
        await container.shutdown()

        # TRUNCATE requires table-owner privilege; app_tenant deliberately
        # doesn't have it (least privilege) -- use the migrator connection for
        # teardown, same fix as tests/integration/conftest.py.
        cleanup_engine = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)
        async with cleanup_engine.begin() as conn:
            table_list = ", ".join(_ALL_TABLES)
            await conn.execute(text(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE"))
        await cleanup_engine.dispose()
