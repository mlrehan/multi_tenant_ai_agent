"""The public widget surface: what an anonymous stranger can and cannot reach.

This is the only place in the platform where an unauthenticated caller touches
tenant data, so these tests are about the constraints rather than the feature.
They drive the real use cases with fakes for I/O, because the properties under
test are decisions the use cases make -- which widget, which namespace, whether
to spend money -- not what Postgres or Redis do with them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    WidgetOriginNotAllowedError,
    WidgetQuotaExceededError,
    WidgetUnavailableError,
)
from iam_platform.application.ai_resources.public_chat import (
    AskWidget,
    AskWidgetCommand,
    StartWidgetSession,
    StartWidgetSessionCommand,
)
from iam_platform.domain.ai_resources.entities import ChatWidget, WidgetStatus

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ORIGIN = "https://help.acme.test"


def _widget(**overrides: object) -> ChatWidget:
    base: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "knowledge_base_id": uuid4(),
        "name": "Help",
        "public_key": "wk_public",
        "allowed_origins": [ORIGIN],
        "created_by_membership_id": uuid4(),
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return ChatWidget(**base)  # type: ignore[arg-type]


@dataclass
class _FakeLookup:
    widgets: list[ChatWidget] = field(default_factory=list)

    async def find_by_public_key(self, public_key: str) -> ChatWidget | None:
        return next((w for w in self.widgets if w.public_key == public_key), None)

    async def find_by_widget_id(self, widget_id: UUID) -> ChatWidget | None:
        return next((w for w in self.widgets if w.id == widget_id), None)


@dataclass
class _FakeQuota:
    allow: bool = True
    calls: list[UUID] = field(default_factory=list)

    async def consume(self, *, widget_id: UUID, limit: int) -> bool:
        del limit
        self.calls.append(widget_id)
        return self.allow


@dataclass
class _FakePipeline:
    """Records the namespace it was asked to answer from.

    That value is the whole question for a public surface: it decides which
    tenant's passages a stranger's question reaches.
    """

    namespaces: list[str] = field(default_factory=list)

    async def answer_from_namespace(self, question: str, *, namespace: str) -> str:
        del question
        self.namespaces.append(namespace)
        return "answered"


class TestSessionIssuance:
    async def test_an_unknown_public_key_is_refused(self) -> None:
        use_case = StartWidgetSession(_FakeLookup())

        with pytest.raises(WidgetUnavailableError):
            await use_case.execute(
                StartWidgetSessionCommand(public_key="wk_nope", origin=ORIGIN)
            )

    async def test_a_disabled_widget_reports_the_same_error_as_a_missing_one(
        self,
    ) -> None:
        """Distinguishing them would tell an anonymous caller whether a key
        they guessed is real -- a probing oracle for free."""
        disabled = _widget(status=WidgetStatus.DISABLED)
        use_case = StartWidgetSession(_FakeLookup([disabled]))

        with pytest.raises(WidgetUnavailableError) as disabled_error:
            await use_case.execute(
                StartWidgetSessionCommand(public_key="wk_public", origin=ORIGIN)
            )
        with pytest.raises(WidgetUnavailableError) as missing_error:
            await use_case.execute(
                StartWidgetSessionCommand(public_key="wk_absent", origin=ORIGIN)
            )

        assert str(disabled_error.value) == str(missing_error.value)

    @pytest.mark.parametrize(
        "origin",
        [
            None,
            "https://evil.test",
            # The case a naive suffix match accepts.
            "https://evil-help.acme.test",
            "http://help.acme.test",  # scheme differs
        ],
    )
    async def test_a_disallowed_origin_is_refused(self, origin: str | None) -> None:
        use_case = StartWidgetSession(_FakeLookup([_widget()]))

        with pytest.raises(WidgetOriginNotAllowedError):
            await use_case.execute(
                StartWidgetSessionCommand(public_key="wk_public", origin=origin)
            )

    async def test_an_allowed_origin_is_scoped_to_the_widgets_own_knowledge_base(
        self,
    ) -> None:
        """The positive control, and the isolation property in one: the caller
        never names a tenant or knowledge base, so what comes back is whatever
        the widget row says and nothing else."""
        widget = _widget()
        use_case = StartWidgetSession(_FakeLookup([widget]))

        resolved = await use_case.execute(
            StartWidgetSessionCommand(public_key="wk_public", origin=ORIGIN)
        )

        assert resolved.tenant_id == widget.tenant_id
        assert resolved.knowledge_base_id == widget.knowledge_base_id
        assert resolved.origin == ORIGIN


class TestAskingIsReCheckedEveryTime:
    async def test_a_widget_disabled_mid_session_stops_answering_immediately(
        self,
    ) -> None:
        """A session token lives thirty minutes. Without re-reading the widget,
        "disable this widget" would mean "in half an hour" -- not what an
        operator dealing with abuse means."""
        widget = _widget(status=WidgetStatus.DISABLED)
        pipeline = _FakePipeline()
        use_case = AskWidget(_FakeLookup([widget]), _FakeQuota(), pipeline)  # type: ignore[arg-type]

        with pytest.raises(WidgetUnavailableError):
            await use_case.execute(
                AskWidgetCommand(
                    widget_id=widget.id,
                    knowledge_base_id=widget.knowledge_base_id,
                    question="anything",
                    session_origin=ORIGIN,
                )
            )

        assert pipeline.namespaces == []

    async def test_an_origin_removed_mid_session_stops_answering(self) -> None:
        widget = _widget(allowed_origins=["https://somewhere-else.test"])
        pipeline = _FakePipeline()
        use_case = AskWidget(_FakeLookup([widget]), _FakeQuota(), pipeline)  # type: ignore[arg-type]

        with pytest.raises(WidgetOriginNotAllowedError):
            await use_case.execute(
                AskWidgetCommand(
                    widget_id=widget.id,
                    knowledge_base_id=widget.knowledge_base_id,
                    question="anything",
                    session_origin=ORIGIN,
                )
            )

        assert pipeline.namespaces == []

    async def test_a_token_naming_a_different_knowledge_base_is_refused(self) -> None:
        """The cross-tenant case in its most direct form: a token whose `kb`
        claim does not match the widget's current knowledge base is stale at
        best and forged at worst. The row is the record of truth, not the
        claim."""
        widget = _widget()
        pipeline = _FakePipeline()
        use_case = AskWidget(_FakeLookup([widget]), _FakeQuota(), pipeline)  # type: ignore[arg-type]

        with pytest.raises(WidgetUnavailableError):
            await use_case.execute(
                AskWidgetCommand(
                    widget_id=widget.id,
                    knowledge_base_id=uuid4(),  # someone else's
                    question="anything",
                    session_origin=ORIGIN,
                )
            )

        assert pipeline.namespaces == []


class TestTheNamespaceIsDerivedNotSupplied:
    async def test_the_namespace_comes_from_the_widget_row(self) -> None:
        widget = _widget()
        pipeline = _FakePipeline()
        use_case = AskWidget(_FakeLookup([widget]), _FakeQuota(), pipeline)  # type: ignore[arg-type]

        await use_case.execute(
            AskWidgetCommand(
                widget_id=widget.id,
                knowledge_base_id=widget.knowledge_base_id,
                question="what are your hours?",
                session_origin=ORIGIN,
            )
        )

        assert pipeline.namespaces == [
            f"{widget.tenant_id}/{widget.knowledge_base_id}"
        ]

    def test_the_command_has_no_namespace_or_tenant_field(self) -> None:
        """Structural: there is nothing for a request to populate."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(AskWidgetCommand)}
        assert "namespace" not in fields
        assert "tenant_id" not in fields


class TestQuota:
    async def test_an_exhausted_quota_refuses_before_generation(self) -> None:
        """Checked *before* the pipeline runs. Afterwards would only record
        that the money had already been spent."""
        widget = _widget()
        pipeline = _FakePipeline()
        use_case = AskWidget(
            _FakeLookup([widget]), _FakeQuota(allow=False), pipeline  # type: ignore[arg-type]
        )

        with pytest.raises(WidgetQuotaExceededError):
            await use_case.execute(
                AskWidgetCommand(
                    widget_id=widget.id,
                    knowledge_base_id=widget.knowledge_base_id,
                    question="anything",
                    session_origin=ORIGIN,
                )
            )

        assert pipeline.namespaces == [], "quota must be checked before spending"

    async def test_quota_is_consumed_for_the_widget_being_asked(self) -> None:
        widget = _widget()
        quota = _FakeQuota()
        use_case = AskWidget(_FakeLookup([widget]), quota, _FakePipeline())  # type: ignore[arg-type]

        await use_case.execute(
            AskWidgetCommand(
                widget_id=widget.id,
                knowledge_base_id=widget.knowledge_base_id,
                question="anything",
                session_origin=ORIGIN,
            )
        )

        assert quota.calls == [widget.id]
