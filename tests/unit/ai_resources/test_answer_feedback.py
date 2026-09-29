"""Rating an answer, from the console and from the public widget.

What matters here is not that a row gets written -- anything can do that -- but
*where its identity comes from*. Each door must authorize exactly as asking
through it does, and take the tenant and knowledge base from rows the server
loaded, never from the request. A visitor must not be able to file feedback
against another tenant by naming it, and a member must not be able to rate
answers from a knowledge base they could never have asked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.answer_feedback import (
    MAX_FEEDBACK_PER_VISITOR_SESSION,
    RecordAnswerFeedback,
    RecordAnswerFeedbackCommand,
    RecordWidgetFeedback,
    RecordWidgetFeedbackCommand,
)
from iam_platform.application.ai_resources.exceptions import (
    AnswerFeedbackInvalidError,
    AnswerFeedbackLimitError,
    KnowledgeBaseNotFoundError,
    PermissionDeniedError,
    WidgetUnavailableError,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.ai_resources.entities import (
    ChatWidget,
    KnowledgeBase,
    ResourceVisibility,
    WidgetStatus,
)
from iam_platform.domain.ai_resources.feedback import (
    AnswerFeedback,
    FeedbackInvalid,
    FeedbackRating,
)
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork

NOW = datetime(2026, 9, 27, tzinfo=UTC)
QUERY = "tenant.knowledge_bases.query"
ORIGIN = "https://nursery.example"


def _member(uow: FakeAiResourceUnitOfWork, tenant_id: UUID) -> tuple[UUID, TenantMembership]:
    user_id = uuid4()
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[membership.id] = membership
    return user_id, membership


def _kb(
    uow: FakeAiResourceUnitOfWork,
    tenant_id: UUID,
    owner: UUID,
    *,
    visibility: ResourceVisibility = ResourceVisibility.TENANT,
) -> KnowledgeBase:
    kb = KnowledgeBase(
        id=uuid4(),
        tenant_id=tenant_id,
        name="Policies",
        owner_membership_id=owner,
        visibility=visibility,
        vector_namespace=f"{tenant_id}/{uuid4()}",
        created_at=NOW,
        updated_at=NOW,
    )
    uow.knowledge_bases.by_id[kb.id] = kb
    return kb


def _console_command(user_id: UUID, tenant_id: UUID, kb_id: UUID, **overrides: object):
    values: dict[str, object] = {
        "actor_user_id": str(user_id),
        "tenant_id": str(tenant_id),
        "knowledge_base_id": str(kb_id),
        "permissions": frozenset({QUERY}),
        "rating": "down",
        "question": "When are fees due?",
        "answer": "Fees are due on the **1st** of each month. [1]",
        "comment": "Wrong date",
    }
    values.update(overrides)
    return RecordAnswerFeedbackCommand(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
class TestTheConsoleAuthorizesExactlyAsAskingDoes:
    async def test_a_member_who_could_ask_can_rate(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _member(uow, tenant_id)
        kb = _kb(uow, tenant_id, membership.id)

        await RecordAnswerFeedback(uow, FixedClock(NOW)).execute(  # type: ignore[arg-type]
            _console_command(user_id, tenant_id, kb.id)
        )

        [row] = uow.answer_feedback.rows
        assert row.membership_id == membership.id  # type: ignore[attr-defined]
        assert row.knowledge_base_id == kb.id  # type: ignore[attr-defined]
        assert row.rating is FeedbackRating.DOWN  # type: ignore[attr-defined]
        assert row.visitor_session_id is None  # type: ignore[attr-defined]

    async def test_without_the_ask_permission_nothing_is_stored(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _member(uow, tenant_id)
        kb = _kb(uow, tenant_id, membership.id)

        with pytest.raises(PermissionDeniedError):
            await RecordAnswerFeedback(uow, FixedClock(NOW)).execute(  # type: ignore[arg-type]
                _console_command(user_id, tenant_id, kb.id, permissions=frozenset())
            )
        assert uow.answer_feedback.rows == []

    async def test_a_knowledge_base_the_member_cannot_see_is_not_found(self) -> None:
        """404, never 403: a knowledge base someone could not have asked must
        not be provable to exist by rating it."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _member(uow, tenant_id)
        _, owner = _member(uow, tenant_id)
        hidden = _kb(uow, tenant_id, owner.id, visibility=ResourceVisibility.RESTRICTED)

        with pytest.raises(KnowledgeBaseNotFoundError):
            await RecordAnswerFeedback(uow, FixedClock(NOW)).execute(  # type: ignore[arg-type]
                _console_command(user_id, tenant_id, hidden.id)
            )
        assert uow.answer_feedback.rows == []

    async def test_another_tenants_knowledge_base_is_not_found(self) -> None:
        """In production RLS hides the row outright; this fake does not model
        RLS, so this proves the use case's own check -- the second of three
        mechanisms, with the composite FK as the third."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        other_tenant = uuid4()
        user_id, _ = _member(uow, tenant_id)
        _, stranger = _member(uow, other_tenant)
        foreign = _kb(uow, other_tenant, stranger.id)

        with pytest.raises(KnowledgeBaseNotFoundError):
            await RecordAnswerFeedback(uow, FixedClock(NOW)).execute(  # type: ignore[arg-type]
                _console_command(user_id, tenant_id, foreign.id)
            )
        assert uow.answer_feedback.rows == []

    async def test_a_rating_other_than_up_or_down_is_refused(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _member(uow, tenant_id)
        kb = _kb(uow, tenant_id, membership.id)

        with pytest.raises(AnswerFeedbackInvalidError):
            await RecordAnswerFeedback(uow, FixedClock(NOW)).execute(  # type: ignore[arg-type]
                _console_command(user_id, tenant_id, kb.id, rating="5-stars")
            )
        assert uow.answer_feedback.rows == []

    async def test_a_blank_comment_is_stored_as_none(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _member(uow, tenant_id)
        kb = _kb(uow, tenant_id, membership.id)

        await RecordAnswerFeedback(uow, FixedClock(NOW)).execute(  # type: ignore[arg-type]
            _console_command(user_id, tenant_id, kb.id, rating="up", comment="   ")
        )
        assert uow.answer_feedback.rows[0].comment is None  # type: ignore[attr-defined]


def _widget(tenant_id: UUID, kb_id: UUID, *, status: WidgetStatus = WidgetStatus.ACTIVE) -> ChatWidget:
    return ChatWidget(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=kb_id,
        name="Website",
        public_key="wk_test",
        allowed_origins=[ORIGIN],
        status=status,
        daily_question_limit=500,
        created_by_membership_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


class _Lookup:
    def __init__(self, widget: ChatWidget) -> None:
        self._widget = widget

    async def find_by_widget_id(self, widget_id: UUID) -> ChatWidget | None:
        return self._widget if widget_id == self._widget.id else None


def _visitor_command(widget: ChatWidget, **overrides: object) -> RecordWidgetFeedbackCommand:
    values: dict[str, object] = {
        "widget_id": widget.id,
        "session_id": uuid4(),
        "session_origin": ORIGIN,
        "rating": "up",
        "question": "What are your opening hours?",
        "answer": "We are open 7:30 to 18:00. [1]",
    }
    values.update(overrides)
    return RecordWidgetFeedbackCommand(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
class TestTheWidgetTakesIdentityFromTheWidgetRow:
    async def test_tenant_and_knowledge_base_come_from_the_widget(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id, kb_id = uuid4(), uuid4()
        widget = _widget(tenant_id, kb_id)

        await RecordWidgetFeedback(_Lookup(widget), uow, FixedClock(NOW)).execute(  # type: ignore[arg-type]
            _visitor_command(widget)
        )

        [row] = uow.answer_feedback.rows
        assert row.tenant_id == tenant_id  # type: ignore[attr-defined]
        assert row.knowledge_base_id == kb_id  # type: ignore[attr-defined]
        assert row.widget_id == widget.id  # type: ignore[attr-defined]
        assert row.membership_id is None  # type: ignore[attr-defined]
        # The unit of work was opened for the widget's tenant -- the RLS scope
        # every statement in it runs under.
        assert uow.last_tenant_id == tenant_id

    async def test_a_disabled_widget_takes_no_feedback(self) -> None:
        """Disabling a widget is the off switch; it must stop everything the
        widget can do, not only answering."""
        uow = FakeAiResourceUnitOfWork()
        widget = _widget(uuid4(), uuid4(), status=WidgetStatus.DISABLED)

        with pytest.raises(WidgetUnavailableError):
            await RecordWidgetFeedback(_Lookup(widget), uow, FixedClock(NOW)).execute(  # type: ignore[arg-type]
                _visitor_command(widget)
            )
        assert uow.answer_feedback.rows == []

    async def test_a_site_the_widget_is_not_allowed_on_is_refused(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        widget = _widget(uuid4(), uuid4())

        with pytest.raises(WidgetUnavailableError):
            await RecordWidgetFeedback(_Lookup(widget), uow, FixedClock(NOW)).execute(  # type: ignore[arg-type]
                _visitor_command(widget, session_origin="https://evil.example")
            )
        assert uow.answer_feedback.rows == []

    async def test_one_session_cannot_store_without_limit(self) -> None:
        """The bound that stops feedback on a public page being free storage
        for anyone holding one session token."""
        uow = FakeAiResourceUnitOfWork()
        widget = _widget(uuid4(), uuid4())
        session = uuid4()
        use_case = RecordWidgetFeedback(_Lookup(widget), uow, FixedClock(NOW))  # type: ignore[arg-type]

        for _ in range(MAX_FEEDBACK_PER_VISITOR_SESSION):
            await use_case.execute(_visitor_command(widget, session_id=session))
        with pytest.raises(AnswerFeedbackLimitError):
            await use_case.execute(_visitor_command(widget, session_id=session))

        assert len(uow.answer_feedback.rows) == MAX_FEEDBACK_PER_VISITOR_SESSION
        # A different session is unaffected.
        await use_case.execute(_visitor_command(widget))
        assert len(uow.answer_feedback.rows) == MAX_FEEDBACK_PER_VISITOR_SESSION + 1


class TestTheEntityRefusesWhatTheTableWould:
    def _make(self, **overrides: object) -> AnswerFeedback:
        values: dict[str, object] = {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "knowledge_base_id": uuid4(),
            "membership_id": uuid4(),
            "rating": FeedbackRating.UP,
            "question": "q",
            "answer": "a",
            "comment": None,
            "created_at": NOW,
        }
        values.update(overrides)
        return AnswerFeedback(**values)  # type: ignore[arg-type]

    def test_a_member_and_a_visitor_at_once_is_refused(self) -> None:
        with pytest.raises(FeedbackInvalid):
            self._make(widget_id=uuid4(), visitor_session_id=uuid4())

    def test_no_author_is_refused(self) -> None:
        with pytest.raises(FeedbackInvalid):
            self._make(membership_id=None)

    def test_half_a_visitor_is_refused(self) -> None:
        with pytest.raises(FeedbackInvalid):
            self._make(membership_id=None, visitor_session_id=uuid4())

    def test_an_oversized_answer_is_refused(self) -> None:
        with pytest.raises(FeedbackInvalid):
            self._make(answer="x" * 20001)

    def test_a_whole_visitor_alone_is_accepted(self) -> None:
        self._make(membership_id=None, widget_id=uuid4(), visitor_session_id=uuid4())


# ------------------------------------------------------------- reviewing

from iam_platform.application.ai_resources.answer_feedback import (  # noqa: E402
    ListAnswerFeedback,
    ListAnswerFeedbackQuery,
)

REVIEW = "tenant.conversations.view"


def _rated(uow: FakeAiResourceUnitOfWork, tenant_id: UUID, rating: FeedbackRating) -> None:
    uow.answer_feedback.rows.append(
        AnswerFeedback(
            id=uuid4(),
            tenant_id=tenant_id,
            knowledge_base_id=uuid4(),
            membership_id=uuid4(),
            rating=rating,
            question="q",
            answer="a",
            comment=None,
            created_at=NOW,
        )
    )


@pytest.mark.asyncio
class TestATenantReviewsOnlyItsOwnFeedback:
    async def test_counts_and_items_are_this_tenants(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        mine, theirs = uuid4(), uuid4()
        _rated(uow, mine, FeedbackRating.UP)
        _rated(uow, mine, FeedbackRating.DOWN)
        _rated(uow, mine, FeedbackRating.DOWN)
        _rated(uow, theirs, FeedbackRating.DOWN)

        page = await ListAnswerFeedback(uow).execute(  # type: ignore[arg-type]
            ListAnswerFeedbackQuery(
                actor_user_id=str(uuid4()), tenant_id=str(mine), permissions=frozenset({REVIEW})
            )
        )
        assert (page.total, page.helpful, page.not_helpful) == (3, 1, 2)
        assert all(r.tenant_id == mine for r in page.items)

    async def test_the_query_is_always_filtered_by_this_tenant(self) -> None:
        """RLS would confine it anyway; the tenant path must not rest on that
        alone, so the repository is always handed this tenant's own id."""
        uow = FakeAiResourceUnitOfWork()
        mine = uuid4()
        await ListAnswerFeedback(uow).execute(  # type: ignore[arg-type]
            ListAnswerFeedbackQuery(
                actor_user_id=str(uuid4()), tenant_id=str(mine), permissions=frozenset({REVIEW})
            )
        )
        assert uow.answer_feedback.last_list_tenant == mine  # type: ignore[attr-defined]

    async def test_without_the_conversations_permission_it_is_refused(self) -> None:
        """Feedback carries what visitors asked -- conversation content."""
        uow = FakeAiResourceUnitOfWork()
        with pytest.raises(PermissionDeniedError):
            await ListAnswerFeedback(uow).execute(  # type: ignore[arg-type]
                ListAnswerFeedbackQuery(
                    actor_user_id=str(uuid4()), tenant_id=str(uuid4()), permissions=frozenset()
                )
            )

    async def test_the_rating_filter_narrows_the_list_not_the_headline(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        mine = uuid4()
        _rated(uow, mine, FeedbackRating.UP)
        _rated(uow, mine, FeedbackRating.DOWN)
        page = await ListAnswerFeedback(uow).execute(  # type: ignore[arg-type]
            ListAnswerFeedbackQuery(
                actor_user_id=str(uuid4()),
                tenant_id=str(mine),
                permissions=frozenset({REVIEW}),
                rating="down",
            )
        )
        assert page.total == 1 and page.items[0].rating == "down"
        assert (page.helpful, page.not_helpful) == (1, 1)

    @pytest.mark.parametrize("bad", [{"rating": "5"}, {"channel": "email"}, {"limit": 0}, {"limit": 1000}])
    async def test_invalid_filters_are_refused(self, bad: dict[str, object]) -> None:
        uow = FakeAiResourceUnitOfWork()
        with pytest.raises(AnswerFeedbackInvalidError):
            await ListAnswerFeedback(uow).execute(  # type: ignore[arg-type]
                ListAnswerFeedbackQuery(
                    actor_user_id=str(uuid4()),
                    tenant_id=str(uuid4()),
                    permissions=frozenset({REVIEW}),
                    **bad,  # type: ignore[arg-type]
                )
            )


class _PlatformUow:
    def __init__(self, feedback: object) -> None:
        self.answer_feedback = feedback
        self.audit = self
        self.audited: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.audited.append(kwargs)

    async def __aenter__(self) -> _PlatformUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.asyncio
class TestThePlatformViewIsGatedAndAudited:
    async def _run(self, monkeypatch: pytest.MonkeyPatch, permissions: set[str]):  # type: ignore[no-untyped-def]
        from types import SimpleNamespace

        from iam_platform.application.ai_resources import platform_feedback
        from tests.unit.ai_resources.fakes import FakeAnswerFeedbackRepository

        async def fake_state(uow: object, actor: object, *, now: object) -> object:
            return SimpleNamespace(permissions=permissions)

        monkeypatch.setattr(platform_feedback, "compute_effective_platform_state", fake_state)
        repo = FakeAnswerFeedbackRepository()
        a, b = uuid4(), uuid4()
        holder = FakeAiResourceUnitOfWork()
        holder.answer_feedback = repo
        _rated(holder, a, FeedbackRating.DOWN)
        _rated(holder, b, FeedbackRating.UP)
        uow = _PlatformUow(repo)
        use_case = platform_feedback.ListPlatformAnswerFeedback(
            lambda _actor: uow, FixedClock(NOW)  # type: ignore[arg-type,return-value]
        )
        return use_case, uow, (a, b)

    async def test_it_sees_every_tenant_and_the_read_is_audited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from iam_platform.application.ai_resources.platform_feedback import PlatformFeedbackQuery

        use_case, uow, (a, b) = await self._run(monkeypatch, {"platform.model_configurations.manage"})
        page = await use_case.execute(PlatformFeedbackQuery(actor_user_id=str(uuid4())))
        assert {s.tenant_id for s in page.by_tenant} == {a, b}
        assert page.total == 2
        # Cross-tenant content read by someone outside the tenant: recorded.
        assert [e["action"] for e in uow.audited] == ["platform.answer_feedback.viewed"]

    async def test_without_the_operator_permission_nothing_is_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from iam_platform.application.ai_resources.exceptions import (
            ModelConfigurationManagementDeniedError,
        )
        from iam_platform.application.ai_resources.platform_feedback import PlatformFeedbackQuery

        use_case, uow, _ = await self._run(monkeypatch, set())
        with pytest.raises(ModelConfigurationManagementDeniedError):
            await use_case.execute(PlatformFeedbackQuery(actor_user_id=str(uuid4())))
        assert uow.audited == []
