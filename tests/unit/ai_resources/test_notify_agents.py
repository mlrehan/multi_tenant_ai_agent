"""Who gets a push notification, and what it is allowed to contain.

These are the two properties worth testing here. Delivery itself is
`pywebpush`'s job and is faked; the decisions are the audience (which must
match what the inbox will actually show the recipient) and the payload (which
travels through a third party and lands on a lock screen).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.handoff import QUEUE_OVERSIGHT_PERMISSION
from iam_platform.application.ai_resources.notify_agents import (
    NotifyAgentsCommand,
    NotifyAgentsOfHandoff,
)
from iam_platform.application.ai_resources.ports import PushSendOutcome, PushSendResult
from iam_platform.domain.ai_resources.push import PushMessage, PushSubscription

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 1, tzinfo=UTC)
TENANT = uuid4()
ADMISSIONS = uuid4()
ADMISSIONS_AGENT = uuid4()
ACCOUNTS_AGENT = uuid4()
ADMIN = uuid4()


class _FixedClock:
    def now(self) -> datetime:
        return NOW


def _subscription(membership_id: UUID, endpoint: str) -> PushSubscription:
    return PushSubscription(
        id=uuid4(),
        tenant_id=TENANT,
        membership_id=membership_id,
        endpoint=endpoint,
        p256dh_key="p256dh",
        auth_key="auth",
        created_at=NOW,
    )


@dataclass
class _FakeTeams:
    staff: dict[UUID, list[UUID]] = field(default_factory=dict)
    oversight: list[UUID] = field(default_factory=list)

    async def list_members(self, *, tenant_id: UUID, team_id: UUID) -> list[UUID]:
        del tenant_id
        return list(self.staff.get(team_id, []))

    async def list_memberships_with_permission(
        self, *, tenant_id: UUID, permission_code: str
    ) -> list[UUID]:
        del tenant_id
        if permission_code == QUEUE_OVERSIGHT_PERMISSION:
            return list(self.oversight)
        return []


@dataclass
class _FakePushRepo:
    by_membership: dict[UUID, list[PushSubscription]] = field(default_factory=dict)
    asked_for: list[list[UUID]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    marked: list[str] = field(default_factory=list)

    async def list_for_memberships(
        self, *, tenant_id: UUID, membership_ids: object
    ) -> list[PushSubscription]:
        del tenant_id
        ids = list(membership_ids)  # type: ignore[arg-type]
        self.asked_for.append(ids)
        out: list[PushSubscription] = []
        for membership_id in ids:
            out.extend(self.by_membership.get(membership_id, []))
        return out

    async def delete_by_endpoint(self, *, tenant_id: UUID, endpoint: str) -> int:
        del tenant_id
        self.deleted.append(endpoint)
        return 1

    async def mark_used(self, *, tenant_id: UUID, endpoint: str, at: datetime) -> None:
        del tenant_id, at
        self.marked.append(endpoint)


@dataclass
class _FakeSender:
    configured: bool = True
    outcomes: dict[str, PushSendOutcome] = field(default_factory=dict)
    sent: list[tuple[str, PushMessage]] = field(default_factory=list)

    @property
    def is_configured(self) -> bool:
        return self.configured

    async def send(
        self, *, subscription: PushSubscription, message: PushMessage
    ) -> PushSendResult:
        self.sent.append((subscription.endpoint, message))
        return PushSendResult(
            self.outcomes.get(subscription.endpoint, PushSendOutcome.DELIVERED)
        )


class _FakeUow:
    def __init__(self, teams: _FakeTeams, push: _FakePushRepo) -> None:
        self.teams = teams
        self.push_subscriptions = push

    async def __aenter__(self) -> _FakeUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _build(teams: _FakeTeams, push: _FakePushRepo, sender: _FakeSender):
    def factory(user_id: UUID, tenant_id: UUID) -> _FakeUow:
        del user_id, tenant_id
        return _FakeUow(teams, push)

    return NotifyAgentsOfHandoff(factory, sender, _FixedClock())  # type: ignore[arg-type]


class TestTheAudience:
    async def test_only_the_routed_team_and_oversight_are_notified(self) -> None:
        """An agent staffing Accounts must not be told about an Admissions
        handoff -- their own inbox would refuse to show it, so the alert would
        point at something they are then told does not exist."""
        teams = _FakeTeams(staff={ADMISSIONS: [ADMISSIONS_AGENT]}, oversight=[ADMIN])
        push = _FakePushRepo(
            by_membership={
                ADMISSIONS_AGENT: [_subscription(ADMISSIONS_AGENT, "https://push/adm")],
                ACCOUNTS_AGENT: [_subscription(ACCOUNTS_AGENT, "https://push/acc")],
                ADMIN: [_subscription(ADMIN, "https://push/admin")],
            }
        )
        sender = _FakeSender()

        result = await _build(teams, push, sender).execute(
            NotifyAgentsCommand(
                tenant_id=TENANT, team_id=ADMISSIONS, team_name="Admissions Enquiries"
            )
        )

        endpoints = {endpoint for endpoint, _ in sender.sent}
        assert endpoints == {"https://push/adm", "https://push/admin"}
        assert "https://push/acc" not in endpoints
        assert result.delivered == 2

    async def test_an_admin_who_also_staffs_the_team_is_notified_once(self) -> None:
        """One person, one buzz. The two source lists overlap and the union has
        to be deduplicated or an admin on the rota gets everything twice."""
        teams = _FakeTeams(staff={ADMISSIONS: [ADMIN]}, oversight=[ADMIN])
        push = _FakePushRepo(
            by_membership={ADMIN: [_subscription(ADMIN, "https://push/admin")]}
        )
        sender = _FakeSender()

        await _build(teams, push, sender).execute(
            NotifyAgentsCommand(tenant_id=TENANT, team_id=ADMISSIONS, team_name="Adm")
        )

        assert push.asked_for == [[ADMIN]]
        assert len(sender.sent) == 1

    async def test_an_unrouted_handoff_reaches_only_oversight(self) -> None:
        teams = _FakeTeams(staff={ADMISSIONS: [ADMISSIONS_AGENT]}, oversight=[ADMIN])
        push = _FakePushRepo(
            by_membership={
                ADMISSIONS_AGENT: [_subscription(ADMISSIONS_AGENT, "https://push/adm")],
                ADMIN: [_subscription(ADMIN, "https://push/admin")],
            }
        )
        sender = _FakeSender()

        await _build(teams, push, sender).execute(
            NotifyAgentsCommand(tenant_id=TENANT, team_id=None, team_name=None)
        )

        assert [endpoint for endpoint, _ in sender.sent] == ["https://push/admin"]


class TestThePayload:
    async def test_it_carries_no_visitor_content(self) -> None:
        """The team name is fine -- it is the tenant's own label. The visitor's
        words are not: a push payload is stored by a third-party service and
        rendered on a lock screen."""
        teams = _FakeTeams(staff={ADMISSIONS: [ADMISSIONS_AGENT]})
        push = _FakePushRepo(
            by_membership={
                ADMISSIONS_AGENT: [_subscription(ADMISSIONS_AGENT, "https://push/adm")]
            }
        )
        sender = _FakeSender()

        await _build(teams, push, sender).execute(
            NotifyAgentsCommand(
                tenant_id=TENANT, team_id=ADMISSIONS, team_name="Accounts Enquiries"
            )
        )

        _, message = sender.sent[0]
        assert "Accounts Enquiries" in message.body
        # A relative path, so a stored value cannot send an authenticated
        # agent's click to another origin.
        assert not message.url.startswith("http")
        assert message.tag.endswith(str(ADMISSIONS))


class TestFailureHandling:
    async def test_an_expired_endpoint_is_pruned_and_others_still_arrive(self) -> None:
        """404/410 means the browser is gone for good. Keeping it would
        re-attempt a dead endpoint on every future handoff, for ever."""
        teams = _FakeTeams(staff={ADMISSIONS: [ADMISSIONS_AGENT, ADMIN]})
        push = _FakePushRepo(
            by_membership={
                ADMISSIONS_AGENT: [_subscription(ADMISSIONS_AGENT, "https://push/dead")],
                ADMIN: [_subscription(ADMIN, "https://push/live")],
            }
        )
        sender = _FakeSender(
            outcomes={"https://push/dead": PushSendOutcome.EXPIRED}
        )

        result = await _build(teams, push, sender).execute(
            NotifyAgentsCommand(tenant_id=TENANT, team_id=ADMISSIONS, team_name="Adm")
        )

        assert push.deleted == ["https://push/dead"]
        assert push.marked == ["https://push/live"]
        assert result.delivered == 1
        assert result.expired == 1

    async def test_a_transient_failure_does_not_prune(self) -> None:
        """A push service having a bad ten minutes must not quietly unsubscribe
        a tenant's entire team."""
        teams = _FakeTeams(staff={ADMISSIONS: [ADMISSIONS_AGENT]})
        push = _FakePushRepo(
            by_membership={
                ADMISSIONS_AGENT: [_subscription(ADMISSIONS_AGENT, "https://push/flaky")]
            }
        )
        sender = _FakeSender(outcomes={"https://push/flaky": PushSendOutcome.FAILED})

        result = await _build(teams, push, sender).execute(
            NotifyAgentsCommand(tenant_id=TENANT, team_id=ADMISSIONS, team_name="Adm")
        )

        assert push.deleted == []
        assert result.failed == 1

    async def test_an_unconfigured_deployment_sends_nothing_at_all(self) -> None:
        """Not "attempts and fails" -- that would log an error per agent per
        handoff on every deployment without a VAPID keypair."""
        teams = _FakeTeams(staff={ADMISSIONS: [ADMISSIONS_AGENT]})
        push = _FakePushRepo(
            by_membership={
                ADMISSIONS_AGENT: [_subscription(ADMISSIONS_AGENT, "https://push/adm")]
            }
        )
        sender = _FakeSender(configured=False)

        result = await _build(teams, push, sender).execute(
            NotifyAgentsCommand(tenant_id=TENANT, team_id=ADMISSIONS, team_name="Adm")
        )

        assert sender.sent == []
        assert push.asked_for == []
        assert result == type(result)(0, 0, 0, 0)
