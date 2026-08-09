"""Turning a public widget off, and back on.

This is the control the rest of the public surface's design rests on. The
origin allowlist is only honest against browsers and the daily cap only bounds
spending after the money is gone, so when a widget is being abused the answer
is "switch it off". Until this use case existed that required a database
session -- which is not an incident response.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    ChatWidgetNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.manage_chat_widget import (
    MANAGE_WIDGET_PERMISSION,
    SetChatWidgetStatus,
    SetChatWidgetStatusCommand,
)
from iam_platform.domain.ai_resources.entities import ChatWidget, WidgetStatus
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ALLOWED = frozenset({MANAGE_WIDGET_PERMISSION})


def _widget(tenant_id: object) -> ChatWidget:
    return ChatWidget(
        id=uuid4(),
        tenant_id=tenant_id,  # type: ignore[arg-type]
        knowledge_base_id=uuid4(),
        name="Help",
        public_key="wk_public",
        allowed_origins=["https://help.acme.test"],
        created_by_membership_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


class TestSetChatWidgetStatus:
    async def test_disabling_marks_the_widget_disabled(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        widget = _widget(tenant_id)
        await uow.chat_widgets.add(widget)

        result = await SetChatWidgetStatus(uow).execute(
            SetChatWidgetStatusCommand(
                actor_user_id=str(uuid4()),
                tenant_id=str(tenant_id),
                widget_id=str(widget.id),
                permissions=ALLOWED,
                enabled=False,
            )
        )

        assert result.status is WidgetStatus.DISABLED
        stored = await uow.chat_widgets.get_for_tenant(tenant_id, widget.id)
        assert stored is not None and stored.status is WidgetStatus.DISABLED

    async def test_enabling_a_disabled_widget_restores_it(self) -> None:
        """The reverse transition has to work, or "switch it off" is a decision
        an operator can only make once."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        widget = _widget(tenant_id)
        widget.disable()
        await uow.chat_widgets.add(widget)

        result = await SetChatWidgetStatus(uow).execute(
            SetChatWidgetStatusCommand(
                actor_user_id=str(uuid4()),
                tenant_id=str(tenant_id),
                widget_id=str(widget.id),
                permissions=ALLOWED,
                enabled=True,
            )
        )

        assert result.status is WidgetStatus.ACTIVE

    async def test_an_actor_without_the_permission_is_refused(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        widget = _widget(tenant_id)
        await uow.chat_widgets.add(widget)

        with pytest.raises(PermissionDeniedError):
            await SetChatWidgetStatus(uow).execute(
                SetChatWidgetStatusCommand(
                    actor_user_id=str(uuid4()),
                    tenant_id=str(tenant_id),
                    widget_id=str(widget.id),
                    permissions=frozenset({"tenant.documents.read"}),
                    enabled=False,
                )
            )

        stored = await uow.chat_widgets.get_for_tenant(tenant_id, widget.id)
        assert stored is not None and stored.status is WidgetStatus.ACTIVE

    async def test_another_tenants_widget_is_not_found(self) -> None:
        """The cross-tenant case. RLS would stop this in the database too, but
        an explicit predicate is what makes the intent readable here -- and the
        error is 'not found', not 'forbidden', so an id from another tenant
        cannot be confirmed to exist."""
        uow = FakeAiResourceUnitOfWork()
        widget = _widget(uuid4())
        await uow.chat_widgets.add(widget)

        with pytest.raises(ChatWidgetNotFoundError):
            await SetChatWidgetStatus(uow).execute(
                SetChatWidgetStatusCommand(
                    actor_user_id=str(uuid4()),
                    tenant_id=str(uuid4()),  # a different tenant
                    widget_id=str(widget.id),
                    permissions=ALLOWED,
                    enabled=False,
                )
            )

    async def test_the_change_is_audited(self) -> None:
        """Turning a public endpoint on or off is exactly what an incident
        review reconstructs a timeline from."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        widget = _widget(tenant_id)
        await uow.chat_widgets.add(widget)

        await SetChatWidgetStatus(uow).execute(
            SetChatWidgetStatusCommand(
                actor_user_id=str(uuid4()),
                tenant_id=str(tenant_id),
                widget_id=str(widget.id),
                permissions=ALLOWED,
                enabled=False,
            )
        )

        entry = uow.audit.events[-1]
        assert entry["action"] == "ai_resources.chat_widget_status_changed"
        assert entry["metadata"]["status"] == "disabled"
