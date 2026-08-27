"""Repositories for entitlements, chatbot settings, teams and handoff routing.

Kept in their own module rather than appended to `ai_resources.py` because
they span two domains -- `tenancy` owns entitlements and teams, `ai_resources`
owns chatbot settings -- and the file they would otherwise join is already the
largest in the package.

**The concurrency-safe claim lives here, not in the entity.** Two agents can
both load an unassigned conversation and both decide to claim it; nothing in
the domain object can prevent that, because by the time either calls a method
the read is already stale. `SqlConversationHandoffRepository.claim` settles it
with a conditional `UPDATE ... WHERE state = 'unassigned'`, which Postgres
serialises: exactly one statement matches a row, the other reports zero
affected, and the loser is told so instead of silently stealing the thread.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.domain.ai_resources.chatbot import (
    DEFAULT_INDUSTRY,
    TenantChatbotSettings,
    coerce_personality,
    coerce_response_length,
)
from iam_platform.domain.ai_resources.entities import ConversationState, HandoffInitiator
from iam_platform.domain.ai_resources.push import PushSubscription
from iam_platform.domain.tenancy.entitlements import TenantEntitlements
from iam_platform.domain.tenancy.teams import TenantTeam
from iam_platform.infrastructure.db.models.ai_resources import (
    AiAssistantModel,
    ChatWidgetModel,
    ConversationModel,
    KnowledgeBaseModel,
)
from iam_platform.infrastructure.db.models.tenancy import (
    PushSubscriptionModel,
    TenantChatbotSettingsModel,
    TenantEntitlementModel,
    TenantTeamMemberModel,
    TenantTeamModel,
)
from iam_platform.infrastructure.db.models.tenant_authz import (
    TenantMembershipRoleModel,
    TenantPermissionModel,
    TenantRolePermissionModel,
)


def _entitlements(row: TenantEntitlementModel) -> TenantEntitlements:
    return TenantEntitlements(
        id=row.id,
        tenant_id=row.tenant_id,
        max_knowledge_bases=row.max_knowledge_bases,
        max_chat_widgets=row.max_chat_widgets,
        max_messages_per_day=row.max_messages_per_day,
        max_tokens_per_month=row.max_tokens_per_month,
        allow_invite_members=row.allow_invite_members,
        allow_create_roles=row.allow_create_roles,
        updated_by_user_id=row.updated_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlTenantEntitlementRepository:
    """Reads run under whichever role the session holds.

    A tenant session may read its own row (RLS policy) and cannot write one at
    all (the grant is SELECT-only). A platform session holds BYPASSRLS and does
    both. That split is enforced by the database, so this class needs no
    branch for it -- and could not be trusted to have one anyway.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_tenant(self, tenant_id: UUID) -> TenantEntitlements | None:
        row = await self._session.scalar(
            select(TenantEntitlementModel).where(
                TenantEntitlementModel.tenant_id == tenant_id
            )
        )
        return _entitlements(row) if row else None

    async def list_all(self) -> list[TenantEntitlements]:
        rows = await self._session.scalars(select(TenantEntitlementModel))
        return [_entitlements(r) for r in rows]

    async def upsert(self, entitlements: TenantEntitlements) -> None:
        # Upsert on `tenant_id`, not on the primary key: the caller may have
        # built this object from `defaults_for()` with a fresh id, and two
        # rows for one tenant would mean two answers to "what is this tenant
        # allowed to do" decided by row order.
        stmt = (
            pg_insert(TenantEntitlementModel)
            .values(
                id=entitlements.id,
                tenant_id=entitlements.tenant_id,
                max_knowledge_bases=entitlements.max_knowledge_bases,
                max_chat_widgets=entitlements.max_chat_widgets,
                max_messages_per_day=entitlements.max_messages_per_day,
                max_tokens_per_month=entitlements.max_tokens_per_month,
                allow_invite_members=entitlements.allow_invite_members,
                allow_create_roles=entitlements.allow_create_roles,
                updated_by_user_id=entitlements.updated_by_user_id,
                created_at=entitlements.created_at,
                updated_at=entitlements.updated_at,
            )
            .on_conflict_do_update(
                index_elements=[TenantEntitlementModel.tenant_id],
                set_={
                    "max_knowledge_bases": entitlements.max_knowledge_bases,
                    "max_chat_widgets": entitlements.max_chat_widgets,
                    "max_messages_per_day": entitlements.max_messages_per_day,
                    "max_tokens_per_month": entitlements.max_tokens_per_month,
                    "allow_invite_members": entitlements.allow_invite_members,
                    "allow_create_roles": entitlements.allow_create_roles,
                    "updated_by_user_id": entitlements.updated_by_user_id,
                    "updated_at": entitlements.updated_at,
                },
            )
        )
        await self._session.execute(stmt)

    # -- live counts, never a stored counter --------------------------------

    async def count_knowledge_bases(self, tenant_id: UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(KnowledgeBaseModel)
                .where(KnowledgeBaseModel.tenant_id == tenant_id)
            )
            or 0
        )

    async def count_chat_widgets(self, tenant_id: UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(ChatWidgetModel)
                .where(ChatWidgetModel.tenant_id == tenant_id)
            )
            or 0
        )

    async def count_assistants(self, tenant_id: UUID) -> int:
        # Archived assistants are excluded: they cannot be used, so counting
        # them would let a tenant lock itself out of its own quota by
        # archiving rather than by having too many live assistants.
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(AiAssistantModel)
                .where(
                    AiAssistantModel.tenant_id == tenant_id,
                    AiAssistantModel.status != "archived",
                )
            )
            or 0
        )


class SqlTenantChatbotSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_tenant(self, tenant_id: UUID) -> TenantChatbotSettings | None:
        row = await self._session.scalar(
            select(TenantChatbotSettingsModel).where(
                TenantChatbotSettingsModel.tenant_id == tenant_id
            )
        )
        if row is None:
            return None
        return TenantChatbotSettings(
            id=row.id,
            tenant_id=row.tenant_id,
            ai_chatbot_enabled=row.ai_chatbot_enabled,
            company_name=row.company_name,
            company_description=row.company_description or "",
            industry=row.industry or DEFAULT_INDUSTRY,
            allow_human_handoff=row.allow_human_handoff,
            add_ai_summary_as_internal_comment=row.add_ai_summary_as_internal_comment,
            allow_ai_for_unassigned_conversations=row.allow_ai_for_unassigned_conversations,
            daily_message_limit=row.daily_message_limit,
            share_visitor_location=row.share_visitor_location,
            conversation_retention_days=row.conversation_retention_days,
            role_instructions=row.role_instructions,
            avoid_instructions=row.avoid_instructions,
            # Coerced, not trusted: a stored value outside the enum degrades to
            # the default rather than reaching the prompt builder as free text.
            personality=coerce_personality(row.personality),
            response_length=coerce_response_length(row.response_length),
            quota_timezone=row.quota_timezone or "UTC",
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def upsert(self, settings: TenantChatbotSettings) -> None:
        values = {
            "ai_chatbot_enabled": settings.ai_chatbot_enabled,
            "company_name": settings.company_name,
            "company_description": settings.company_description,
            "industry": settings.industry,
            "allow_human_handoff": settings.allow_human_handoff,
            "add_ai_summary_as_internal_comment": settings.add_ai_summary_as_internal_comment,
            "allow_ai_for_unassigned_conversations": (
                settings.allow_ai_for_unassigned_conversations
            ),
            "daily_message_limit": settings.daily_message_limit,
            "share_visitor_location": settings.share_visitor_location,
            "conversation_retention_days": settings.conversation_retention_days,
            "role_instructions": settings.role_instructions,
            "avoid_instructions": settings.avoid_instructions,
            "personality": settings.personality.value,
            "response_length": settings.response_length.value,
            "quota_timezone": settings.quota_timezone,
            "updated_at": settings.updated_at,
        }
        stmt = (
            pg_insert(TenantChatbotSettingsModel)
            .values(
                id=settings.id,
                tenant_id=settings.tenant_id,
                created_at=settings.created_at,
                **values,
            )
            .on_conflict_do_update(
                index_elements=[TenantChatbotSettingsModel.tenant_id], set_=values
            )
        )
        await self._session.execute(stmt)


class SqlTenantTeamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, tenant_id: UUID, team_id: UUID) -> TenantTeam | None:
        row = await self._session.scalar(
            select(TenantTeamModel).where(
                TenantTeamModel.tenant_id == tenant_id, TenantTeamModel.id == team_id
            )
        )
        return self._to_entity(row) if row else None

    async def list_for_tenant(
        self, tenant_id: UUID, *, active_only: bool = False
    ) -> list[TenantTeam]:
        stmt = select(TenantTeamModel).where(TenantTeamModel.tenant_id == tenant_id)
        if active_only:
            stmt = stmt.where(TenantTeamModel.is_active.is_(True))
        rows = await self._session.scalars(stmt.order_by(TenantTeamModel.name))
        return [self._to_entity(r) for r in rows]

    async def add(self, team: TenantTeam) -> None:
        self._session.add(
            TenantTeamModel(
                id=team.id,
                tenant_id=team.tenant_id,
                name=team.name,
                description=team.description,
                is_active=team.is_active,
                created_at=team.created_at,
                updated_at=team.updated_at,
            )
        )
        await self._session.flush()

    async def save(self, team: TenantTeam) -> None:
        await self._session.execute(
            update(TenantTeamModel)
            .where(TenantTeamModel.id == team.id, TenantTeamModel.tenant_id == team.tenant_id)
            .values(
                name=team.name,
                description=team.description,
                is_active=team.is_active,
                updated_at=team.updated_at,
            )
        )

    async def list_members(self, *, tenant_id: UUID, team_id: UUID) -> list[UUID]:
        rows = await self._session.scalars(
            select(TenantTeamMemberModel.membership_id).where(
                TenantTeamMemberModel.tenant_id == tenant_id,
                TenantTeamMemberModel.team_id == team_id,
            )
        )
        return list(rows)

    async def list_memberships_with_permission(
        self, *, tenant_id: UUID, permission_code: str
    ) -> list[UUID]:
        # `revoked_at IS NULL` is load-bearing: `tenant_membership_roles` keeps
        # revoked grants as history, so without it a supervisor whose oversight
        # was taken away would keep receiving notifications about every team's
        # queue -- alerts about conversations the inbox then refuses to show
        # them.
        rows = await self._session.scalars(
            select(TenantMembershipRoleModel.membership_id)
            .join(
                TenantRolePermissionModel,
                TenantRolePermissionModel.role_id == TenantMembershipRoleModel.role_id,
            )
            .join(
                TenantPermissionModel,
                TenantPermissionModel.id == TenantRolePermissionModel.permission_id,
            )
            .where(
                TenantMembershipRoleModel.tenant_id == tenant_id,
                TenantMembershipRoleModel.revoked_at.is_(None),
                TenantPermissionModel.code == permission_code,
            )
            .distinct()
        )
        return list(rows)

    async def list_team_ids_for_membership(
        self, *, tenant_id: UUID, membership_id: UUID
    ) -> list[UUID]:
        rows = await self._session.scalars(
            select(TenantTeamMemberModel.team_id).where(
                TenantTeamMemberModel.tenant_id == tenant_id,
                TenantTeamMemberModel.membership_id == membership_id,
            )
        )
        return list(rows)

    async def set_members(
        self, *, tenant_id: UUID, team_id: UUID, membership_ids: list[UUID]
    ) -> None:
        """Replaces the roster wholesale.

        Delete-then-insert rather than a diff: the set is small, the caller
        always knows the intended final state, and a diff would need to be
        correct about ordering to avoid transiently emptying a team that an
        Unassigned-inbox query is reading at the same moment.
        """
        await self._session.execute(
            delete(TenantTeamMemberModel).where(
                TenantTeamMemberModel.tenant_id == tenant_id,
                TenantTeamMemberModel.team_id == team_id,
            )
        )
        for membership_id in dict.fromkeys(membership_ids):
            self._session.add(
                TenantTeamMemberModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    team_id=team_id,
                    membership_id=membership_id,
                )
            )
        await self._session.flush()

    async def teams_for_membership(
        self, *, tenant_id: UUID, membership_id: UUID
    ) -> list[UUID]:
        rows = await self._session.scalars(
            select(TenantTeamMemberModel.team_id).where(
                TenantTeamMemberModel.tenant_id == tenant_id,
                TenantTeamMemberModel.membership_id == membership_id,
            )
        )
        return list(rows)

    @staticmethod
    def _to_entity(row: TenantTeamModel) -> TenantTeam:
        return TenantTeam(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            description=row.description,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SqlConversationHandoffRepository:
    """Handoff transitions, written as conditional UPDATEs.

    Every method here states the state it expects in its `WHERE` clause and
    reports whether a row matched. That is not defensive style -- it is the
    only way two concurrent agents can be told apart, and the same shape makes
    "the AI must not resume after a takeover" enforceable at the write rather
    than only at the read.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def route_to_team(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        team_id: UUID | None,
        reason: str | None,
        initiated_by: HandoffInitiator,
        now: datetime,
    ) -> bool:
        """Puts a conversation in a team's queue. False if an agent already has it."""
        result = await self._session.execute(
            update(ConversationModel)
            .where(
                ConversationModel.tenant_id == tenant_id,
                ConversationModel.id == conversation_id,
                # Refuses to yank a thread away from an agent who has already
                # claimed it -- a visitor pressing "talk to a person" twice
                # must not strand the person now typing a reply.
                ConversationModel.state.notin_(
                    [ConversationState.ASSIGNED.value, ConversationState.HUMAN_ACTIVE.value]
                ),
            )
            .values(
                state=(
                    ConversationState.UNASSIGNED.value
                    if team_id is not None
                    else ConversationState.HANDOFF_REQUESTED.value
                ),
                assigned_team_id=team_id,
                assigned_membership_id=None,
                handoff_reason=(reason or None),
                handoff_initiated_by=initiated_by.value,
                handoff_at=now,
                updated_at=now,
            )
        )
        return bool(_rowcount(result))

    async def claim(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        membership_id: UUID,
        now: datetime,
    ) -> bool:
        """**The race is settled here.**

        `WHERE state = 'unassigned'` makes the check and the write one
        statement. Postgres serialises conflicting updates to the same row, so
        of two agents claiming simultaneously exactly one sees a rowcount of 1;
        the other sees 0 and is told the conversation was already taken. A
        read-then-write would let both succeed and the second would silently
        overwrite the first.
        """
        result = await self._session.execute(
            update(ConversationModel)
            .where(
                ConversationModel.tenant_id == tenant_id,
                ConversationModel.id == conversation_id,
                ConversationModel.state == ConversationState.UNASSIGNED.value,
            )
            .values(
                state=ConversationState.ASSIGNED.value,
                assigned_membership_id=membership_id,
                claimed_at=now,
                updated_at=now,
            )
        )
        return bool(_rowcount(result))

    async def set_state(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        state: ConversationState,
        now: datetime,
        clear_assignment: bool = False,
    ) -> bool:
        values: dict[str, object] = {"state": state.value, "updated_at": now}
        if clear_assignment:
            values["assigned_membership_id"] = None
            values["assigned_team_id"] = None
        result = await self._session.execute(
            update(ConversationModel)
            .where(
                ConversationModel.tenant_id == tenant_id,
                ConversationModel.id == conversation_id,
            )
            .values(**values)
        )
        return bool(_rowcount(result))

    async def set_ai_fallback_disabled(
        self, *, tenant_id: UUID, conversation_id: UUID, disabled: bool, now: datetime
    ) -> bool:
        """Records the agent's decision to hold (or release) a thread.

        A plain UPDATE rather than a read-modify-write: the value being set is
        supplied by the agent, so there is nothing to read first, and two agents
        toggling at once should land on whichever pressed last rather than on a
        stale copy either of them loaded.
        """
        result = await self._session.execute(
            update(ConversationModel)
            .where(
                ConversationModel.tenant_id == tenant_id,
                ConversationModel.id == conversation_id,
            )
            .values(ai_fallback_disabled=disabled, updated_at=now)
        )
        return bool(_rowcount(result))

    async def purge_expired_conversations(
        self, *, tenant_id: UUID, older_than: datetime
    ) -> int:
        """Deletes this tenant's conversations last active before `older_than`.

        **Tenant-scoped by argument as well as by RLS.** A retention sweep is
        the one job whose bug deletes data rather than merely exposing it, so
        the predicate is explicit at the call site and the policy is the
        database's too -- neither alone.

        Messages go with the conversation by `ON DELETE CASCADE`; deleting the
        parent is what makes "the conversation is gone" true rather than
        leaving orphaned turns that still contain everything the visitor said.

        `last_message_at` rather than `created_at`: retention should measure
        from the last time anyone said anything, so a long-running support
        thread is not deleted out from under an agent mid-exchange.
        """
        result = await self._session.execute(
            delete(ConversationModel).where(
                ConversationModel.tenant_id == tenant_id,
                func.coalesce(
                    ConversationModel.last_message_at, ConversationModel.created_at
                )
                < older_than,
            )
        )
        return _rowcount(result)

    async def list_unassigned(
        self, *, tenant_id: UUID, team_ids: list[UUID] | None = None
    ) -> list[ConversationModel]:
        """The Unassigned inbox.

        `team_ids=None` means "every team", which is what a tenant admin with
        the oversight permission sees. An agent is passed their own teams, so
        a conversation routed to a team they do not staff never reaches them --
        scoping done in the query, not by filtering after the fact.
        """
        stmt = select(ConversationModel).where(
            ConversationModel.tenant_id == tenant_id,
            ConversationModel.state == ConversationState.UNASSIGNED.value,
        )
        if team_ids is not None:
            if not team_ids:
                return []
            stmt = stmt.where(ConversationModel.assigned_team_id.in_(team_ids))
        # Newest first. `nullslast` because `handoff_at` is nullable: a row
        # with no timestamp sorts *first* under a plain DESC in Postgres, which
        # would put the one conversation nobody can say the age of at the top
        # of a queue ordered by age.
        rows = await self._session.scalars(
            stmt.order_by(ConversationModel.handoff_at.desc().nullslast())
        )
        return list(rows)


class SqlPushSubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, subscription: PushSubscription) -> None:
        # Conflict on `(membership_id, endpoint)`: a browser re-subscribing --
        # which happens on an ordinary reload -- hands back the same endpoint,
        # and its keys may have been rotated. Updating rather than inserting is
        # what stops one browser accumulating a row per page load, each one
        # then receiving its own copy of every notification.
        stmt = (
            pg_insert(PushSubscriptionModel)
            .values(
                id=subscription.id,
                tenant_id=subscription.tenant_id,
                membership_id=subscription.membership_id,
                endpoint=subscription.endpoint,
                p256dh_key=subscription.p256dh_key,
                auth_key=subscription.auth_key,
                user_agent=subscription.user_agent,
                created_at=subscription.created_at,
            )
            .on_conflict_do_update(
                index_elements=[
                    PushSubscriptionModel.membership_id,
                    PushSubscriptionModel.endpoint,
                ],
                set_={
                    "p256dh_key": subscription.p256dh_key,
                    "auth_key": subscription.auth_key,
                    "user_agent": subscription.user_agent,
                },
            )
        )
        await self._session.execute(stmt)

    async def list_for_memberships(
        self, *, tenant_id: UUID, membership_ids: Sequence[UUID]
    ) -> list[PushSubscription]:
        if not membership_ids:
            # An empty recipient list means nobody, and `IN ()` is not valid
            # SQL. Returning early keeps "notify nobody" from becoming
            # "notify everyone" through a dropped predicate.
            return []
        rows = await self._session.scalars(
            select(PushSubscriptionModel).where(
                PushSubscriptionModel.tenant_id == tenant_id,
                PushSubscriptionModel.membership_id.in_(list(membership_ids)),
            )
        )
        return [
            PushSubscription(
                id=r.id,
                tenant_id=r.tenant_id,
                membership_id=r.membership_id,
                endpoint=r.endpoint,
                p256dh_key=r.p256dh_key,
                auth_key=r.auth_key,
                user_agent=r.user_agent,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
            )
            for r in rows
        ]

    async def delete_for_membership(
        self, *, tenant_id: UUID, membership_id: UUID, endpoint: str
    ) -> int:
        result = await self._session.execute(
            delete(PushSubscriptionModel).where(
                PushSubscriptionModel.tenant_id == tenant_id,
                PushSubscriptionModel.membership_id == membership_id,
                PushSubscriptionModel.endpoint == endpoint,
            )
        )
        return _rowcount(result)

    async def delete_by_endpoint(self, *, tenant_id: UUID, endpoint: str) -> int:
        result = await self._session.execute(
            delete(PushSubscriptionModel).where(
                PushSubscriptionModel.tenant_id == tenant_id,
                PushSubscriptionModel.endpoint == endpoint,
            )
        )
        return _rowcount(result)

    async def mark_used(
        self, *, tenant_id: UUID, endpoint: str, at: datetime
    ) -> None:
        await self._session.execute(
            update(PushSubscriptionModel)
            .where(
                PushSubscriptionModel.tenant_id == tenant_id,
                PushSubscriptionModel.endpoint == endpoint,
            )
            .values(last_used_at=at)
        )


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)
