"""The tenant's chatbot configuration actually reaches the model.

Everything on the AI Chatbot page -- role, avoid rules, personality, response
length, company context -- was stored and never sent: `build_system_prompt`
had no caller, so every answer used the bare platform policy. These tests
drive the real `answer_from_namespace` (the path both the widget and the
console's Ask panel take) and inspect the system prompt the model received.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.answer_question import (
    SYSTEM_PROMPT,
    AnswerQuestion,
)
from iam_platform.domain.ai_resources.chatbot import (
    DEFAULT_COMPANY_NAME,
    Personality,
    ResponseLength,
    TenantChatbotSettings,
)
from iam_platform.domain.tenancy.teams import TenantTeam
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork
from tests.unit.ai_resources.test_answer_question import _chunk, _FakeChatModel, _FakeVectorSearch
from tests.unit.ai_resources.test_answer_question_cases import _OrderPreservingReranker

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _settings(tenant_id: UUID, **kwargs: object) -> TenantChatbotSettings:
    return TenantChatbotSettings(
        id=uuid4(), tenant_id=tenant_id, created_at=NOW, updated_at=NOW, **kwargs  # type: ignore[arg-type]
    )


def _staff_a_team(uow: FakeAiResourceUnitOfWork, tenant_id: UUID, *, active: bool = True) -> None:
    team = TenantTeam(
        id=uuid4(), tenant_id=tenant_id, name="Support", is_active=active,
        created_at=NOW, updated_at=NOW,
    )
    uow.teams.teams[team.id] = team
    uow.teams.members[team.id] = [uuid4()]


async def _prompt(uow: FakeAiResourceUnitOfWork, tenant_id: UUID) -> str:
    chat = _FakeChatModel()
    pipeline = AnswerQuestion(
        lambda _u, _t: uow,  # type: ignore[arg-type,return-value]
        _FakeVectorSearch(chunks=[_chunk("We open at 8am.")]),  # type: ignore[arg-type]
        _OrderPreservingReranker(),
        chat,  # type: ignore[arg-type]
    )
    stream = await pipeline.answer_from_namespace(
        "When do you open?", namespace=f"{tenant_id}/{uuid4()}", tenant_id=tenant_id, channel="widget"
    )
    [p async for p in stream.tokens]
    return str(chat.calls[0][2])


class TestTheTenantsBriefIsSent:
    async def test_role_avoid_and_company_reach_the_model_beneath_the_platform_policy(
        self,
    ) -> None:
        uow, tenant_id = FakeAiResourceUnitOfWork(), uuid4()
        uow.chatbot_settings.stored[tenant_id] = _settings(
            tenant_id,
            company_name="Little Acorns",
            role_instructions="ROLE-MARKER: help parents with admissions.",
            avoid_instructions="AVOID-MARKER: never discuss staff salaries.",
        )
        prompt = await _prompt(uow, tenant_id)

        assert prompt.startswith(SYSTEM_PROMPT)  # platform policy first, intact
        for marker in ("Little Acorns", "ROLE-MARKER", "AVOID-MARKER"):
            assert marker in prompt
        assert prompt.index("ROLE-MARKER") > len(SYSTEM_PROMPT)

    async def test_personality_and_length_reach_the_model(self) -> None:
        uow, tenant_id = FakeAiResourceUnitOfWork(), uuid4()
        uow.chatbot_settings.stored[tenant_id] = _settings(
            tenant_id,
            personality=Personality.REASSURING,
            response_length=ResponseLength.CONCISE,
        )
        prompt = await _prompt(uow, tenant_id)
        assert "anxious" in prompt
        assert "two or three short sentences" in prompt

    async def test_with_no_settings_the_bot_uses_this_tenants_own_name(self) -> None:
        # The shipped default company name belongs to one real tenant; every
        # other tenant must be introduced by its own display name.
        uow, tenant_id = FakeAiResourceUnitOfWork(), uuid4()
        uow.chatbot_settings.display_names[tenant_id] = "Northwind Nursery"
        prompt = await _prompt(uow, tenant_id)
        assert "Northwind Nursery" in prompt
        assert DEFAULT_COMPANY_NAME not in prompt


class TestHandoffGuidanceMatchesWhatTheWidgetOffers:
    async def test_a_staffed_team_means_a_transfer_is_offered(self) -> None:
        uow, tenant_id = FakeAiResourceUnitOfWork(), uuid4()
        _staff_a_team(uow, tenant_id)
        prompt = await _prompt(uow, tenant_id)
        assert "Offer a human transfer" in prompt
        assert "transfer is not available" not in prompt

    async def test_no_staffed_team_means_the_model_is_told_there_is_none(self) -> None:
        uow, tenant_id = FakeAiResourceUnitOfWork(), uuid4()
        prompt = await _prompt(uow, tenant_id)
        assert "transfer is not available" in prompt

    async def test_an_inactive_staffed_team_does_not_count(self) -> None:
        uow, tenant_id = FakeAiResourceUnitOfWork(), uuid4()
        _staff_a_team(uow, tenant_id, active=False)
        assert "transfer is not available" in await _prompt(uow, tenant_id)

    async def test_a_tenant_that_switched_handoff_off_is_told_so(self) -> None:
        uow, tenant_id = FakeAiResourceUnitOfWork(), uuid4()
        _staff_a_team(uow, tenant_id)
        uow.chatbot_settings.stored[tenant_id] = _settings(tenant_id, allow_human_handoff=False)
        assert "transfer is not available" in await _prompt(uow, tenant_id)


class TestAFailedSettingsReadFallsBackToThePlatformPolicy:
    async def test_the_answer_still_goes_out_with_the_platform_policy_alone(self) -> None:
        uow, tenant_id = FakeAiResourceUnitOfWork(), uuid4()

        async def _broken(_tenant_id: UUID) -> None:
            raise RuntimeError("database unavailable")

        uow.chatbot_settings.get_for_tenant = _broken  # type: ignore[method-assign]
        assert await _prompt(uow, tenant_id) == SYSTEM_PROMPT
