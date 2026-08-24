"""Push notifications for agents whose console is closed.

The chime and the toast both need an open tab. This is the path that reaches an
agent who has gone to lunch -- and it is the only notification channel where
getting the *audience* wrong has consequences beyond noise, because a push
notification is delivered through a third party and displayed on a lock screen.

**Three decisions, and each of them is the point:**

1. **The recipient list honours the same team scope as the inbox.** An agent
   staffing Admissions must not be notified about a billing dispute routed to
   Accounts -- they cannot open it (`resolve_queue_team_scope` hides it), so a
   notification would be an alert about something they are then told does not
   exist. Recipients are the team's own members plus whoever holds the
   oversight permission.

2. **The payload carries no visitor content.** Not the question, not the
   handoff reason. Those are the visitor's words, they may contain anything a
   stranger typed, and a push payload is stored briefly by Google/Mozilla/Apple
   and then rendered on an unlocked-by-nobody phone screen. The notification
   says a visitor is waiting and which team; the console shows the rest to
   someone who has authenticated.

3. **Nothing here can fail the handoff.** The transfer has already committed
   by the time this runs. Every send is best-effort and every failure is
   swallowed and logged -- a push service having a bad minute must not roll
   back a conversation that a visitor is already waiting on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.ai_resources.handoff import QUEUE_OVERSIGHT_PERMISSION
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    PushSendOutcome,
    WebPushSender,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.push import PushMessage

logger = logging.getLogger("iam_platform.application.ai_resources.notify_agents")

#: Collapses repeat notifications in the OS tray. One tag per team, so two
#: waiting visitors for Admissions replace each other rather than stacking --
#: an agent needs to know the queue is not empty, not to clear six alerts.
_TAG_PREFIX = "handoff"


@dataclass(frozen=True, slots=True)
class NotifyAgentsCommand:
    tenant_id: UUID
    team_id: UUID | None
    team_name: str | None


@dataclass(frozen=True, slots=True)
class NotifyResult:
    recipients: int
    delivered: int
    expired: int
    failed: int


class NotifyAgentsOfHandoff:
    """Pushes "a visitor is waiting" to the agents who may actually take it."""

    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        sender: WebPushSender,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._sender = sender
        self._clock = clock

    async def execute(self, command: NotifyAgentsCommand) -> NotifyResult:
        if not self._sender.is_configured:
            # No VAPID keypair: do nothing at all rather than attempt a send
            # per agent per handoff and log a failure for each.
            return NotifyResult(0, 0, 0, 0)

        now = self._clock.now()
        message = _build_message(command)

        async with self._uow_factory(_SYSTEM_SESSION_ID, command.tenant_id) as uow:
            recipient_ids = await self._recipients(uow, command)
            subscriptions = await uow.push_subscriptions.list_for_memberships(
                tenant_id=command.tenant_id, membership_ids=recipient_ids
            )

        delivered = expired = failed = 0
        prune: list[str] = []
        for subscription in subscriptions:
            result = await self._sender.send(subscription=subscription, message=message)
            if result.outcome is PushSendOutcome.DELIVERED:
                delivered += 1
            elif result.outcome is PushSendOutcome.EXPIRED:
                expired += 1
                prune.append(subscription.endpoint)
            else:
                failed += 1

        # Pruned in a *second* transaction, after the sends. Holding the first
        # one open across a dozen HTTP calls to Apple and Google would keep a
        # database transaction alive for the slowest push service in the set.
        if prune or delivered:
            async with self._uow_factory(_SYSTEM_SESSION_ID, command.tenant_id) as uow:
                for endpoint in prune:
                    # A 404/410 means the browser is gone for good. Keeping it
                    # would re-attempt a dead endpoint on every future handoff,
                    # for ever.
                    await uow.push_subscriptions.delete_by_endpoint(
                        tenant_id=command.tenant_id, endpoint=endpoint
                    )
                for subscription in subscriptions:
                    if subscription.endpoint not in prune:
                        await uow.push_subscriptions.mark_used(
                            tenant_id=command.tenant_id,
                            endpoint=subscription.endpoint,
                            at=now,
                        )

        logger.info(
            "handoff push: %s recipient(s), %s delivered, %s expired, %s failed",
            len(subscriptions),
            delivered,
            expired,
            failed,
        )
        return NotifyResult(len(subscriptions), delivered, expired, failed)

    async def _recipients(
        self, uow: object, command: NotifyAgentsCommand
    ) -> list[UUID]:
        """Memberships that may see this conversation in their inbox.

        The team's own members, plus anyone holding queue oversight. Deriving
        it from the same two facts the inbox query uses is what keeps a
        notification from advertising a conversation its recipient will then be
        told does not exist.
        """
        if command.team_id is None:
            # An unrouted handoff belongs to no team, so only oversight holders
            # can act on it.
            return await _oversight_memberships(uow, command.tenant_id)

        staff = await uow.teams.list_members(  # type: ignore[attr-defined]
            tenant_id=command.tenant_id, team_id=command.team_id
        )
        oversight = await _oversight_memberships(uow, command.tenant_id)
        # Deduplicated: an admin who also staffs the team is one person and
        # should get one notification, not two.
        return list(dict.fromkeys([*staff, *oversight]))


def _build_message(command: NotifyAgentsCommand) -> PushMessage:
    team = command.team_name or "your team"
    return PushMessage(
        title="A visitor is waiting",
        # Team name only. The visitor's own words stay on the server -- see the
        # module docstring.
        body=f"Someone has asked to speak with {team}.",
        # **The inbox for this tenant, not the console root.** A notification
        # that says someone is waiting and then lands the agent on a dashboard
        # makes them navigate to the queue themselves -- at exactly the moment
        # the point was to save them the seconds. A relative path, resolved by
        # the service worker against the console's own origin, so a payload can
        # never send an authenticated tab to another site.
        url=f"/tenant/{command.tenant_id}/inbox",
        tag=f"{_TAG_PREFIX}:{command.team_id or 'unassigned'}",
    )


async def _oversight_memberships(uow: object, tenant_id: UUID) -> list[UUID]:
    """Memberships whose roles carry the queue-oversight permission.

    Resolved from role grants rather than from a stored list, so revoking the
    permission stops the notifications too -- a cached recipient list is how a
    former supervisor keeps being told about queues they can no longer open.
    """
    ids: list[UUID] = await uow.teams.list_memberships_with_permission(  # type: ignore[attr-defined]
        tenant_id=tenant_id, permission_code=QUEUE_OVERSIGHT_PERMISSION
    )
    return ids


#: Fills `app.user_id` for the RLS session. Notifications have no human actor;
#: the tenant scope is what confines every read.
_SYSTEM_SESSION_ID = UUID(int=0)
