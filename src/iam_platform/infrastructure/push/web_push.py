"""Web Push delivery via `pywebpush`.

**Not hand-rolled.** RFC 8291 payload encryption is ECDH-P256 + HKDF +
AES128GCM with exact salt, nonce and `info` derivation, and RFC 8292 VAPID is
an ES256 JWT with specific claims. A mistake in any of it produces a request
the push service accepts and the browser silently discards -- a failure mode
with no error anywhere. `pywebpush` is the reference implementation of both.

**`pywebpush` is synchronous**, so each send runs in a worker thread rather
than blocking the event loop. That matters here specifically: a handoff can fan
out to every agent on a team, and serialising a dozen HTTP requests to Apple
and Google on the loop that is also streaming SSE to those same agents would
stall the notification it is trying to deliver.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pywebpush import WebPushException, webpush

from iam_platform.application.ai_resources.ports import PushSendOutcome, PushSendResult
from iam_platform.core.config import PushSettings
from iam_platform.domain.ai_resources.push import PushMessage, PushSubscription

logger = logging.getLogger("iam_platform.infrastructure.push.web_push")

#: Statuses that mean "this endpoint will never work again". 404 is the push
#: service saying it has no such subscription; 410 Gone is the browser having
#: unsubscribed. Anything else -- a 429, a 500, a timeout -- is transient and
#: must NOT prune the subscription, or a push service having a bad ten minutes
#: would quietly unsubscribe an entire tenant's agents.
_DEAD_STATUSES = frozenset({404, 410})


class PyWebPushSender:
    def __init__(self, settings: PushSettings) -> None:
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    async def send(
        self, *, subscription: PushSubscription, message: PushMessage
    ) -> PushSendResult:
        if not self.is_configured:
            # Should not be reached -- callers check first -- but returning a
            # result rather than raising keeps an unconfigured deployment from
            # turning one missing env var into a failed handoff.
            return PushSendResult(PushSendOutcome.FAILED, "push is not configured")

        info = {
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh_key, "auth": subscription.auth_key},
        }
        payload = json.dumps(
            {
                "title": message.title,
                "body": message.body,
                "url": message.url,
                "tag": message.tag,
            }
        )
        try:
            await asyncio.to_thread(self._send_blocking, info, payload)
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in _DEAD_STATUSES:
                return PushSendResult(
                    PushSendOutcome.EXPIRED, f"push service reported {status}"
                )
            # Logged without the endpoint's full URL: it is a capability -- a
            # bearer address for that browser -- and does not belong in logs
            # that are shipped elsewhere.
            logger.warning("web push failed (status=%s)", status)
            return PushSendResult(PushSendOutcome.FAILED, f"status={status}")
        except Exception:
            logger.exception("unexpected error sending web push")
            return PushSendResult(PushSendOutcome.FAILED, "unexpected error")
        return PushSendResult(PushSendOutcome.DELIVERED)

    def _send_blocking(self, info: dict[str, Any], payload: str) -> None:
        webpush(
            subscription_info=info,
            data=payload,
            vapid_private_key=self._settings.vapid_private_key.get_secret_value(),
            vapid_claims={"sub": self._settings.vapid_subject},
            ttl=self._settings.ttl_seconds,
        )


class UnconfiguredWebPushSender:
    """Reports itself unconfigured and refuses to pretend otherwise.

    Deliberately not a no-op that returns `DELIVERED`: that is the "inert by
    design" shape this codebase has been bitten by repeatedly -- it would make
    the metrics, the logs and the `last_used_at` column all claim notifications
    were being delivered to agents who never received one.
    """

    @property
    def is_configured(self) -> bool:
        return False

    async def send(
        self, *, subscription: PushSubscription, message: PushMessage
    ) -> PushSendResult:
        del subscription, message
        return PushSendResult(PushSendOutcome.FAILED, "push is not configured")


def build_web_push_sender(settings: PushSettings) -> PyWebPushSender | UnconfiguredWebPushSender:
    if not settings.is_configured:
        logger.info(
            "PUSH__VAPID_PUBLIC_KEY / PUSH__VAPID_PRIVATE_KEY are not set -- agent "
            "push notifications are disabled. The inbox still updates live in an "
            "open tab. Generate a keypair with: python -m py_vapid --gen"
        )
        return UnconfiguredWebPushSender()
    return PyWebPushSender(settings)
