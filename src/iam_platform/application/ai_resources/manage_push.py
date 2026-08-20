"""An agent turning browser notifications on and off.

**The membership is resolved server-side from the authenticated session**, never
accepted from the request. A `membership_id` in the body would let any
authenticated caller register their own browser as a *colleague's* device and
receive that colleague's queue notifications -- which, since the notification
names the team, is a small but real disclosure of another person's work.

Subscribing needs the same permission as working the inbox. Someone who cannot
see the queue has nothing to be notified about, and letting them subscribe
would create rows that the notifier then has to remember to filter out.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    PermissionDeniedError,
    PushSubscriptionInvalidError,
)
from iam_platform.application.ai_resources.handoff import AGENT_PERMISSION
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.push import PushSubscription

#: A push endpoint URL is long (Apple's run to several hundred characters) but
#: not unbounded. Capped so a caller cannot use this table as free storage.
MAX_ENDPOINT_CHARS = 2000
MAX_KEY_CHARS = 400
MAX_USER_AGENT_CHARS = 400


@dataclass(frozen=True, slots=True)
class SubscribeToPushCommand:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    endpoint: str
    p256dh_key: str
    auth_key: str
    user_agent: str | None = None


class SubscribeToPush:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: SubscribeToPushCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)

        endpoint = command.endpoint.strip()
        if not endpoint.startswith("https://"):
            # Push endpoints are always https. Refusing anything else keeps a
            # crafted value from being handed to the HTTP client later.
            raise PushSubscriptionInvalidError(
                "a push endpoint must be an https URL"
            )
        if len(endpoint) > MAX_ENDPOINT_CHARS:
            raise PushSubscriptionInvalidError("that push endpoint is too long")
        if not command.p256dh_key or not command.auth_key:
            raise PushSubscriptionInvalidError(
                "the subscription is missing its encryption keys"
            )
        if len(command.p256dh_key) > MAX_KEY_CHARS or len(command.auth_key) > MAX_KEY_CHARS:
            raise PushSubscriptionInvalidError("those subscription keys are too long")

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if AGENT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(AGENT_PERMISSION)

            # From the session, not the request. See the module docstring.
            membership = await uow.tenant_memberships.get_by_tenant_and_user(
                tenant_id, actor_id
            )
            if membership is None:
                raise PermissionDeniedError(AGENT_PERMISSION)

            await uow.push_subscriptions.upsert(
                PushSubscription(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    membership_id=membership.id,
                    endpoint=endpoint,
                    p256dh_key=command.p256dh_key,
                    auth_key=command.auth_key,
                    user_agent=(command.user_agent or "")[:MAX_USER_AGENT_CHARS] or None,
                    created_at=self._clock.now(),
                )
            )


@dataclass(frozen=True, slots=True)
class UnsubscribeFromPushCommand:
    actor_user_id: str
    tenant_id: str
    endpoint: str


class UnsubscribeFromPush:
    """Removes one browser's subscription.

    **Scoped to the caller's own membership**, so an endpoint string learned
    some other way cannot be used to silence a colleague's notifications --
    which would be a quiet denial of service against one agent.

    Idempotent: unsubscribing something already gone is a success, because the
    caller's intent ("do not notify this browser") is satisfied either way, and
    a browser that has already dropped its subscription would otherwise get an
    error it cannot act on.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: UnsubscribeFromPushCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        async with self._uow_factory(actor_id, tenant_id) as uow:
            membership = await uow.tenant_memberships.get_by_tenant_and_user(
                tenant_id, actor_id
            )
            if membership is None:
                return
            await uow.push_subscriptions.delete_for_membership(
                tenant_id=tenant_id,
                membership_id=membership.id,
                endpoint=command.endpoint,
            )
