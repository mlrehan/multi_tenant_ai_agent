"""Threat-model scenario 8, second half: per-job authorization re-validation.

Phase 8 could only cover the *mechanism* this defense rests on (transaction-
scoped ``set_config``, proven by the pool-reuse test) because no ``workers/``
runtime existed to attack. Phase 11 builds one, so the actual property is now
testable: **a job whose authorization was revoked between enqueue and
execution must be refused at execution time.**

Every test here runs against real Postgres with real RLS. The gap being
closed is a time-of-check-to-time-of-use window that is unbounded in practice
-- a queue backlog, a retry, or a worker restart can all put minutes or hours
between the upload that authorized a job and the worker that runs it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from iam_platform.workers.job_context import JobAuthorizationError, establish_job_context

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def seeded(migrator_engine: AsyncEngine) -> AsyncIterator[dict[str, UUID]]:
    """An active tenant, an active user, and an active membership joining them.

    Seeded through the migrator connection so the fixture itself isn't subject
    to the RLS being tested -- the same approach the RLS proof suite uses.
    """
    ids = {
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "membership_id": uuid4(),
        "other_tenant_id": uuid4(),
    }
    async with migrator_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, status, security_stamp) "
                "VALUES (:id, :email, 'active', :stamp)"
            ),
            {"id": str(ids["user_id"]), "email": f"worker-{ids['user_id']}@example.test",
             "stamp": str(uuid4())},
        )
        for key in ("tenant_id", "other_tenant_id"):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                    "VALUES (:id, :slug, 'Worker Test', 'active', :owner)"
                ),
                {"id": str(ids[key]), "slug": f"worker-{ids[key]}", "owner": str(ids["user_id"])},
            )
        await conn.execute(
            text(
                "INSERT INTO tenant_memberships "
                "(id, tenant_id, user_id, status, is_default, metadata, "
                " created_at, updated_at, joined_at) "
                "VALUES (:id, :tid, :uid, 'active', false, '{}'::jsonb, "
                "        now(), now(), now())"
            ),
            {"id": str(ids["membership_id"]), "tid": str(ids["tenant_id"]),
             "uid": str(ids["user_id"])},
        )

    yield ids

    async with migrator_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM tenant_memberships WHERE user_id = :uid"),
            {"uid": str(ids["user_id"])},
        )
        await conn.execute(
            text("DELETE FROM tenants WHERE id = ANY(:ids)"),
            {"ids": [str(ids["tenant_id"]), str(ids["other_tenant_id"])]},
        )
        await conn.execute(
            text("DELETE FROM users WHERE id = :uid"), {"uid": str(ids["user_id"])}
        )


async def _run_in_transaction(engine: AsyncEngine, tenant_id: UUID, user_id: UUID) -> UUID:
    """Calls establish_job_context exactly as a worker job would."""
    async with AsyncSession(engine) as session, session.begin():
        context = await establish_job_context(
            session, tenant_id=tenant_id, actor_user_id=user_id
        )
        return context.membership_id


class TestJobAuthorizationRevalidation:
    async def test_a_fully_authorized_job_is_accepted(
        self, engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """The positive case -- without it, every test below could pass simply
        because the function always raises."""
        membership_id = await _run_in_transaction(
            engine, seeded["tenant_id"], seeded["user_id"]
        )

        assert membership_id == seeded["membership_id"]

    async def test_suspended_tenant_is_refused(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """A tenant suspended after enqueue must not have work completed for it."""
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET status = 'suspended' WHERE id = :tid"),
                {"tid": str(seeded["tenant_id"])},
            )

        with pytest.raises(JobAuthorizationError, match="suspended"):
            await _run_in_transaction(engine, seeded["tenant_id"], seeded["user_id"])

    async def test_revoked_membership_is_refused(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """The central case: the member who uploaded the document had their
        access revoked while the job sat in the queue."""
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenant_memberships SET status = 'revoked' WHERE id = :mid"),
                {"mid": str(seeded["membership_id"])},
            )

        with pytest.raises(JobAuthorizationError, match="revoked"):
            await _run_in_transaction(engine, seeded["tenant_id"], seeded["user_id"])

    async def test_suspended_membership_is_refused(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenant_memberships SET status = 'suspended' WHERE id = :mid"),
                {"mid": str(seeded["membership_id"])},
            )

        with pytest.raises(JobAuthorizationError, match="suspended"):
            await _run_in_transaction(engine, seeded["tenant_id"], seeded["user_id"])

    async def test_suspended_user_account_is_refused(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """Suspending an account is supposed to stop it acting *immediately*.
        An earlier session found `LoginUser` didn't check `users.status`, which
        made suspension cosmetic; a worker that skipped this check would
        reintroduce exactly that hole on a different path."""
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET status = 'suspended' WHERE id = :uid"),
                {"uid": str(seeded["user_id"])},
            )

        with pytest.raises(JobAuthorizationError, match="suspended"):
            await _run_in_transaction(engine, seeded["tenant_id"], seeded["user_id"])

    async def test_deleted_user_account_is_refused(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET deleted_at = now() WHERE id = :uid"),
                {"uid": str(seeded["user_id"])},
            )

        with pytest.raises(JobAuthorizationError, match="deleted"):
            await _run_in_transaction(engine, seeded["tenant_id"], seeded["user_id"])

    async def test_a_deactivated_account_is_refused(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """The other revocation state. Named explicitly because this check is
        now an allow-by-default one -- it refuses the two revoked statuses
        rather than demanding exactly `active` -- so a new revocation state
        added to `UserStatus` without being listed here would pass silently.
        Testing only `suspended` would not have surfaced that."""
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET status = 'deactivated' WHERE id = :uid"),
                {"uid": str(seeded["user_id"])},
            )

        with pytest.raises(JobAuthorizationError, match="deactivated"):
            await _run_in_transaction(engine, seeded["tenant_id"], seeded["user_id"])

    async def test_an_unverified_account_is_allowed_to_have_its_job_run(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """`pending_verification` is not a revoked state, and this deployment
        can never leave it -- the email sender only logs, so no verification
        link is ever delivered. Refusing it here meant a self-registered
        tenant's upload was accepted with a 201 and then never processed: the
        job died before it had a document row to mark failed, so the console
        showed `processing` for ever with no reason given anywhere.
        """
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET status = 'pending_verification' WHERE id = :uid"),
                {"uid": str(seeded["user_id"])},
            )

        membership_id = await _run_in_transaction(
            engine, seeded["tenant_id"], seeded["user_id"]
        )

        # Asserting on the resolved membership, not merely that nothing was
        # raised: a helper that returned `None` on every path would satisfy
        # "did not refuse" while proving the job had no context to run with.
        assert membership_id == seeded["membership_id"]

    async def test_a_job_claiming_another_tenant_is_refused(
        self, engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """A forged or corrupted payload naming a tenant the actor has no
        membership in.

        Note what makes this safe: the RLS context is set from the *claimed*
        tenant id before any validation query runs, so the membership lookup
        executes scoped to that tenant and finds nothing. The job never gets a
        query executed with more reach than the identity it claims.
        """
        with pytest.raises(JobAuthorizationError, match="no membership"):
            await _run_in_transaction(
                engine, seeded["other_tenant_id"], seeded["user_id"]
            )

    async def test_a_job_naming_a_nonexistent_tenant_is_refused(
        self, engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        with pytest.raises(JobAuthorizationError):
            await _run_in_transaction(engine, uuid4(), seeded["user_id"])

    async def test_a_job_naming_a_nonexistent_user_is_refused(
        self, engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        with pytest.raises(JobAuthorizationError):
            await _run_in_transaction(engine, seeded["tenant_id"], uuid4())
