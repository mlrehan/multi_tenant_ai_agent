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
class _FakeChatbotSettings:
    """Only the field the session endpoint reads."""

    allow_human_handoff: bool = True


class _FakeSettingsRepo:
    def __init__(self, settings: _FakeChatbotSettings | None) -> None:
        self._settings = settings

    async def get_for_tenant(self, tenant_id: UUID) -> _FakeChatbotSettings | None:
        del tenant_id
        return self._settings


class _FakeUow:
    def __init__(self, settings: _FakeChatbotSettings | None) -> None:
        self.chatbot_settings = _FakeSettingsRepo(settings)

    async def __aenter__(self) -> _FakeUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


_HANDOFF_PERMITTED = _FakeChatbotSettings(allow_human_handoff=True)


def _uow_factory(settings: _FakeChatbotSettings | None = _HANDOFF_PERMITTED):  # type: ignore[no-untyped-def]
    def factory(user_id: UUID, tenant_id: UUID) -> _FakeUow:
        del user_id, tenant_id
        return _FakeUow(settings)

    return factory


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

    # `**kwargs` rather than a fixed list: this fake stands in for a real
    # signature that has grown parameters twice now (memory, then token
    # attribution), and each time a narrower fake failed every caller with a
    # TypeError that had nothing to do with what the test was checking.
    async def answer_from_namespace(
        self, question: str, *, namespace: str, **kwargs: object
    ) -> str:
        del question, kwargs
        self.namespaces.append(namespace)
        return "answered"


class TestSessionIssuance:
    async def test_an_unknown_public_key_is_refused(self) -> None:
        use_case = StartWidgetSession(_FakeLookup(), _uow_factory())  # type: ignore[arg-type]

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
        use_case = StartWidgetSession(_FakeLookup([disabled]), _uow_factory())  # type: ignore[arg-type]

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
        use_case = StartWidgetSession(_FakeLookup([_widget()]), _uow_factory())  # type: ignore[arg-type]

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
        use_case = StartWidgetSession(_FakeLookup([widget]), _uow_factory())  # type: ignore[arg-type]

        resolved = await use_case.execute(
            StartWidgetSessionCommand(public_key="wk_public", origin=ORIGIN)
        )

        assert resolved.tenant_id == widget.tenant_id
        assert resolved.knowledge_base_id == widget.knowledge_base_id
        assert resolved.origin == ORIGIN


class TestTheWidgetIsToldWhatToLookLike:
    """The session response is what makes the embedded widget match the preview.

    The console writes name, title, avatar and greeting to `chat_widgets`; if
    the session does not carry them back, the widget renders whatever the
    script tag happened to say and the tenant's configuration is decorative.
    """

    async def test_the_configured_presentation_comes_back_with_the_session(
        self,
    ) -> None:
        widget = _widget(
            chatbot_name="Little Stars Assistant",
            chatbot_title="Admissions & Fees",
            avatar_key="nursery-star",
            greeting="Hello! Ask me about sessions.",
        )
        use_case = StartWidgetSession(_FakeLookup([widget]), _uow_factory())  # type: ignore[arg-type]

        resolved = await use_case.execute(
            StartWidgetSessionCommand(public_key="wk_public", origin=ORIGIN)
        )

        assert resolved.chatbot_name == "Little Stars Assistant"
        assert resolved.chatbot_title == "Admissions & Fees"
        assert resolved.avatar_key == "nursery-star"
        assert resolved.greeting == "Hello! Ask me about sessions."

    async def test_the_handoff_pill_is_offered_only_when_handoff_is_permitted(
        self,
    ) -> None:
        """Otherwise the visitor presses "Speak to a person", the wording
        reaches the model as an ordinary question, and nobody comes."""
        widget = _widget()
        allowed = StartWidgetSession(  # type: ignore[arg-type]
            _FakeLookup([widget]), _uow_factory(_FakeChatbotSettings(True))
        )
        refused = StartWidgetSession(  # type: ignore[arg-type]
            _FakeLookup([widget]), _uow_factory(_FakeChatbotSettings(False))
        )
        command = StartWidgetSessionCommand(public_key="wk_public", origin=ORIGIN)

        with_handoff = (await allowed.execute(command)).quick_replies
        without_handoff = (await refused.execute(command)).quick_replies

        assert "Speak to a person" in with_handoff
        assert "Speak to a person" not in without_handoff
        # The topic prompts are unaffected -- only the transfer pill is policy.
        assert without_handoff and set(without_handoff) < set(with_handoff)

    async def test_a_widget_with_suggestions_off_gets_no_pills_at_all(self) -> None:
        widget = _widget(show_quick_reply_suggestions=False)
        use_case = StartWidgetSession(_FakeLookup([widget]), _uow_factory())  # type: ignore[arg-type]

        resolved = await use_case.execute(
            StartWidgetSessionCommand(public_key="wk_public", origin=ORIGIN)
        )

        assert resolved.quick_replies == ()


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
                session_id=uuid4(),
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
                session_id=uuid4(),
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
                session_id=uuid4(),
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
                session_id=uuid4(),
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
                session_id=uuid4(),
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
                session_id=uuid4(),
            )
        )

        assert quota.calls == [widget.id]


