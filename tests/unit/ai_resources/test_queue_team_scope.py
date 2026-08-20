"""Who may see which handoff queue.

The rule: an agent sees only the teams they staff; a caller holding the
oversight permission sees every team in the tenant; nobody sees another
tenant's queue (that boundary is RLS's, and is proved in the integration
suite -- these tests are about the *scope within* one tenant).

The scope is returned rather than applied as a post-filter on purpose, so
these assertions are about what the query will be *asked* for. A conversation
belonging to another team is never loaded, which is a stronger property than
loading it and declining to render it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.handoff import (
    AGENT_PERMISSION,
    QUEUE_OVERSIGHT_PERMISSION,
    resolve_queue_team_scope,
)

pytestmark = pytest.mark.unit

TENANT = uuid4()
USER = uuid4()
MEMBERSHIP = uuid4()
ADMISSIONS = uuid4()
ACCOUNTS = uuid4()


@dataclass
class _Membership:
    id: UUID


@dataclass
class _FakeMemberships:
    membership: _Membership | None

    async def get_by_tenant_and_user(
        self, tenant_id: UUID, user_id: UUID
    ) -> _Membership | None:
        del tenant_id, user_id
        return self.membership


@dataclass
class _FakeTeams:
    by_membership: dict[UUID, list[UUID]] = field(default_factory=dict)
    calls: list[UUID] = field(default_factory=list)

    async def list_team_ids_for_membership(
        self, *, tenant_id: UUID, membership_id: UUID
    ) -> list[UUID]:
        del tenant_id
        self.calls.append(membership_id)
        return self.by_membership.get(membership_id, [])


class _FakeUow:
    def __init__(self, membership: _Membership | None, teams: _FakeTeams) -> None:
        # Matches the real `SqlAiResourceUnitOfWork` attribute name --
        # `memberships` would let this test pass while the real code used a
        # nonexistent one, exactly as it did on the first pass.
        self.tenant_memberships = _FakeMemberships(membership)
        self.teams = teams


async def _scope(permissions: set[str], *, membership: _Membership | None,
                 teams: _FakeTeams) -> list[UUID] | None:
    return await resolve_queue_team_scope(
        _FakeUow(membership, teams),
        tenant_id=TENANT,
        user_id=USER,
        permissions=frozenset(permissions),
    )


class TestQueueScope:
    async def test_an_agent_is_scoped_to_the_teams_they_staff(self) -> None:
        teams = _FakeTeams({MEMBERSHIP: [ADMISSIONS]})

        scope = await _scope(
            {AGENT_PERMISSION}, membership=_Membership(MEMBERSHIP), teams=teams
        )

        assert scope == [ADMISSIONS]
        assert ACCOUNTS not in (scope or [])

    async def test_oversight_sees_every_team(self) -> None:
        """`None` is the query's "no team predicate", so a tenant admin's
        inbox is unfiltered -- and the membership lookup is skipped entirely,
        which is why an admin who staffs no team still sees the queue."""
        teams = _FakeTeams({MEMBERSHIP: [ADMISSIONS]})

        scope = await _scope(
            {AGENT_PERMISSION, QUEUE_OVERSIGHT_PERMISSION},
            membership=_Membership(MEMBERSHIP),
            teams=teams,
        )

        assert scope is None
        assert teams.calls == [], "oversight should not need a team lookup"

    async def test_an_agent_on_no_team_sees_nothing(self) -> None:
        """An empty list is a real answer, not a missing one: someone granted
        inbox access but put on no team has no queue. The repository treats
        `[]` as "match nothing", so this must not collapse into `None`."""
        teams = _FakeTeams({MEMBERSHIP: []})

        scope = await _scope(
            {AGENT_PERMISSION}, membership=_Membership(MEMBERSHIP), teams=teams
        )

        assert scope == []
        assert scope is not None, "[] must not degrade into 'every team'"

    async def test_a_caller_with_no_membership_row_fails_closed(self) -> None:
        scope = await _scope({AGENT_PERMISSION}, membership=None, teams=_FakeTeams())

        assert scope == []

    async def test_a_multi_team_agent_sees_the_union(self) -> None:
        teams = _FakeTeams({MEMBERSHIP: [ADMISSIONS, ACCOUNTS]})

        scope = await _scope(
            {AGENT_PERMISSION}, membership=_Membership(MEMBERSHIP), teams=teams
        )

        assert scope is not None
        assert set(scope) == {ADMISSIONS, ACCOUNTS}
