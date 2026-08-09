"""Exhaustive tests for the AI-resource visibility policy -- the Phase 1 §12
access rules (docs/16-schema-ai-resources.md).

Structured around the four visibility modes crossed with the four ways a
requester can qualify (owner / view-all permission / explicit grant /
visibility match), because that product is exactly where an access-control
bug would hide.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from iam_platform.domain.ai_resources.entities import AssistantAccessLevel, ResourceVisibility
from iam_platform.domain.ai_resources.policies import (
    VIEW_ALL_ASSISTANTS,
    RequesterContext,
    VisibilityDescriptor,
    can_access_resource,
    can_modify_resource,
    can_read_conversation,
    is_conversation_owner,
)

MANAGE = "tenant.assistants.manage"


def _requester(
    *, membership_id=None, department_id=None, team_id=None, permissions=frozenset()
) -> RequesterContext:
    return RequesterContext(
        membership_id=membership_id or uuid4(),
        department_id=department_id,
        team_id=team_id,
        permissions=permissions,
    )


def _resource(
    *, owner_membership_id=None, visibility=ResourceVisibility.TENANT, department_id=None, team_id=None
) -> VisibilityDescriptor:
    return VisibilityDescriptor(
        owner_membership_id=owner_membership_id or uuid4(),
        visibility=visibility,
        department_id=department_id,
        team_id=team_id,
    )


class TestTenantVisibility:
    def test_any_member_can_access(self) -> None:
        assert can_access_resource(
            resource=_resource(visibility=ResourceVisibility.TENANT),
            requester=_requester(),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )


class TestDepartmentVisibility:
    def test_matching_department_can_access(self) -> None:
        dept = uuid4()
        assert can_access_resource(
            resource=_resource(visibility=ResourceVisibility.DEPARTMENT, department_id=dept),
            requester=_requester(department_id=dept),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )

    def test_different_department_cannot_access(self) -> None:
        assert not can_access_resource(
            resource=_resource(visibility=ResourceVisibility.DEPARTMENT, department_id=uuid4()),
            requester=_requester(department_id=uuid4()),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )

    def test_requester_with_no_department_cannot_access(self) -> None:
        assert not can_access_resource(
            resource=_resource(visibility=ResourceVisibility.DEPARTMENT, department_id=uuid4()),
            requester=_requester(department_id=None),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )

    def test_two_null_departments_do_not_match(self) -> None:
        """The NULL-vs-NULL trap: two members who simply have no department set
        must not thereby "match" a department-scoped resource with none set."""
        assert not can_access_resource(
            resource=_resource(visibility=ResourceVisibility.DEPARTMENT, department_id=None),
            requester=_requester(department_id=None),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )


class TestTeamVisibility:
    def test_matching_team_can_access(self) -> None:
        team = uuid4()
        assert can_access_resource(
            resource=_resource(visibility=ResourceVisibility.TEAM, team_id=team),
            requester=_requester(team_id=team),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )

    def test_different_team_cannot_access(self) -> None:
        assert not can_access_resource(
            resource=_resource(visibility=ResourceVisibility.TEAM, team_id=uuid4()),
            requester=_requester(team_id=uuid4()),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )

    def test_two_null_teams_do_not_match(self) -> None:
        assert not can_access_resource(
            resource=_resource(visibility=ResourceVisibility.TEAM, team_id=None),
            requester=_requester(team_id=None),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )


class TestRestrictedVisibility:
    def test_plain_member_cannot_access(self) -> None:
        assert not can_access_resource(
            resource=_resource(visibility=ResourceVisibility.RESTRICTED),
            requester=_requester(),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )

    def test_explicit_grant_unlocks_access(self) -> None:
        assert can_access_resource(
            resource=_resource(visibility=ResourceVisibility.RESTRICTED),
            requester=_requester(),
            explicit_access_level=AssistantAccessLevel.VIEWER,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )

    def test_owner_always_has_access(self) -> None:
        owner_membership = uuid4()
        assert can_access_resource(
            resource=_resource(
                owner_membership_id=owner_membership, visibility=ResourceVisibility.RESTRICTED
            ),
            requester=_requester(membership_id=owner_membership),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )

    def test_view_all_permission_grants_access(self) -> None:
        assert can_access_resource(
            resource=_resource(visibility=ResourceVisibility.RESTRICTED),
            requester=_requester(permissions=frozenset({VIEW_ALL_ASSISTANTS})),
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )


class TestExplicitGrantIsAdditive:
    @pytest.mark.parametrize(
        "visibility",
        [
            ResourceVisibility.DEPARTMENT,
            ResourceVisibility.TEAM,
            ResourceVisibility.RESTRICTED,
        ],
    )
    def test_grant_reaches_a_resource_the_requester_would_otherwise_miss(
        self, visibility: ResourceVisibility
    ) -> None:
        assert can_access_resource(
            resource=_resource(
                visibility=visibility, department_id=uuid4(), team_id=uuid4()
            ),
            requester=_requester(department_id=uuid4(), team_id=uuid4()),
            explicit_access_level=AssistantAccessLevel.VIEWER,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )


class TestCanModifyResource:
    def test_owner_can_modify(self) -> None:
        owner_membership = uuid4()
        assert can_modify_resource(
            resource=_resource(owner_membership_id=owner_membership),
            requester=_requester(membership_id=owner_membership),
            explicit_access_level=None,
            manage_permission=MANAGE,
        )

    def test_manage_permission_allows_modify(self) -> None:
        assert can_modify_resource(
            resource=_resource(),
            requester=_requester(permissions=frozenset({MANAGE})),
            explicit_access_level=None,
            manage_permission=MANAGE,
        )

    def test_seeing_a_tenant_visible_resource_does_not_imply_modify(self) -> None:
        """The core read/write split -- a plain member can see every
        tenant-visible assistant but must not be able to edit one."""
        resource = _resource(visibility=ResourceVisibility.TENANT)
        requester = _requester()
        assert can_access_resource(
            resource=resource,
            requester=requester,
            explicit_access_level=None,
            view_all_permission=VIEW_ALL_ASSISTANTS,
        )
        assert not can_modify_resource(
            resource=resource,
            requester=requester,
            explicit_access_level=None,
            manage_permission=MANAGE,
        )

    def test_viewer_grant_does_not_allow_modify(self) -> None:
        assert not can_modify_resource(
            resource=_resource(),
            requester=_requester(),
            explicit_access_level=AssistantAccessLevel.VIEWER,
            manage_permission=MANAGE,
        )

    @pytest.mark.parametrize(
        "level", [AssistantAccessLevel.EDITOR, AssistantAccessLevel.OWNER]
    )
    def test_editor_and_owner_grants_allow_modify(self, level: AssistantAccessLevel) -> None:
        assert can_modify_resource(
            resource=_resource(),
            requester=_requester(),
            explicit_access_level=level,
            manage_permission=MANAGE,
        )

    def test_view_all_permission_alone_does_not_allow_modify(self) -> None:
        """An auditor can see everything; that must not make them an editor."""
        assert not can_modify_resource(
            resource=_resource(),
            requester=_requester(permissions=frozenset({VIEW_ALL_ASSISTANTS})),
            explicit_access_level=None,
            manage_permission=MANAGE,
        )


class TestConversationAccess:
    def test_owner_can_read_and_is_owner(self) -> None:
        membership = uuid4()
        requester = _requester(membership_id=membership)
        assert can_read_conversation(
            conversation_membership_id=membership, requester=requester
        )
        assert is_conversation_owner(
            conversation_membership_id=membership, requester=requester
        )

    def test_other_member_cannot_read(self) -> None:
        assert not can_read_conversation(
            conversation_membership_id=uuid4(), requester=_requester()
        )

    def test_auditor_can_read_but_is_not_owner(self) -> None:
        """The metadata-only path: reachable, but flagged non-owner so the API
        layer serves the summary representation."""
        requester = _requester(permissions=frozenset({"tenant.conversations.view"}))
        conversation_membership = uuid4()
        assert can_read_conversation(
            conversation_membership_id=conversation_membership, requester=requester
        )
        assert not is_conversation_owner(
            conversation_membership_id=conversation_membership, requester=requester
        )
