"""A widget's "questions per day" can't be set above the tenant's ceiling.

Every widget answer also spends from the tenant-wide daily counter, so a cap
above the tenant's platform ceiling is a number the widget can never reach.
Before this guard, the console created every widget at 500 on a plan of 100.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    ChatWidgetInvalidError,
    TokenBudgetExceededError,
    WidgetQuotaExceededError,
)
from iam_platform.application.ai_resources.manage_chat_widget import (
    CreateChatWidget,
    CreateChatWidgetCommand,
    UpdateChatWidget,
)
from iam_platform.application.ai_resources.public_chat import AskWidget, AskWidgetCommand
from iam_platform.domain.tenancy.entitlements import TenantEntitlements
from tests.unit.ai_resources.test_tenant_token_allowance import _MessageQuota
from tests.unit.ai_resources.test_widget_conversation_persistence import (
    ORIGIN,
    _FakeLookup,
    _FakeQuota,
)
from tests.unit.ai_resources.test_widget_conversation_persistence import (
    _widget as _persistence_widget,
)
from tests.unit.ai_resources.test_widget_edit_delete import (
    ACTOR,
    ALLOWED,
    TENANT,
    _Audit,
    _Entitlements,
    _factory,
    _FixedClock,
    _update_command,
    _widget,
    _Widgets,
)

pytestmark = pytest.mark.unit


def _plan(max_messages_per_day: int | None) -> TenantEntitlements:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    return TenantEntitlements(
        id=uuid4(),
        tenant_id=TENANT,
        created_at=now,
        updated_at=now,
        max_messages_per_day=max_messages_per_day,
    )


class TestEditing:
    async def test_a_new_cap_above_the_ceiling_is_refused_and_nothing_saved(self) -> None:
        widgets = _Widgets(_widget(daily_question_limit=50))
        with pytest.raises(ChatWidgetInvalidError, match="at most 100"):
            await UpdateChatWidget(_factory(widgets, _plan(100)), _FixedClock()).execute(
                _update_command(daily_question_limit=101)
            )
        assert widgets.updated is None

    async def test_the_ceiling_itself_is_allowed(self) -> None:
        widgets = _Widgets(_widget(daily_question_limit=50))
        await UpdateChatWidget(_factory(widgets, _plan(100)), _FixedClock()).execute(
            _update_command(daily_question_limit=100)
        )
        assert widgets.updated is not None
        assert widgets.updated.daily_question_limit == 100

    async def test_an_unchanged_legacy_cap_does_not_block_an_origin_edit(self) -> None:
        # Stored at 500 before the rule existed; the admin only adds a website.
        widgets = _Widgets(_widget(daily_question_limit=500))
        await UpdateChatWidget(_factory(widgets, _plan(100)), _FixedClock()).execute(
            _update_command(daily_question_limit=500, allowed_origins=["https://new.example"])
        )
        assert widgets.updated is not None
        assert widgets.updated.allowed_origins == ["https://new.example"]

    async def test_an_uncapped_tenant_may_choose_any_number(self) -> None:
        widgets = _Widgets(_widget(daily_question_limit=50))
        await UpdateChatWidget(_factory(widgets, _plan(None)), _FixedClock()).execute(
            _update_command(daily_question_limit=50_000)
        )
        assert widgets.updated is not None


class _CreateEntitlements(_Entitlements):
    async def count_chat_widgets(self, tenant_id: UUID) -> int:
        del tenant_id
        return 0


class _CreateUow:
    def __init__(self, plan: TenantEntitlements) -> None:
        self.entitlements = _CreateEntitlements(plan)
        self.audit = _Audit()
        self.added: list[object] = []

    async def __aenter__(self) -> _CreateUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class TestCreating:
    async def test_creating_above_the_ceiling_is_refused_before_anything_is_read(self) -> None:
        uow = _CreateUow(_plan(100))
        with pytest.raises(ChatWidgetInvalidError, match="at most 100"):
            await CreateChatWidget(lambda _a, _t: uow, _FixedClock()).execute(  # type: ignore[arg-type,return-value]
                CreateChatWidgetCommand(
                    actor_user_id=str(ACTOR),
                    tenant_id=str(TENANT),
                    knowledge_base_id=str(uuid4()),
                    permissions=ALLOWED,
                    name="Website chatbot",
                    allowed_origins=["https://site.example"],
                    daily_question_limit=500,
                )
            )


class _SpentPipeline:
    """The tenant's monthly token allowance is gone."""

    async def answer_from_namespace(self, question: str, **kwargs: object) -> object:
        del question, kwargs
        raise TokenBudgetExceededError(
            "this organisation's monthly allowance of 1,000,000 tokens is spent "
            "(1,000,412 used). It resets at the start of next month."
        )


