"""Load-then-authorize helpers shared by every AI-resource use case.

The same three steps repeat everywhere: fetch the resource, confirm the
caller may *see* it (else raise NotFound, never AccessDenied -- an invisible
resource must not be provable to exist), and for writes additionally confirm
they may *change* it. Centralizing it means a new use case cannot accidentally
skip the visibility check or leak existence through the wrong exception type.
"""

from __future__ import annotations

from uuid import UUID

from iam_platform.application.ai_resources.exceptions import (
    AssistantNotFoundError,
    KnowledgeBaseNotFoundError,
    ResourceAccessDeniedError,
)
from iam_platform.application.ai_resources.ports import AiResourceUnitOfWork
from iam_platform.domain.ai_resources.entities import AiAssistant, KnowledgeBase
from iam_platform.domain.ai_resources.policies import (
    MANAGE_ASSISTANTS,
    MANAGE_KNOWLEDGE_BASES,
    VIEW_ALL_ASSISTANTS,
    VIEW_ALL_KNOWLEDGE_BASES,
    RequesterContext,
    can_access_resource,
    can_modify_resource,
    describe_assistant,
    describe_knowledge_base,
)


async def load_visible_assistant(
    uow: AiResourceUnitOfWork,
    *,
    assistant_id: UUID,
    requester: RequesterContext,
    for_modification: bool = False,
) -> AiAssistant:
    assistant = await uow.assistants.get_by_id(assistant_id)
    if assistant is None:
        raise AssistantNotFoundError(str(assistant_id))

    grant = await uow.assistant_members.get(
        assistant_id=assistant_id, membership_id=requester.membership_id
    )
    access_level = grant.access_level if grant is not None else None
    descriptor = describe_assistant(assistant)

    if not can_access_resource(
        resource=descriptor,
        requester=requester,
        explicit_access_level=access_level,
        view_all_permission=VIEW_ALL_ASSISTANTS,
    ):
        raise AssistantNotFoundError(str(assistant_id))

    if for_modification and not can_modify_resource(
        resource=descriptor,
        requester=requester,
        explicit_access_level=access_level,
        manage_permission=MANAGE_ASSISTANTS,
    ):
        # Safe to be explicit here: they already proved they can see it.
        raise ResourceAccessDeniedError("cannot modify this assistant")

    return assistant


async def load_visible_knowledge_base(
    uow: AiResourceUnitOfWork,
    *,
    knowledge_base_id: UUID,
    requester: RequesterContext,
    for_modification: bool = False,
) -> KnowledgeBase:
    knowledge_base = await uow.knowledge_bases.get_by_id(knowledge_base_id)
    if knowledge_base is None:
        raise KnowledgeBaseNotFoundError(str(knowledge_base_id))

    descriptor = describe_knowledge_base(knowledge_base)
    # Knowledge bases have no per-resource explicit-grant table of their own
    # (docs/16 defines `assistant_members` only), so the explicit-grant lane
    # is always absent here -- visibility mode, ownership, and the tenant-wide
    # permissions are the whole story.
    if not can_access_resource(
        resource=descriptor,
        requester=requester,
        explicit_access_level=None,
        view_all_permission=VIEW_ALL_KNOWLEDGE_BASES,
    ):
        raise KnowledgeBaseNotFoundError(str(knowledge_base_id))

    if for_modification and not can_modify_resource(
        resource=descriptor,
        requester=requester,
        explicit_access_level=None,
        manage_permission=MANAGE_KNOWLEDGE_BASES,
    ):
        raise ResourceAccessDeniedError("cannot modify this knowledge base")

    return knowledge_base
