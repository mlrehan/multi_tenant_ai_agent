"""The install check: where a widget was last seen, and where it was refused.

"Did pasting the code work?" is the question a non-technical admin cannot
answer on their own -- and the commonest mistake (`www.` vs bare domain,
`http` vs `https`) fails invisibly: visitors see no chatbot and nothing in the
console says why. These pin that both outcomes are recorded, that a refusal is
still a refusal, and that the record can never cost a visitor their session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import WidgetOriginNotAllowedError
from iam_platform.application.ai_resources.public_chat import (
    StartWidgetSession,
    StartWidgetSessionCommand,
)
from iam_platform.domain.ai_resources.entities import ChatWidget
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _widget() -> ChatWidget:
    return ChatWidget(
        id=uuid4(), tenant_id=uuid4(), knowledge_base_id=uuid4(), name="Help",
        public_key="wk_test", allowed_origins=["https://falgoon.co.uk"],
        created_by_membership_id=uuid4(), created_at=NOW, updated_at=NOW,
        show_quick_reply_suggestions=False,
    )


@dataclass
class _Lookup:
    widget: ChatWidget

    async def find_by_public_key(self, public_key: str) -> ChatWidget | None:
        return self.widget if public_key == self.widget.public_key else None

    async def find_by_widget_id(self, widget_id: UUID) -> ChatWidget | None:
        return self.widget


def _start(uow: FakeAiResourceUnitOfWork, widget: ChatWidget) -> StartWidgetSession:
    return StartWidgetSession(_Lookup(widget), uow)  # type: ignore[arg-type]


async def test_an_allowed_site_is_recorded_as_seen_in_its_origin_form() -> None:
    uow, widget = FakeAiResourceUnitOfWork(), _widget()
    await _start(uow, widget).execute(
        StartWidgetSessionCommand(public_key="wk_test", origin="https://FALGOON.co.uk")
    )
    assert uow.chat_widgets.seen == [(widget.id, "https://falgoon.co.uk")]
    assert uow.chat_widgets.refused == []


async def test_a_disallowed_site_is_recorded_and_still_refused() -> None:
    uow, widget = FakeAiResourceUnitOfWork(), _widget()
    with pytest.raises(WidgetOriginNotAllowedError):
        await _start(uow, widget).execute(
            StartWidgetSessionCommand(public_key="wk_test", origin="https://www.falgoon.co.uk")
        )
    # Survives the refusal: recorded in a unit of work that closed first.
    assert uow.chat_widgets.refused == [(widget.id, "https://www.falgoon.co.uk")]
    assert uow.chat_widgets.seen == []


async def test_a_malformed_origin_is_never_stored() -> None:
    uow, widget = FakeAiResourceUnitOfWork(), _widget()
    with pytest.raises(WidgetOriginNotAllowedError):
        await _start(uow, widget).execute(
            StartWidgetSessionCommand(public_key="wk_test", origin="not a url <script>")
        )
    assert uow.chat_widgets.refused == []


async def test_a_failing_record_never_costs_the_visitor_their_session() -> None:
    uow, widget = FakeAiResourceUnitOfWork(), _widget()

    async def broken(**_: object) -> None:
        raise RuntimeError("database unavailable")

    uow.chat_widgets.record_seen = broken  # type: ignore[method-assign]
    resolved = await _start(uow, widget).execute(
        StartWidgetSessionCommand(public_key="wk_test", origin="https://falgoon.co.uk")
    )
    assert resolved.widget_id == widget.id