class TestOriginsAreComparedAsOrigins:
    """A browser's `Origin` is `scheme://host[:port]` and never carries a path.

    An allowlist entry stored as a full page URL -- which is what someone
    pastes when asked where their widget lives -- therefore matched nothing a
    browser could ever send, and the widget failed with "not permitted on this
    site" on the very page it was configured for. Found in production.

    These tests pin both halves: the page URL now matches, and the things that
    must *not* match still don't.
    """

    def test_a_page_url_in_the_allowlist_matches_the_browsers_origin(self) -> None:
        widget = _widget(allowed_origins=["https://lsite.co.uk/nursery1/test.html"])
        assert widget.permits_origin("https://lsite.co.uk")

    def test_a_bare_origin_still_matches_itself(self) -> None:
        widget = _widget(allowed_origins=["https://lsite.co.uk"])
        assert widget.permits_origin("https://lsite.co.uk")

    def test_case_and_trailing_slash_are_irrelevant(self) -> None:
        widget = _widget(allowed_origins=["https://LSite.CO.UK/"])
        assert widget.permits_origin("https://lsite.co.uk")

    def test_a_different_scheme_is_still_refused(self) -> None:
        """Downgrading https to http must not be permitted by normalisation."""
        widget = _widget(allowed_origins=["https://lsite.co.uk/page.html"])
        assert not widget.permits_origin("http://lsite.co.uk")

    def test_a_different_subdomain_is_still_refused(self) -> None:
        widget = _widget(allowed_origins=["https://lsite.co.uk/page.html"])
        assert not widget.permits_origin("https://www.lsite.co.uk")

    def test_a_lookalike_domain_is_still_refused(self) -> None:
        """The suffix-matching trap: `evil-lsite.co.uk` is not `lsite.co.uk`."""
        widget = _widget(allowed_origins=["https://lsite.co.uk/page.html"])
        assert not widget.permits_origin("https://evil-lsite.co.uk")

    def test_a_different_port_is_still_refused(self) -> None:
        """Distinct origins to a browser, so distinct here."""
        widget = _widget(allowed_origins=["https://site.example/page.html"])
        assert not widget.permits_origin("https://site.example:8443")

    def test_an_entry_without_a_scheme_permits_nothing(self) -> None:
        """Refusing to guess: a bare host could be either scheme, and inventing
        one would silently permit a site the tenant never listed."""
        widget = _widget(allowed_origins=["lsite.co.uk"])
        assert not widget.permits_origin("https://lsite.co.uk")
        assert not widget.permits_origin("http://lsite.co.uk")

    def test_an_empty_allowlist_still_permits_nothing(self) -> None:
        widget = _widget(allowed_origins=[])
        assert not widget.permits_origin("https://lsite.co.uk")
