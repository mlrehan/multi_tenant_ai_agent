"""A long conversation must stay readable to the agent working it.

**The defect this exists for was invisible from the tenant side and total from
the visitor's.** `GetConversationMessages` read `list_page(limit=50, offset=0)`
-- the *first* fifty turns. Once a thread passed fifty, every new message
landed outside the window: the agent's console polled every four seconds and
faithfully re-rendered the same opening fifty turns for ever, while the
visitor's replies piled up unseen in Postgres. The conversation was working
perfectly and looked dead from one end.

Found by reading the live table (seq 76 present and stored) rather than by any
test, because every test drove threads far shorter than one page. So these
build a thread *longer* than the page deliberately -- that is the whole point.

Driven through the use case, not the repository. A repository-level test passes
just as happily when the use case calls the wrong method, which is exactly what
a first attempt at this proved: the mutation survived.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.manage_conversation import (
    ConversationMessagesQuery,
    GetConversationMessages,
)
from iam_platform.domain.ai_resources.entities import (
    Conversation,
    ConversationMessage,
    ConversationState,
    MessageRole,
)
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
VIEW = frozenset({"tenant.conversations.view"})


def _thread(turns: int) -> tuple[FakeAiResourceUnitOfWork, UUID, UUID, UUID]:
    uow = FakeAiResourceUnitOfWork()
    tenant_id, user_id = uuid4(), uuid4()
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[membership.id] = membership

    conversation = Conversation(
        id=uuid4(),
        tenant_id=tenant_id,
        assistant_id=None,
        membership_id=membership.id,
        visitor_session_id=None,
        widget_id=None,
        state=ConversationState.HUMAN_ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.conversations.by_id[conversation.id] = conversation
    uow.conversation_messages.by_conversation[conversation.id] = [
        ConversationMessage(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            seq=i,
            role=MessageRole.USER,
            content=f"turn {i}",
            created_at=NOW,
        )
        for i in range(1, turns + 1)
    ]
    return uow, tenant_id, user_id, conversation.id


async def _read(
    uow: FakeAiResourceUnitOfWork,
    tenant_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    *,
    limit: int = 50,
    before_seq: int | None = None,
):  # type: ignore[no-untyped-def]
    return await GetConversationMessages(uow).execute(  # type: ignore[arg-type]
        ConversationMessagesQuery(
            actor_user_id=str(user_id),
            tenant_id=str(tenant_id),
            conversation_id=str(conversation_id),
            permissions=VIEW,
            limit=limit,
            before_seq=before_seq,
        )
    )


class TestTheAgentSeesWhatIsBeingSaidNow:
    async def test_a_thread_longer_than_a_page_shows_its_newest_turns(self) -> None:
        """The regression. With the old read this returned turns 1-50 of 76,
        so the last twenty-six -- every message since the agent joined -- were
        unreachable."""
        uow, tenant_id, user_id, cid = _thread(76)
        _, messages = await _read(uow, tenant_id, user_id, cid)
        assert [m.seq for m in messages] == list(range(27, 77))

    async def test_a_new_message_appears_on_the_next_read(self) -> None:
        """What the four-second poll actually needs. Under the old read the
        window never moved, so a visitor could type all day into a console that
        showed nothing new."""
        uow, tenant_id, user_id, cid = _thread(76)
        uow.conversation_messages.by_conversation[cid].append(
            ConversationMessage(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=cid,
                seq=77,
                role=MessageRole.USER,
                content="are you still there?",
                created_at=NOW,
            )
        )
        _, messages = await _read(uow, tenant_id, user_id, cid)
        assert messages[-1].content == "are you still there?"

    async def test_a_short_thread_is_returned_whole(self) -> None:
        """The common case, unchanged -- a fix that only worked for long
        threads would have traded one broken shape for another."""
        uow, tenant_id, user_id, cid = _thread(6)
        view, messages = await _read(uow, tenant_id, user_id, cid)
        assert [m.seq for m in messages] == [1, 2, 3, 4, 5, 6]
        assert view.total_messages == 6


class TestPagingBackwards:
    async def test_the_cursor_walks_upward_without_gaps_or_repeats(self) -> None:
        uow, tenant_id, user_id, cid = _thread(76)
        _, newest = await _read(uow, tenant_id, user_id, cid, limit=10)
        _, older = await _read(
            uow, tenant_id, user_id, cid, limit=10, before_seq=newest[0].seq
        )
        assert [m.seq for m in newest] == list(range(67, 77))
        assert [m.seq for m in older] == list(range(57, 67))
        assert not {m.seq for m in older} & {m.seq for m in newest}

    async def test_turns_arriving_mid_scroll_do_not_shift_the_page(self) -> None:
        """Why this is a `seq` cursor and not an offset. The thread is live: an
        offset counts from a position that has already moved, so a page taken
        after three new messages would hand back turns already on screen."""
        uow, tenant_id, user_id, cid = _thread(76)
        _, newest = await _read(uow, tenant_id, user_id, cid, limit=10)
        for extra in range(77, 80):
            uow.conversation_messages.by_conversation[cid].append(
                ConversationMessage(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    conversation_id=cid,
                    seq=extra,
                    role=MessageRole.USER,
                    content=f"turn {extra}",
                    created_at=NOW,
                )
            )
        _, older = await _read(
            uow, tenant_id, user_id, cid, limit=10, before_seq=newest[0].seq
        )
        assert [m.seq for m in older] == list(range(57, 67))

    async def test_the_total_reports_the_thread_not_the_page(self) -> None:
        """What tells a client there is more above without fetching a page to
        find out it is empty."""
        uow, tenant_id, user_id, cid = _thread(76)
        view, messages = await _read(uow, tenant_id, user_id, cid, limit=10)
        assert view.total_messages == 76
        assert len(messages) == 10
