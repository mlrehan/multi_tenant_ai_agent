"""A tenant owner can correct a widget's settings, and remove an unused one.

Editing the origin list is the half that matters most: get it wrong at
creation and the widget answers "this chat is not enabled for this website" on
the very page it was made for -- and before this existed the only remedy was to
create a second widget, because deleting the first was not possible either.

Deleting refuses when conversations reference the widget. The foreign key would
refuse it regardless; refusing here is what turns an IntegrityError into a
sentence naming the count and the alternative.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    ChatWidgetInUseError,
    ChatWidgetInvalidError,
    ChatWidgetNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.manage_chat_widget import (
    MANAGE_WIDGET_PERMISSION,
    DeleteChatWidget,
    DeleteChatWidgetCommand,
    UpdateChatWidget,
    UpdateChatWidgetCommand,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import ChatWidget, WidgetStatus

pytestmark = pytest.mark.asyncio

TENANT = uuid4()
OTHER_TENANT = uuid4()
ACTOR = uuid4()
WIDGET = uuid4()
ALLOWED = frozenset({MANAGE_WIDGET_PERMISSION})


class _FixedClock(Clock):
    def now(self) -> datetime:
        return datetime(2026, 8, 26, tzinfo=UTC)


def _widget(**overrides: object) -> ChatWidget:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    defaults: dict[str, object] = {
        "id": WIDGET,
        "tenant_id": TENANT,
        "knowledge_base_id": uuid4(),
        "name": "Help widget",
        "public_key": "wk_original",
        "allowed_origins": ["https://old.example"],
        "status": WidgetStatus.ACTIVE,
        "daily_question_limit": 500,
        "created_by_membership_id": uuid4(),
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return ChatWidget(**defaults)  # type: ignore[arg-type]


class _Widgets:
    def __init__(self, widget: ChatWidget | None, conversations: int = 0) -> None:
        self._widget = widget
        self._conversations = conversations
        self.updated: ChatWidget | None = None
        self.deleted: tuple[UUID, UUID] | None = None

    async def get_for_tenant(
        self, tenant_id: UUID, widget_id: UUID
    ) -> ChatWidget | None:
        if self._widget is None:
            return None
        # Mirrors the real repository's tenant scoping, so a cross-tenant id
        # cannot appear to succeed here and pass a test the database would fail.
        if self._widget.tenant_id != tenant_id or self._widget.id != widget_id:
            return None
        return self._widget

    async def update(self, widget: ChatWidget) -> None:
        self.updated = widget

    async def count_conversations(self, *, tenant_id: UUID, widget_id: UUID) -> int:
        del tenant_id, widget_id
        return self._conversations

    async def delete(self, *, tenant_id: UUID, widget_id: UUID) -> None:
        self.deleted = (tenant_id, widget_id)


class _Audit:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


class _Uow:
    def __init__(self, widgets: _Widgets) -> None:
        self.chat_widgets = widgets
        self.audit = _Audit()

    async def __aenter__(self) -> "_Uow":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _factory(widgets: _Widgets):
    def make(actor_id: UUID, tenant_id: UUID) -> _Uow:
        del actor_id, tenant_id
        return _Uow(widgets)

    return make


def _update_command(**overrides: object) -> UpdateChatWidgetCommand:
    defaults: dict[str, object] = {
        "actor_user_id": str(ACTOR),
        "tenant_id": str(TENANT),
        "widget_id": str(WIDGET),
        "permissions": ALLOWED,
        "name": "Renamed",
        "allowed_origins": ["https://new.example"],
        "daily_question_limit": 250,
    }
    defaults.update(overrides)
    return UpdateChatWidgetCommand(**defaults)  # type: ignore[arg-type]


def _delete_command(**overrides: object) -> DeleteChatWidgetCommand:
    defaults: dict[str, object] = {
        "actor_user_id": str(ACTOR),
        "tenant_id": str(TENANT),
        "widget_id": str(WIDGET),
        "permissions": ALLOWED,
    }
    defaults.update(overrides)
    return DeleteChatWidgetCommand(**defaults)  # type: ignore[arg-type]


class TestEditing:
    async def test_the_origin_list_can_be_corrected(self) -> None:
        widgets = _Widgets(_widget())
        await UpdateChatWidget(_factory(widgets), _FixedClock()).execute(
            _update_command()
        )
        assert widgets.updated is not None
        assert widgets.updated.allowed_origins == ["https://new.example"]

    async def test_a_pasted_page_url_is_stored_as_its_origin(self) -> None:
        """The mistake that started this: a browser never sends a path, so a
        stored page URL could never match."""
        widgets = _Widgets(_widget())
        await UpdateChatWidget(_factory(widgets), _FixedClock()).execute(
            _update_command(allowed_origins=["https://site.example/a/page.html"])
        )
        assert widgets.updated is not None
        assert widgets.updated.allowed_origins == ["https://site.example"]

    async def test_two_pages_on_one_site_collapse_to_one_origin(self) -> None:
        widgets = _Widgets(_widget())
        await UpdateChatWidget(_factory(widgets), _FixedClock()).execute(
            _update_command(
                allowed_origins=[
                    "https://site.example/one.html",
                    "https://site.example/two.html",
                ]
            )
        )
        assert widgets.updated is not None
        assert widgets.updated.allowed_origins == ["https://site.example"]

    async def test_the_public_key_is_never_changed(self) -> None:
        """It sits in script tags on sites this console does not control."""
        widgets = _Widgets(_widget())
        await UpdateChatWidget(_factory(widgets), _FixedClock()).execute(
            _update_command()
        )
        assert widgets.updated is not None
        assert widgets.updated.public_key == "wk_original"

    async def test_an_unusable_origin_list_is_refused(self) -> None:
        """A bare host has no scheme; storing it would permit nothing and the
        tenant would have no idea why."""
        widgets = _Widgets(_widget())
        with pytest.raises(ChatWidgetInvalidError):
            await UpdateChatWidget(_factory(widgets), _FixedClock()).execute(
                _update_command(allowed_origins=["site.example"])
            )
        assert widgets.updated is None

    async def test_an_empty_name_is_refused(self) -> None:
        widgets = _Widgets(_widget())
        with pytest.raises(ChatWidgetInvalidError):
            await UpdateChatWidget(_factory(widgets), _FixedClock()).execute(
                _update_command(name="   ")
            )
        assert widgets.updated is None

    async def test_without_the_permission_nothing_is_written(self) -> None:
        widgets = _Widgets(_widget())
        with pytest.raises(PermissionDeniedError):
            await UpdateChatWidget(_factory(widgets), _FixedClock()).execute(
                _update_command(permissions=frozenset())
            )
        assert widgets.updated is None

    async def test_another_tenants_widget_is_not_found_not_forbidden(self) -> None:
        """404, never 403: a widget in another tenant must not be provable to
        exist by the shape of the refusal."""
        widgets = _Widgets(_widget(tenant_id=OTHER_TENANT))
        with pytest.raises(ChatWidgetNotFoundError):
            await UpdateChatWidget(_factory(widgets), _FixedClock()).execute(
                _update_command()
            )


class TestDeleting:
    async def test_an_unused_widget_is_deleted(self) -> None:
        widgets = _Widgets(_widget(), conversations=0)
        await DeleteChatWidget(_factory(widgets)).execute(_delete_command())
        assert widgets.deleted == (TENANT, WIDGET)

    async def test_a_widget_with_conversations_is_refused(self) -> None:
        """Deleting would take the transcripts with it. Disabling already stops
        it answering, and the error says so."""
        widgets = _Widgets(_widget(), conversations=3)
        with pytest.raises(ChatWidgetInUseError) as caught:
            await DeleteChatWidget(_factory(widgets)).execute(_delete_command())
        assert widgets.deleted is None
        # The count and the alternative both appear: a refusal that does not
        # say what to do instead is a dead end.
        assert "3" in str(caught.value)
        assert "isable" in str(caught.value)

    async def test_without_the_permission_nothing_is_deleted(self) -> None:
        widgets = _Widgets(_widget())
        with pytest.raises(PermissionDeniedError):
            await DeleteChatWidget(_factory(widgets)).execute(
                _delete_command(permissions=frozenset())
            )
        assert widgets.deleted is None

    async def test_another_tenants_widget_is_not_found(self) -> None:
        widgets = _Widgets(_widget(tenant_id=OTHER_TENANT))
        with pytest.raises(ChatWidgetNotFoundError):
            await DeleteChatWidget(_factory(widgets)).execute(_delete_command())
        assert widgets.deleted is None

    async def test_the_deletion_is_audited_with_the_public_key(self) -> None:
        """That key is still in a script tag on someone's website; after this
        it stops working, and the audit row is what explains why."""
        widgets = _Widgets(_widget())
        factory = _factory(widgets)
        uow = factory(ACTOR, TENANT)

        def one_uow(actor_id: UUID, tenant_id: UUID) -> _Uow:
            del actor_id, tenant_id
            return uow

        await DeleteChatWidget(one_uow).execute(_delete_command())
        assert uow.audit.records, "the deletion was not audited"
        assert uow.audit.records[0]["metadata"]["public_key"] == "wk_original"  # type: ignore[index]
