"""Per-job authorization re-validation -- the security core of the worker runtime.

docs/18-schema-rls-and-migrations.md's "Worker context" section requires that
a job's tenant and actor are **re-validated against the database at execution
time**, not merely trusted from the enqueue-time payload. This module is that
requirement.

**Why it matters, concretely.** Enqueue and execution are separated by an
unbounded gap -- a queue backlog, a retry, a worker restart. In that gap the
membership that authorized the upload can be revoked, the member suspended,
the whole tenant suspended, or the user's account deleted. A worker that
trusts its payload would happily finish ingesting a document for a tenant that
was shut off ten minutes ago, and would do it holding a valid RLS context.

This is the half of threat-model scenario 8 that Phase 8 recorded as only
**partially** covered: "no ``workers/`` runtime exists yet, so there is no
job-execution path to attack... the per-job re-validation half must be tested
when workers are built." It is built here, and
``tests/security/test_worker_job_revalidation.py`` exercises it.

**Order of operations matters.** The RLS context is set *first*, from the
claimed tenant id, and every validation query then runs under it. That is
deliberate: it means a job claiming another tenant's id gets scoped to that
tenant and then fails the membership check, rather than being validated with
broad visibility and only afterwards narrowed. A hostile payload therefore
never gets a query executed with more reach than the identity it claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class JobAuthorizationError(Exception):
    """A job's claimed tenant/actor no longer authorizes it.

    Deliberately not an ``application`` exception: it never reaches an HTTP
    handler, so it has no place in ``api/exception_handlers.py``'s status map.
    A worker catches it, records the refusal, and drops the job -- retrying
    would only re-fail, since the cause is a revoked authorization rather than
    a transient fault.
    """


@dataclass(frozen=True, slots=True)
class VerifiedJobContext:
    """Proof that a job's authorization was re-checked against the database.

    Constructed only by ``establish_job_context``. Jobs take this rather than
    raw ids so that "did anyone actually verify this?" is answered by the type
    signature instead of by reading the call site.
    """

    tenant_id: UUID
    actor_user_id: UUID
    membership_id: UUID


async def establish_job_context(
    session: AsyncSession, *, tenant_id: UUID, actor_user_id: UUID
) -> VerifiedJobContext:
    """Sets the transaction's RLS context, then re-validates it.

    Must be called inside an open transaction: ``set_config(..., true)`` is
    transaction-scoped, so the context and the work it authorizes have to share
    one. Raises ``JobAuthorizationError`` if the job is no longer authorized.
    """
    # Parameterized, matching the Units of Work -- never string-interpolated
    # into SQL text (docs/18).
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(actor_user_id)}
    )
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )

    # 1. Tenant still exists and is active. RLS restricts `tenants` to the
    #    caller's own row (the Phase 6 gap fix), so this reads exactly the
    #    tenant the context was just set to -- a job claiming someone else's
    #    tenant id sees nothing here.
    tenant_status = (
        await session.execute(
            text("SELECT status FROM tenants WHERE id = :tid"), {"tid": str(tenant_id)}
        )
    ).scalar()
    if tenant_status is None:
        raise JobAuthorizationError(f"tenant {tenant_id} no longer visible or does not exist")
    if tenant_status != "active":
        raise JobAuthorizationError(f"tenant {tenant_id} is {tenant_status}, not active")

    # 2. The actor still holds an active membership in it. This is what stops
    #    a job enqueued before a revocation from completing after it.
    membership = (
        await session.execute(
            text(
                "SELECT id, status FROM tenant_memberships "
                "WHERE tenant_id = :tid AND user_id = :uid"
            ),
            {"tid": str(tenant_id), "uid": str(actor_user_id)},
        )
    ).first()
    if membership is None:
        raise JobAuthorizationError(
            f"user {actor_user_id} has no membership in tenant {tenant_id}"
        )
    membership_id, membership_status = membership
    if membership_status != "active":
        raise JobAuthorizationError(
            f"membership of {actor_user_id} in tenant {tenant_id} is {membership_status}"
        )

    # 3. The account itself is still usable. Suspending or deleting a user is
    #    supposed to stop them acting anywhere immediately; without this a
    #    queued job would keep acting on their behalf. `users` is global
    #    identity with no RLS, so this is an unscoped read by primary key.
    account = (
        await session.execute(
            text("SELECT status, deleted_at FROM users WHERE id = :uid"),
            {"uid": str(actor_user_id)},
        )
    ).first()
    if account is None:
        raise JobAuthorizationError(f"user {actor_user_id} does not exist")
    account_status, deleted_at = account
    if deleted_at is not None:
        raise JobAuthorizationError(f"user {actor_user_id} is deleted")
    # The revocation states, mirroring `User.can_authenticate` -- the same
    # question login and the API's per-request check ask. Demanding `active`
    # here also refused `pending_verification`, which this deployment can
    # never leave (no email provider), so a self-registered tenant's uploads
    # were accepted with a 201 and then sat in `processing` for ever: the job
    # died before it had a document row to mark failed, so nothing anywhere
    # said why. Suspended, deactivated and deleted accounts are still refused,
    # which is what threat-model scenario 8 requires.
    if account_status in ("suspended", "deactivated"):
        raise JobAuthorizationError(f"user {actor_user_id} is {account_status}")

    return VerifiedJobContext(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        membership_id=membership_id,
    )
