"""Automatic deletion of conversations past their tenant's retention window.

Widget conversations include anonymous visitors: people who never signed up,
cannot log in to ask for their data back, and have no relationship with the
platform at all. Keeping their questions indefinitely would be a retention
decision nobody made. So retention is a required setting with a 30-day default
(`DEFAULT_RETENTION_DAYS`), and this is the job that actually enforces it --
without it the number on the settings screen would be one more stored value
that nothing reads.

**Deleted, not soft-deleted.** A `deleted_at` column would leave the visitor's
words in the table, which is the opposite of what a retention promise means.
`conversation_messages` follows by `ON DELETE CASCADE`.

**Per-tenant transactions, not one sweep-wide transaction.** A tenant whose
delete fails must not roll back the tenants already purged, and a single
transaction over every tenant's conversations holds locks for the length of
the whole job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.chatbot import DEFAULT_RETENTION_DAYS

logger = logging.getLogger("iam_platform.application.ai_resources.purge_conversations")

#: Fills `app.user_id` for the RLS session. Not an audit actor -- see below.
_SYSTEM_SESSION_ID = UUID(int=0)


@dataclass(frozen=True, slots=True)
class PurgeResult:
    tenant_id: UUID
    retention_days: int
    deleted: int


class PurgeExpiredConversations:
    """Applies one tenant's retention window.

    Takes a tenant id rather than sweeping every tenant itself: the unit of
    work is tenant-scoped by construction (RLS sets `app.tenant_id`), so a
    cross-tenant sweep would need a platform-privileged session -- a much
    larger authority than a retention job needs, and one whose bug would
    delete another tenant's conversations.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, *, tenant_id: UUID) -> PurgeResult:
        now = self._clock.now()
        # The all-zero id fills the RLS session's `app.user_id`, which is a
        # session variable rather than a foreign key -- unlike the audit
        # actor, which is one and is therefore left null. The *tenant* scope
        # is what confines every statement here.
        async with self._uow_factory(_SYSTEM_SESSION_ID, tenant_id) as uow:
            settings = await uow.chatbot_settings.get_for_tenant(tenant_id)
            # A tenant that has never opened the settings screen still has a
            # retention policy -- the default one. Treating "no row" as "no
            # policy" would exempt exactly the tenants who never thought about
            # it, which is the wrong way round.
            retention_days = (
                settings.conversation_retention_days
                if settings is not None
                else DEFAULT_RETENTION_DAYS
            )
            cutoff = now - timedelta(days=retention_days)
            deleted = await uow.handoff.purge_expired_conversations(
                tenant_id=tenant_id, older_than=cutoff
            )
            if deleted:
                # Audited: deletion of tenant data on a schedule must be
                # answerable later, and "the job ran" is not the same claim as
                # "these rows went". The audit row survives the conversations.
                await uow.audit.record(
                    # **No actor, rather than a synthetic one.** `actor_user_id`
                    # is a foreign key into `users`, so a placeholder uuid is
                    # refused outright -- and inventing a "system user" row
                    # would put an account in the directory that nobody can
                    # sign in as and that a future audit reads as a person.
                    # A scheduled deletion genuinely has no human actor; the
                    # action name says what did it.
                    actor_user_id=None,
                    effective_user_id=None,
                    tenant_id=tenant_id,
                    action="tenant.conversations.purged",
                    resource_type="conversations",
                    resource_id=tenant_id,
                    result="success",
                    metadata={
                        "retention_days": retention_days,
                        "deleted": deleted,
                        "older_than": cutoff.isoformat(),
                    },
                )
        if deleted:
            logger.info(
                "purged %s conversations for tenant %s (retention %s days)",
                deleted,
                tenant_id,
                retention_days,
            )
        return PurgeResult(
            tenant_id=tenant_id, retention_days=retention_days, deleted=deleted
        )
