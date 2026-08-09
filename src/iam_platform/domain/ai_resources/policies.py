"""Resource-visibility resolution for AI resources -- docs/16-schema-ai-resources.md,
implementing the Phase 1 §12 assistant/knowledge-base access policies.

This is a pure function over already-loaded data, exactly like
``domain.tenant_authz.policies`` -- it never queries. Two distinct questions
are answered here, and conflating them is the mistake this module exists to
prevent:

- **Can this member see the resource at all?** (``can_access_resource``) --
  visibility mode + department/team match + explicit grant.
- **Can this member change it?** (``can_modify_resource``) -- ownership, an
  editor/owner-level explicit grant, or a tenant-wide manage permission.

Tenant isolation is deliberately NOT re-implemented here. By the time a
resource reaches this function it has already been loaded through an
RLS-scoped connection, so it provably belongs to the active tenant. Adding a
redundant ``tenant_id`` comparison would suggest this layer is what enforces
isolation, which would be wrong and would rot the moment someone trusted it
instead of RLS.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantAccessLevel,
    KnowledgeBase,
    ResourceVisibility,
)

# Holding this permission means "see every resource in the tenant regardless
# of visibility" -- the Administrator/Auditor case from Phase 1 §12. It grants
# *discovery*, not modification, and for conversations it grants metadata
# only (enforced at the API-schema level, since the domain has no notion of
# which fields a DTO exposes).
VIEW_ALL_ASSISTANTS = "tenant.assistants.view_all"
MANAGE_ASSISTANTS = "tenant.assistants.manage"
VIEW_ALL_KNOWLEDGE_BASES = "tenant.knowledge_bases.view_all"
MANAGE_KNOWLEDGE_BASES = "tenant.knowledge_bases.manage"
VIEW_ALL_CONVERSATIONS = "tenant.conversations.view"


@dataclass(frozen=True, slots=True)
class RequesterContext:
    """Everything the visibility policy needs to know about who is asking.

    ``department_id``/``team_id`` come from the requester's own
    ``tenant_memberships`` row, never from client input -- that's what makes
    department/team scoping an authorization control rather than a filter
    suggestion.
    """

    membership_id: UUID
    department_id: UUID | None
    team_id: UUID | None
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class VisibilityDescriptor:
    """The visibility-relevant fields of an assistant or knowledge base.

    A small struct rather than the entity itself so this one function serves
    both resource types without either domain entity having to inherit from a
    shared base purely to satisfy a policy signature.
    """

    owner_membership_id: UUID
    visibility: ResourceVisibility
    department_id: UUID | None
    team_id: UUID | None


def describe_assistant(assistant: AiAssistant) -> VisibilityDescriptor:
    return VisibilityDescriptor(
        owner_membership_id=assistant.owner_membership_id,
        visibility=assistant.visibility,
        department_id=assistant.department_id,
        team_id=assistant.team_id,
    )


def describe_knowledge_base(knowledge_base: KnowledgeBase) -> VisibilityDescriptor:
    return VisibilityDescriptor(
        owner_membership_id=knowledge_base.owner_membership_id,
        visibility=knowledge_base.visibility,
        department_id=knowledge_base.department_id,
        team_id=knowledge_base.team_id,
    )


def can_access_resource(
    *,
    resource: VisibilityDescriptor,
    requester: RequesterContext,
    explicit_access_level: AssistantAccessLevel | None,
    view_all_permission: str,
) -> bool:
    """Whether ``requester`` may see ``resource`` at all.

    ``explicit_access_level`` is the requester's ``assistant_members`` row for
    this resource (``None`` if they have no explicit grant). It is what makes
    ``RESTRICTED`` resources reachable, and it also grants access to a
    department/team-scoped resource the requester would otherwise miss --
    an explicit grant is strictly additive, never a downgrade.
    """
    if requester.membership_id == resource.owner_membership_id:
        return True
    if view_all_permission in requester.permissions:
        return True
    if explicit_access_level is not None:
        return True

    match resource.visibility:
        case ResourceVisibility.TENANT:
            return True
        case ResourceVisibility.DEPARTMENT:
            # `is not None` guards the case where both sides are NULL --
            # without it, two members who simply have no department set would
            # "match" each other and see department-scoped resources they
            # were never meant to.
            return (
                resource.department_id is not None
                and requester.department_id == resource.department_id
            )
        case ResourceVisibility.TEAM:
            return resource.team_id is not None and requester.team_id == resource.team_id
        case ResourceVisibility.RESTRICTED:
            # Reachable only via owner / view-all / explicit grant, all of
            # which were already checked above.
            return False


def can_modify_resource(
    *,
    resource: VisibilityDescriptor,
    requester: RequesterContext,
    explicit_access_level: AssistantAccessLevel | None,
    manage_permission: str,
) -> bool:
    """Whether ``requester`` may change ``resource``.

    Strictly narrower than ``can_access_resource``: being able to *see* a
    tenant-visible assistant never implies being able to edit it.
    """
    if requester.membership_id == resource.owner_membership_id:
        return True
    if manage_permission in requester.permissions:
        return True
    return explicit_access_level in (AssistantAccessLevel.EDITOR, AssistantAccessLevel.OWNER)


def can_read_conversation(
    *, conversation_membership_id: UUID, requester: RequesterContext
) -> bool:
    """Conversation *content* is owner-only, plus holders of
    ``tenant.conversations.view``.

    docs/16-schema-ai-resources.md is explicit that the view-all case is
    metadata-only for auditors; this function answers "may they retrieve the
    row at all", and the API schema is what withholds message content from a
    non-owner. Access by anyone other than the owning membership is audited by
    the calling use case.
    """
    if requester.membership_id == conversation_membership_id:
        return True
    return VIEW_ALL_CONVERSATIONS in requester.permissions


def is_conversation_owner(*, conversation_membership_id: UUID, requester: RequesterContext) -> bool:
    return requester.membership_id == conversation_membership_id
