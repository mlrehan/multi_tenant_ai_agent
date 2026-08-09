"""Explicit per-member grants on a restricted assistant -- the mechanism
behind Phase 1 §12's "restricted assistants require explicit assignment".

Granting access is a *modify* operation on the assistant, not a read one:
only someone who could change the assistant may change who reaches it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.authorize import load_visible_assistant
from iam_platform.application.ai_resources.exceptions import AssistantNotFoundError
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.application.tenant_authz.exceptions import MembershipNotFoundError
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import AssistantAccessLevel, AssistantMember


@dataclass(frozen=True, slots=True)
class GrantAssistantAccessCommand:
    actor_user_id: str
    tenant_id: str
    assistant_id: str
    target_membership_id: str
    access_level: str
    permissions: frozenset[str]


class GrantAssistantAccess:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: GrantAssistantAccessCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        assistant_id = UUID(command.assistant_id)
        target_membership_id = UUID(command.target_membership_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise AssistantNotFoundError(command.assistant_id)

            await load_visible_assistant(
                uow, assistant_id=assistant_id, requester=requester, for_modification=True
            )

            target_membership = await uow.tenant_memberships.get_by_id(target_membership_id)
            # RLS scopes this read to the active tenant, so a membership from
            # another tenant simply isn't found -- the tenant_id comparison is
            # here so the guarantee doesn't rest solely on the connection
            # being the RLS-subject one.
            if target_membership is None or target_membership.tenant_id != tenant_id:
                raise MembershipNotFoundError(command.target_membership_id)

            existing = await uow.assistant_members.get(
                assistant_id=assistant_id, membership_id=target_membership_id
            )
            if existing is not None:
                return existing.id  # idempotent

            member = AssistantMember(
                id=uuid4(),
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                membership_id=target_membership_id,
                access_level=AssistantAccessLevel(command.access_level),
                added_at=now,
            )
            await uow.assistant_members.add(member)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_membership.user_id,
                tenant_id=tenant_id,
                action="ai_resources.assistant_access_granted",
                resource_type="assistant_member",
                resource_id=member.id,
                result="success",
                metadata={
                    "assistant_id": str(assistant_id),
                    "access_level": command.access_level,
                },
            )
            return member.id


@dataclass(frozen=True, slots=True)
class RevokeAssistantAccessCommand:
    actor_user_id: str
    tenant_id: str
    assistant_id: str
    target_membership_id: str
    permissions: frozenset[str]


class RevokeAssistantAccess:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: RevokeAssistantAccessCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        assistant_id = UUID(command.assistant_id)
        target_membership_id = UUID(command.target_membership_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise AssistantNotFoundError(command.assistant_id)

            await load_visible_assistant(
                uow, assistant_id=assistant_id, requester=requester, for_modification=True
            )

            existing = await uow.assistant_members.get(
                assistant_id=assistant_id, membership_id=target_membership_id
            )
            if existing is None:
                return  # idempotent

            await uow.assistant_members.remove(
                assistant_id=assistant_id, membership_id=target_membership_id
            )
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=tenant_id,
                action="ai_resources.assistant_access_revoked",
                resource_type="assistant_member",
                resource_id=existing.id,
                result="success",
                metadata={"assistant_id": str(assistant_id)},
            )
