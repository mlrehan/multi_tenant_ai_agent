"""A tenant with no team gets one, exactly once.

The symptom this fixes: `prompt_layers` only offers a handoff when the tenant
permits it *and* at least one team exists, so a fresh tenant's chatbot told
every visitor it could not fetch a colleague -- with nothing on any screen
saying why. The Teams form existed; nothing led anyone to it.

The risk it introduces, and what these tests pin: creating on a read must not
create *repeatedly*, must not resurrect a team the tenant deliberately
deactivated, and must survive two tabs opening the Inbox at the same instant.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.manage_chatbot import (
    DEFAULT_TEAM_NAME,
    ListTeams,
    ListTeamsQuery,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.tenancy.teams import TenantTeam

pytestmark = pytest.mark.asyncio

TENANT = uuid4()
ACTOR = uuid4()


class _FixedClock(Clock):
    def now(self) -> datetime:
        return datetime(2026, 8, 26, tzinfo=UTC)


class _Teams:
    """Stands in for the table, including its `UNIQUE (tenant_id, name)`.

    The constraint is simulated rather than assumed away: it is the only thing
    that settles the race, so a fake without it would let a concurrency test
    pass while the real system created duplicates.
    """

    def __init__(self, existing: list[TenantTeam] | None = None) -> None:
        self.rows: list[TenantTeam] = list(existing or [])
        self.add_attempts = 0

    async def list_for_tenant(
        self, tenant_id: UUID, *, active_only: bool = False
    ) -> list[TenantTeam]:
        rows = [t for t in self.rows if t.tenant_id == tenant_id]
        return [t for t in rows if t.is_active] if active_only else rows

    async def add(self, team: TenantTeam) -> None:
        self.add_attempts += 1
        # Yield control, so a concurrent caller can interleave here -- exactly
        # where the real race lives, between the check and the insert.
        await asyncio.sleep(0)
        if any(
            t.tenant_id == team.tenant_id and t.name == team.name for t in self.rows
        ):
            raise RuntimeError("duplicate key value violates uq_tenant_teams_name")
        self.rows.append(team)

    async def list_members(self, *, tenant_id: UUID, team_id: UUID) -> list[UUID]:
        del tenant_id, team_id
        return []


class _Uow:
    def __init__(self, teams: _Teams) -> None:
        self.teams = teams

    async def __aenter__(self) -> "_Uow":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _factory(teams: _Teams):
    def make(actor_id: UUID, tenant_id: UUID) -> _Uow:
        del actor_id, tenant_id
        return _Uow(teams)

    return make


def _query(active_only: bool = False) -> ListTeamsQuery:
    return ListTeamsQuery(
        actor_user_id=str(ACTOR), tenant_id=str(TENANT), active_only=active_only
    )


def _team(name: str, *, active: bool = True) -> TenantTeam:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return TenantTeam(
        id=uuid4(),
        tenant_id=TENANT,
        name=name,
        is_active=active,
        created_at=now,
        updated_at=now,
    )


class TestTheDefaultTeamIsCreatedWhenThereIsNone:
    async def test_a_tenant_with_no_teams_gets_the_default(self) -> None:
        teams = _Teams()
        result = await ListTeams(_factory(teams), _FixedClock()).execute(_query())
        assert [t.name for t, _ in result] == [DEFAULT_TEAM_NAME]

    async def test_it_is_returned_by_the_same_call_that_created_it(self) -> None:
        """Not on the *next* request: an Inbox that shows no team on first load
        and one on refresh is the same broken-looking screen."""
        teams = _Teams()
        result = await ListTeams(_factory(teams), _FixedClock()).execute(_query())
        assert len(result) == 1


class TestItDoesNotCreateWhenItShouldNot:
    async def test_an_existing_team_is_left_alone(self) -> None:
        teams = _Teams([_team("Admissions")])
        result = await ListTeams(_factory(teams), _FixedClock()).execute(_query())
        assert [t.name for t, _ in result] == ["Admissions"]
        assert teams.add_attempts == 0

    async def test_repeated_calls_create_only_one(self) -> None:
        """Logging in again, refreshing, revisiting -- none may add another."""
        teams = _Teams()
        use_case = ListTeams(_factory(teams), _FixedClock())
        for _ in range(5):
            await use_case.execute(_query())
        assert len(teams.rows) == 1

    async def test_a_deactivated_team_is_not_resurrected(self) -> None:
        """`active_only` must not narrow the existence check: a tenant who
        turned their only team off has made a decision, and quietly recreating
        it would undo that.

        **Deliberately named something other than the default.** With the
        deactivated team called "Support", the unique constraint would refuse
        the duplicate and this test would pass whether or not the existence
        re-check exists -- passing for the wrong reason. A different name means
        only the re-check can stop the insert.
        """
        teams = _Teams([_team("Admissions", active=False)])
        await ListTeams(_factory(teams), _FixedClock()).execute(_query(active_only=True))
        assert len(teams.rows) == 1

    async def test_without_a_clock_nothing_is_created(self) -> None:
        """The pre-existing construction site behaves exactly as before."""
        teams = _Teams()
        result = await ListTeams(_factory(teams)).execute(_query())
        assert result == []
        assert teams.add_attempts == 0


class TestTheRaceIsSettledByTheDatabase:
    async def test_two_simultaneous_readers_produce_one_team(self) -> None:
        """Both see zero teams and both try to insert -- the unique constraint
        decides, and the loser must not surface as an error to whichever agent
        happened to lose."""
        teams = _Teams()
        use_case = ListTeams(_factory(teams), _FixedClock())
        first, second = await asyncio.gather(
            use_case.execute(_query()), use_case.execute(_query())
        )
        assert len(teams.rows) == 1, "the race created a duplicate team"
        assert teams.add_attempts == 2, "the test did not actually exercise the race"
        # Both callers get a usable answer; neither sees the collision.
        assert len(first) == 1 and len(second) == 1