class TestAVisitorIsNotToldTheTenantsPlan:
    async def test_a_spent_monthly_allowance_is_reworded_and_the_message_returned(self) -> None:
        quota = _MessageQuota()
        use_case = AskWidget(
            _FakeLookup([_ask_widget()]),  # type: ignore[arg-type]
            _FakeQuota(),  # type: ignore[arg-type]
            _SpentPipeline(),  # type: ignore[arg-type]
            tenant_quota=quota,
        )
        with pytest.raises(WidgetQuotaExceededError) as caught:
            await use_case.execute(
                AskWidgetCommand(
                    widget_id=WIDGET_ID,
                    knowledge_base_id=KB_ID,
                    question="When do you open?",
                    session_origin=ORIGIN,
                    session_id=uuid4(),
                )
            )
        message = str(caught.value)
        assert "token" not in message
        assert not any(ch.isdigit() for ch in message)
        # The daily message it reserved was never used, so it is handed back.
        assert quota.released == 1


_ASK_WIDGET = _persistence_widget()
WIDGET_ID = _ASK_WIDGET.id
KB_ID = _ASK_WIDGET.knowledge_base_id


def _ask_widget():  # type: ignore[no-untyped-def]
    return _ASK_WIDGET


class _Pipe:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys

    def incr(self, key: str) -> None:
        self._keys.append(key)

    def expire(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def execute(self) -> list[int]:
        return [1, True]  # type: ignore[list-item]


class _Redis:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def pipeline(self) -> _Pipe:
        return _Pipe(self.keys)


class _RecordingWidgetQuota:
    def __init__(self) -> None:
        self.zones: list[object] = []

    async def consume(self, *, widget_id: UUID, limit: int, **kwargs: object) -> bool:
        del widget_id, limit
        self.zones.append(kwargs.get("zone"))
        return True


class TestTheWidgetDayIsTheTenantsDay:
    async def test_the_counter_key_is_dated_in_the_tenants_zone(self) -> None:
        from zoneinfo import ZoneInfo

        from iam_platform.infrastructure.cache.widget_quota import RedisWidgetQuotaStore

        redis = _Redis()
        # A zone whose date differs from UTC's *right now*: UTC+14 is already
        # tomorrow from 10:00 UTC, UTC-12 is still yesterday before 12:00 UTC.
        # A fixed zone would make this pass for part of each day with the
        # zone ignored entirely -- the trap CLAUDE.md records.
        zone = ZoneInfo("Pacific/Kiritimati" if datetime.now(UTC).hour >= 10 else "Etc/GMT+12")
        assert f"{datetime.now(zone):%Y-%m-%d}" != f"{datetime.now(UTC):%Y-%m-%d}"
        await RedisWidgetQuotaStore(redis).consume(  # type: ignore[arg-type]
            widget_id=WIDGET_ID, limit=10, zone=zone
        )
        assert redis.keys == [f"widget-quota:{WIDGET_ID}:{datetime.now(zone):%Y-%m-%d}"]

    async def test_ask_widget_reserves_both_counters_in_one_zone(self) -> None:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Europe/London")
        widget_quota, tenant_quota = _RecordingWidgetQuota(), _MessageQuota()
        use_case = AskWidget(
            _FakeLookup([_ask_widget()]),  # type: ignore[arg-type]
            widget_quota,  # type: ignore[arg-type]
            _SpentPipeline(),  # type: ignore[arg-type]
            tenant_quota=tenant_quota,
        )

        async def _resolved(tenant_id: UUID) -> tuple[int | None, object]:
            del tenant_id
            return 50, zone

        use_case._daily_limit_and_zone = _resolved  # type: ignore[method-assign,assignment]
        with pytest.raises(WidgetQuotaExceededError):
            await use_case.execute(
                AskWidgetCommand(
                    widget_id=WIDGET_ID,
                    knowledge_base_id=KB_ID,
                    question="When do you open?",
                    session_origin=ORIGIN,
                    session_id=uuid4(),
                )
            )
        assert widget_quota.zones == [zone]


class TestARefusedQuestionIsNotCountedAsUsed:
    """Refused attempts used to stay in the counter: 10 answered and 40
    refused read "50 of 10", and raising the limit mid-day did not unblock."""

    async def test_the_tenant_counter_holds_only_what_was_allowed(self) -> None:
        from iam_platform.infrastructure.cache.tenant_quota import RedisTenantQuotaStore
        from tests.unit.ai_resources.test_usage_ledger import _FakeLedger, _FakeRedis

        store = RedisTenantQuotaStore(_FakeRedis(), ledger=_FakeLedger())  # type: ignore[arg-type]
        allowed = [await store.consume_message(tenant_id=TENANT, limit=3) for _ in range(7)]
        assert allowed.count(True) == 3
        assert await store.messages_used_today(tenant_id=TENANT) == 3
        # Raised mid-day: the very next question is allowed.
        assert await store.consume_message(tenant_id=TENANT, limit=4) is True

    async def test_the_widget_counter_holds_only_what_was_allowed(self) -> None:
        from iam_platform.infrastructure.cache.widget_quota import RedisWidgetQuotaStore
        from tests.unit.ai_resources.test_usage_ledger import _FakeRedis

        redis = _FakeRedis()
        store = RedisWidgetQuotaStore(redis)  # type: ignore[arg-type]
        allowed = [await store.consume(widget_id=WIDGET_ID, limit=2) for _ in range(5)]
        assert allowed.count(True) == 2
        assert list(redis.data.values()) == ["2"]
