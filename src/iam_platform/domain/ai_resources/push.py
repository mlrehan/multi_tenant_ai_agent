"""What the platform knows about one browser it can notify.

Deliberately a small, dumb record. There is no interesting domain behaviour in
a push subscription: it is an address the browser gave us plus the two keys
needed to encrypt for it. The decisions worth making about push -- who gets
notified, and what the payload may contain -- are authorization and privacy
decisions, so they live in the use case where the team scope is resolved, not
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from iam_platform.domain.shared.entity import Entity

#: A push payload is delivered through a third-party service and displayed on a
#: lock screen. Kept short because it is a *notice*, not the content -- see
#: `NotifyAgentsOfHandoff` for why no visitor text goes in it.
MAX_PUSH_TITLE_CHARS = 80
MAX_PUSH_BODY_CHARS = 160


@dataclass(kw_only=True)
class PushSubscription(Entity):
    tenant_id: UUID
    membership_id: UUID

    #: The push service endpoint the browser issued. Treated as opaque: it is
    #: a URL at Google/Mozilla/Apple, and parsing it to infer the vendor would
    #: be a guess that breaks when they change it.
    endpoint: str
    p256dh_key: str
    auth_key: str

    user_agent: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PushMessage:
    """A notice to display, with no tenant content in it.

    `url` is a *path*, resolved against the console's own origin by the service
    worker. An absolute URL here would let a stored value send an agent's click
    to another site.
    """

    title: str
    body: str
    url: str
    tag: str
