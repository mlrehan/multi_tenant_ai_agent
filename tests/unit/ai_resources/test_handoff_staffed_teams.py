"""A visitor is only offered a team that someone is in.

Found in a live run: a tenant's only team had no members, the visitor chose
it, and was told "Someone will pick this up as soon as they can" -- with no
one staffing it and no push reaching a team member. The offer now lists only
teams with at least one *active* member, and choosing an empty team directly
is refused the same way an inactive one is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import TeamNotFoundError
from iam_platform.application.ai_resources.public_handoff import (
    OfferWidgetHandoff,
    SelectHandoffTeam,
    SelectHandoffTeamCommand,
    WidgetHandoffOfferQuery,
)
from iam_platform.domain.ai_resources.entities import ChatWidget
from iam_platform.domain.tenancy.teams import TenantTeam
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
ORIGIN = "https://nursery.example"


class _Clock:
    def now(self) -> datetime:
        return NOW


@dataclass
class _Lookup:
    widget: ChatWidget

    async def find_by_widget_id(self, widget_id: UUID) -> ChatWidget | None:
        return self.widget if widget_id == self.widget.id else None


def _setup() -> tuple[FakeAiResourceUnitOfWork, ChatWidget]:
    tenant_id = uuid4()
    widget = ChatWidget(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=uuid4(),
        name="Website chatbot",
        public_key="wk_test",
        allowed_origins=[ORIGIN],
        created_by_membership_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    return FakeAiResourceUnitOfWork(), widget


def _team(uow: FakeAiResourceUnitOfWork, tenant_id: UUID, name: str, *members: UUID) -> TenantTeam:
    team = TenantTeam(id=uuid4(), tenant_id=tenant_id, name=name, created_at=NOW, updated_at=NOW)
    uow.teams.teams[team.id] = team
    if members:
        uow.teams.members[team.id] = list(members)
    return team


async def _offered(uow: FakeAiResourceUnitOfWork, widget: ChatWidget) -> list[str]:
    offer = await OfferWidgetHandoff(_Lookup(widget), uow).execute(  # type: ignore[arg-type]
        WidgetHandoffOfferQuery(widget_id=widget.id, session_origin=ORIGIN)
    )
    assert offer is not None
    return [t.label for t in offer.teams]


class TestTheOfferListsOnlyStaffedTeams:
    async def test_an_empty_team_is_not_offered(self) -> None:
        uow, widget = _setup()
        _team(uow, widget.tenant_id, "Admissions", uuid4())
        _team(uow, widget.tenant_id, "Support")  # nobody in it
        assert await _offered(uow, widget) == ["Admissions"]

    async def test_a_team_whose_only_member_is_suspended_is_not_offered(self) -> None:
        uow, widget = _setup()
        suspended = uuid4()
        _team(uow, widget.tenant_id, "Support", suspended)
        uow.teams.inactive_members.add(suspended)
        assert await _offered(uow, widget) == []

    async def test_with_no_staffed_team_the_visitor_is_told_plainly(self) -> None:
        uow, widget = _setup()
        _team(uow, widget.tenant_id, "Support")
        offer = await OfferWidgetHandoff(_Lookup(widget), uow).execute(  # type: ignore[arg-type]
            WidgetHandoffOfferQuery(widget_id=widget.id, session_origin=ORIGIN)
        )
        assert offer is not None and offer.teams == []
        assert "not able to transfer you" in offer.message


class TestChoosingAnEmptyTeamIsRefused:
    async def test_a_direct_request_for_an_empty_team_is_not_found(self) -> None:
        uow, widget = _setup()
        empty = _team(uow, widget.tenant_id, "Support")
        with pytest.raises(TeamNotFoundError) as refused:
            await SelectHandoffTeam(_Lookup(widget), uow, _Clock()).execute(  # type: ignore[arg-type]
                SelectHandoffTeamCommand(
                    widget_id=widget.id,
                    session_id=uuid4(),
                    session_origin=ORIGIN,
                    team_id=empty.id,
                    reason="I'd like to speak to a person",
                )
            )
        # Refused before any conversation was created or moved.
        assert uow.conversations.by_id == {}
        # The widget shows the message to the visitor as-is: readable, and
        # not the team's raw id (which it used to be).
        assert str(empty.id) not in str(refused.value)
        assert "contact details" in str(refused.value)
