"""Chatbot settings, and the ceiling a tenant cannot raise.

The regression that motivated this file was found by running the endpoint, not
by reading it: the ceiling check raised a bare `ValueError`, which
`api/exception_handlers.py` does not map, so a tenant admin typing 5,000
against a ceiling of 1,000 got a 500 and no explanation. The exhaustiveness
guard could not see it either -- it only scans `AiResourceError` subclasses.

So the first test here asserts the *exception type*, not just that something
was refused. Asserting "it raises" would have passed against the broken code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    AiResourceError,
    ChatbotSettingsInvalidError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.manage_chatbot import (
    MANAGE_CHATBOT_PERMISSION,
    UpdateChatbotSettings,
    UpdateChatbotSettingsCommand,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.tenancy.entitlements import TenantEntitlements
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FixedClock(Clock):
    def now(self) -> datetime:
        return NOW


def _command(tenant_id: UUID, *, daily_limit: int | None, **over: object):
    return UpdateChatbotSettingsCommand(
        actor_user_id=str(uuid4()),
        tenant_id=str(tenant_id),
        permissions=frozenset({MANAGE_CHATBOT_PERMISSION}),
        ai_chatbot_enabled=True,
        company_name="ABC Nursery",
        company_description=str(over.get("description", "A friendly nursery.")),
        industry=str(over.get("industry", "Early Years")),
        allow_human_handoff=True,
        add_ai_summary_as_internal_comment=False,
        allow_ai_for_unassigned_conversations=True,
        daily_message_limit=daily_limit,
        share_visitor_location=True,
    )


def _with_ceiling(uow: FakeAiResourceUnitOfWork, tenant_id: UUID, ceiling: int | None):
    uow.entitlements.stored[tenant_id] = TenantEntitlements(
        id=uuid4(),
        tenant_id=tenant_id,
        max_messages_per_day=ceiling,
        created_at=NOW,
        updated_at=NOW,
    )


class TestDailyLimitCeiling:
    async def test_above_the_ceiling_raises_a_mapped_error_not_a_bare_value_error(
        self,
    ) -> None:
        """**Asserts the type, deliberately.** The broken version raised a
        `ValueError`, which is unmapped and surfaced as a 500. A test that only
        checked "it refuses" would have passed against it."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        _with_ceiling(uow, tenant_id, 1000)

        with pytest.raises(ChatbotSettingsInvalidError) as caught:
            await UpdateChatbotSettings(uow, _FixedClock()).execute(
                _command(tenant_id, daily_limit=5000)
            )

        # Under the module base class, so the exhaustiveness guard can see it
        # and the handler map can answer 400 instead of 500.
        assert isinstance(caught.value, AiResourceError)
        assert "1000" in str(caught.value), "the message must name the ceiling"

    async def test_at_or_below_the_ceiling_is_stored(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        _with_ceiling(uow, tenant_id, 1000)

        settings = await UpdateChatbotSettings(uow, _FixedClock()).execute(
            _command(tenant_id, daily_limit=250)
        )
        assert settings.daily_message_limit == 250

    async def test_an_uncapped_plan_accepts_any_tenant_value(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        _with_ceiling(uow, tenant_id, None)

        settings = await UpdateChatbotSettings(uow, _FixedClock()).execute(
            _command(tenant_id, daily_limit=99_999)
        )
        assert settings.daily_message_limit == 99_999

    async def test_the_stored_value_is_still_clamped_when_read(self) -> None:
        """Belt and braces, and the reason both exist: a row written before the
        write-side check -- or by a future path that forgets it -- still cannot
        raise the cap, because the *read* clamps too."""
        entitlements = TenantEntitlements(
            id=uuid4(),
            tenant_id=uuid4(),
            max_messages_per_day=1000,
            created_at=NOW,
            updated_at=NOW,
        )
        assert entitlements.effective_daily_message_limit(5000) == 1000


class TestOversizedText:
    @pytest.mark.parametrize(
        ("field", "value"),
        [("description", "x" * 2001), ("industry", "y" * 101)],
    )
    async def test_over_length_text_is_refused_as_a_mapped_error(
        self, field: str, value: str
    ) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        _with_ceiling(uow, tenant_id, 1000)

        with pytest.raises(ChatbotSettingsInvalidError):
            await UpdateChatbotSettings(uow, _FixedClock()).execute(
                _command(tenant_id, daily_limit=None, **{field: value})
            )


class TestPermission:
    async def test_a_caller_without_the_permission_is_refused(self) -> None:
        """Permission before plan: a caller who lacks the permission must be
        told that, not that they are at a limit -- the two send them to
        different people for a fix."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        command = UpdateChatbotSettingsCommand(
            actor_user_id=str(uuid4()),
            tenant_id=str(tenant_id),
            permissions=frozenset(),
            ai_chatbot_enabled=True,
            company_name=None,
            company_description="",
            industry="",
            allow_human_handoff=True,
            add_ai_summary_as_internal_comment=False,
            allow_ai_for_unassigned_conversations=True,
            daily_message_limit=None,
            share_visitor_location=True,
        )
        with pytest.raises(PermissionDeniedError):
            await UpdateChatbotSettings(uow, _FixedClock()).execute(command)


class TestTheErrorIsActuallyMappedToFourHundred:
    def test_the_handler_map_contains_it(self) -> None:
        """The half the unit test above cannot prove on its own: raising the
        right type only helps if a handler answers 400 for it."""
        from fastapi import status

        from iam_platform.api.exception_handlers import _AI_RESOURCE_STATUS_MAP

        assert (
            _AI_RESOURCE_STATUS_MAP[ChatbotSettingsInvalidError]
            == status.HTTP_400_BAD_REQUEST
        )


class TestClaimReportsTheRightFailure:
    """Two ways a claim can fail, and they are not the same fact.

    Found by driving the endpoint: a conversation id that does not exist
    reported "another agent has already picked this up" -- untrue, and it sends
    an agent looking for a colleague who was never there. The conditional
    UPDATE returns zero rows for both cases, so the failure path has to
    disambiguate.
    """

    async def _claim(self, uow, conversation_id: UUID, tenant_id: UUID):
        from iam_platform.application.ai_resources.handoff import (
            AGENT_PERMISSION,
            ClaimConversation,
            ClaimConversationCommand,
        )

        return await ClaimConversation(uow, _FixedClock()).execute(
            ClaimConversationCommand(
                actor_user_id=str(uuid4()),
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                membership_id=str(uuid4()),
                permissions=frozenset({AGENT_PERMISSION}),
            )
        )

    async def test_a_conversation_that_does_not_exist_is_a_not_found(self) -> None:
        from iam_platform.application.ai_resources.exceptions import (
            ConversationNotFoundError,
        )

        uow = FakeAiResourceUnitOfWork()
        with pytest.raises(ConversationNotFoundError):
            await self._claim(uow, uuid4(), uuid4())

    async def test_one_already_claimed_is_a_conflict(self) -> None:
        """The genuine race, still reported as a conflict so the losing agent
        is told plainly rather than silently sharing the conversation."""
        from iam_platform.application.ai_resources.exceptions import (
            ConversationAlreadyClaimedError,
        )
        from iam_platform.domain.ai_resources.entities import (
            Conversation,
            ConversationState,
        )

        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        conversation = Conversation(
            id=uuid4(),
            tenant_id=tenant_id,
            membership_id=uuid4(),
            state=ConversationState.UNASSIGNED,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.conversations.by_id[conversation.id] = conversation

        await self._claim(uow, conversation.id, tenant_id)  # first agent wins
        with pytest.raises(ConversationAlreadyClaimedError):
            await self._claim(uow, conversation.id, tenant_id)  # second loses
