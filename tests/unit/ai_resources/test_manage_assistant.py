"""Assistant use cases -- visibility filtering on list, existence-hiding on
get, and the read/modify split on write operations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    AssistantNotFoundError,
    ModelConfigurationNotFoundError,
    PermissionDeniedError,
    ResourceAccessDeniedError,
)
from iam_platform.application.ai_resources.manage_assistant import (
    ArchiveAssistant,
    ArchiveAssistantCommand,
    CreateAssistant,
    CreateAssistantCommand,
    GetAssistant,
    GetAssistantQuery,
    ListAssistants,
    ListAssistantsQuery,
    ListModelConfigurations,
    ListModelConfigurationsQuery,
    PublishAssistant,
    PublishAssistantCommand,
    UpdateAssistant,
    UpdateAssistantCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantMember,
    AssistantStatus,
    ModelConfiguration,
    ResourceVisibility,
)
from iam_platform.domain.shared.exceptions import InvalidStateTransitionError
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork

NOW = datetime(2026, 1, 1, tzinfo=UTC)

CREATE = "tenant.assistants.create"
PUBLISH = "tenant.assistants.publish"
MANAGE = "tenant.assistants.manage"
VIEW_ALL = "tenant.assistants.view_all"


def _seed_member(
    uow: FakeAiResourceUnitOfWork, tenant_id, *, department_id=None, team_id=None
):
    user_id = uuid4()
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        status=MembershipStatus.ACTIVE,
        department_id=department_id,
        team_id=team_id,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[membership.id] = membership
    return user_id, membership


def _seed_model_configuration(
    uow: FakeAiResourceUnitOfWork, tenant_id=None, *, granted_to=None
) -> ModelConfiguration:
    """Creates a platform-owned configuration and, unless told otherwise,
    grants it to `granted_to`.

    Existing and available are separate steps here for the same reason they
    are separate rows in the database: the tests that matter most are the ones
    where a configuration exists and the tenant may *not* use it.
    """
    config = ModelConfiguration(
        id=uuid4(), tenant_id=tenant_id, model_name="claude-sonnet-5", created_at=NOW, updated_at=NOW
    )
    uow.model_configurations.by_id[config.id] = config
    if granted_to is not None:
        uow.model_configurations.grant(tenant_id=granted_to, model_configuration_id=config.id)
    return config


def _seed_assistant(
    uow: FakeAiResourceUnitOfWork,
    tenant_id,
    owner_membership_id,
    *,
    visibility=ResourceVisibility.TENANT,
    department_id=None,
    team_id=None,
    status=AssistantStatus.DRAFT,
) -> AiAssistant:
    assistant = AiAssistant(
        id=uuid4(),
        tenant_id=tenant_id,
        name="assistant",
        owner_membership_id=owner_membership_id,
        model_configuration_id=uuid4(),
        visibility=visibility,
        department_id=department_id,
        team_id=team_id,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.assistants.by_id[assistant.id] = assistant
    return assistant


class TestCreateAssistant:
    async def test_creates_a_draft_owned_by_the_caller(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        config = _seed_model_configuration(uow, granted_to=tenant_id)

        use_case = CreateAssistant(uow, FixedClock(NOW))
        assistant_id = await use_case.execute(
            CreateAssistantCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({CREATE}),
                name="Support Bot",
                description=None,
                model_configuration_id=str(config.id),
            )
        )

        assistant = await uow.assistants.get_by_id(assistant_id)
        assert assistant is not None
        assert assistant.owner_membership_id == membership.id
        assert assistant.status == AssistantStatus.DRAFT
        assert uow.audit.events[0]["action"] == "ai_resources.assistant_created"

    async def test_denied_without_create_permission(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        config = _seed_model_configuration(uow, granted_to=tenant_id)

        use_case = CreateAssistant(uow, FixedClock(NOW))
        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                CreateAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    permissions=frozenset(),
                    name="Support Bot",
                    description=None,
                    model_configuration_id=str(config.id),
                )
            )
        assert uow.assistants.by_id == {}

    async def test_unknown_model_configuration_rejected(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)

        use_case = CreateAssistant(uow, FixedClock(NOW))
        with pytest.raises(ModelConfigurationNotFoundError):
            await use_case.execute(
                CreateAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    permissions=frozenset({CREATE}),
                    name="Support Bot",
                    description=None,
                    model_configuration_id=str(uuid4()),
                )
            )

    async def test_department_visibility_without_department_id_rejected(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        config = _seed_model_configuration(uow, granted_to=tenant_id)

        use_case = CreateAssistant(uow, FixedClock(NOW))
        with pytest.raises(InvalidStateTransitionError):
            await use_case.execute(
                CreateAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    permissions=frozenset({CREATE}),
                    name="Dept Bot",
                    description=None,
                    model_configuration_id=str(config.id),
                    visibility="department",
                    department_id=None,
                )
            )
        assert uow.assistants.by_id == {}


class TestListAssistants:
    async def test_filters_out_resources_the_caller_cannot_see(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        dept_a, dept_b = uuid4(), uuid4()
        user_id, membership = _seed_member(uow, tenant_id, department_id=dept_a)
        _, other_membership = _seed_member(uow, tenant_id, department_id=dept_b)

        visible_tenant_wide = _seed_assistant(uow, tenant_id, other_membership.id)
        visible_same_dept = _seed_assistant(
            uow,
            tenant_id,
            other_membership.id,
            visibility=ResourceVisibility.DEPARTMENT,
            department_id=dept_a,
        )
        _hidden_other_dept = _seed_assistant(
            uow,
            tenant_id,
            other_membership.id,
            visibility=ResourceVisibility.DEPARTMENT,
            department_id=dept_b,
        )
        _hidden_restricted = _seed_assistant(
            uow, tenant_id, other_membership.id, visibility=ResourceVisibility.RESTRICTED
        )

        use_case = ListAssistants(uow)
        result = await use_case.execute(
            ListAssistantsQuery(
                actor_user_id=str(user_id), tenant_id=str(tenant_id), permissions=frozenset()
            )
        )

        assert {a.id for a in result} == {visible_tenant_wide.id, visible_same_dept.id}

    async def test_explicit_grant_reveals_a_restricted_assistant(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        _, other_membership = _seed_member(uow, tenant_id)
        restricted = _seed_assistant(
            uow, tenant_id, other_membership.id, visibility=ResourceVisibility.RESTRICTED
        )
        grant = AssistantMember(
            id=uuid4(),
            tenant_id=tenant_id,
            assistant_id=restricted.id,
            membership_id=membership.id,
            added_at=NOW,
        )
        uow.assistant_members.by_id[grant.id] = grant

        use_case = ListAssistants(uow)
        result = await use_case.execute(
            ListAssistantsQuery(
                actor_user_id=str(user_id), tenant_id=str(tenant_id), permissions=frozenset()
            )
        )
        assert {a.id for a in result} == {restricted.id}

    async def test_view_all_permission_reveals_everything(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        _, other_membership = _seed_member(uow, tenant_id)
        a1 = _seed_assistant(
            uow, tenant_id, other_membership.id, visibility=ResourceVisibility.RESTRICTED
        )
        a2 = _seed_assistant(
            uow,
            tenant_id,
            other_membership.id,
            visibility=ResourceVisibility.DEPARTMENT,
            department_id=uuid4(),
        )

        use_case = ListAssistants(uow)
        result = await use_case.execute(
            ListAssistantsQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({VIEW_ALL}),
            )
        )
        assert {a.id for a in result} == {a1.id, a2.id}

    async def test_non_member_gets_empty_list(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        _, other_membership = _seed_member(uow, tenant_id)
        _seed_assistant(uow, tenant_id, other_membership.id)

        use_case = ListAssistants(uow)
        result = await use_case.execute(
            ListAssistantsQuery(
                actor_user_id=str(uuid4()), tenant_id=str(tenant_id), permissions=frozenset()
            )
        )
        assert result == []


class TestGetAssistant:
    async def test_invisible_assistant_reports_not_found_not_forbidden(self) -> None:
        """Existence-inference prevention: a caller who cannot see a resource
        must not be able to distinguish it from one that doesn't exist."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        _, other_membership = _seed_member(uow, tenant_id)
        restricted = _seed_assistant(
            uow, tenant_id, other_membership.id, visibility=ResourceVisibility.RESTRICTED
        )

        use_case = GetAssistant(uow)
        with pytest.raises(AssistantNotFoundError):
            await use_case.execute(
                GetAssistantQuery(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    assistant_id=str(restricted.id),
                    permissions=frozenset(),
                )
            )


class TestPublishAssistant:
    async def test_owner_can_publish(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(uow, tenant_id, membership.id)

        use_case = PublishAssistant(uow, FixedClock(NOW))
        await use_case.execute(
            PublishAssistantCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                assistant_id=str(assistant.id),
                permissions=frozenset({PUBLISH}),
            )
        )
        assert assistant.status == AssistantStatus.PUBLISHED
        assert uow.audit.events[0]["action"] == "ai_resources.assistant_published"

    async def test_non_owner_who_can_see_it_cannot_publish(self) -> None:
        """The read/modify split at the use-case level: tenant-wide visibility
        gets you the row, not the right to change it."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        _, other_membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(uow, tenant_id, other_membership.id)

        use_case = PublishAssistant(uow, FixedClock(NOW))
        with pytest.raises(ResourceAccessDeniedError):
            await use_case.execute(
                PublishAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    assistant_id=str(assistant.id),
                    permissions=frozenset({PUBLISH}),
                )
            )
        assert assistant.status == AssistantStatus.DRAFT

    async def test_manage_permission_allows_publishing_someone_elses(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        _, other_membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(uow, tenant_id, other_membership.id)

        use_case = PublishAssistant(uow, FixedClock(NOW))
        await use_case.execute(
            PublishAssistantCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                assistant_id=str(assistant.id),
                permissions=frozenset({PUBLISH, MANAGE}),
            )
        )
        assert assistant.status == AssistantStatus.PUBLISHED

    async def test_denied_without_publish_permission(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(uow, tenant_id, membership.id)

        use_case = PublishAssistant(uow, FixedClock(NOW))
        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                PublishAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    assistant_id=str(assistant.id),
                    permissions=frozenset(),
                )
            )
        assert assistant.status == AssistantStatus.DRAFT


class TestUpdateAssistant:
    async def test_owner_can_edit_name_description_and_model_configuration(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(uow, tenant_id, membership.id)
        new_config = _seed_model_configuration(uow, granted_to=tenant_id)

        use_case = UpdateAssistant(uow, FixedClock(NOW))
        await use_case.execute(
            UpdateAssistantCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                assistant_id=str(assistant.id),
                permissions=frozenset(),
                name="Renamed Bot",
                description="Updated description",
                system_prompt="Be helpful.",
                model_configuration_id=str(new_config.id),
            )
        )
        assert assistant.name == "Renamed Bot"
        assert assistant.description == "Updated description"
        assert assistant.system_prompt == "Be helpful."
        assert assistant.model_configuration_id == new_config.id
        assert uow.audit.events[-1]["action"] == "ai_resources.assistant_updated"

    async def test_non_owner_who_can_see_it_cannot_edit(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        _, other_membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(uow, tenant_id, other_membership.id)
        original_name = assistant.name

        use_case = UpdateAssistant(uow, FixedClock(NOW))
        with pytest.raises(ResourceAccessDeniedError):
            await use_case.execute(
                UpdateAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    assistant_id=str(assistant.id),
                    permissions=frozenset(),
                    name="Hijacked Bot",
                    description=None,
                    system_prompt=None,
                    model_configuration_id=str(assistant.model_configuration_id),
                )
            )
        assert assistant.name == original_name

    async def test_unknown_model_configuration_rejected(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(uow, tenant_id, membership.id)

        use_case = UpdateAssistant(uow, FixedClock(NOW))
        with pytest.raises(ModelConfigurationNotFoundError):
            await use_case.execute(
                UpdateAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    assistant_id=str(assistant.id),
                    permissions=frozenset(),
                    name=assistant.name,
                    description=None,
                    system_prompt=None,
                    model_configuration_id=str(uuid4()),
                )
            )


class TestArchiveAssistant:
    async def test_owner_can_archive(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(uow, tenant_id, membership.id)

        use_case = ArchiveAssistant(uow, FixedClock(NOW))
        await use_case.execute(
            ArchiveAssistantCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                assistant_id=str(assistant.id),
                permissions=frozenset(),
            )
        )
        assert assistant.status == AssistantStatus.ARCHIVED
        assert uow.audit.events[-1]["action"] == "ai_resources.assistant_archived"

    async def test_non_owner_who_can_see_it_cannot_archive(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        _, other_membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(uow, tenant_id, other_membership.id)

        use_case = ArchiveAssistant(uow, FixedClock(NOW))
        with pytest.raises(ResourceAccessDeniedError):
            await use_case.execute(
                ArchiveAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    assistant_id=str(assistant.id),
                    permissions=frozenset(),
                )
            )
        assert assistant.status == AssistantStatus.DRAFT

    async def test_already_archived_rejected(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        assistant = _seed_assistant(
            uow, tenant_id, membership.id, status=AssistantStatus.ARCHIVED
        )

        use_case = ArchiveAssistant(uow, FixedClock(NOW))
        with pytest.raises(InvalidStateTransitionError):
            await use_case.execute(
                ArchiveAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    assistant_id=str(assistant.id),
                    permissions=frozenset(),
                )
            )


class TestListModelConfigurations:
    async def test_returns_only_configurations_granted_to_this_tenant(self) -> None:
        """The rule that replaced "platform defaults plus my own".

        The ungranted configuration is the point: it exists, it is
        platform-owned, and before entitlements every tenant would have been
        offered it.
        """
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        granted = _seed_model_configuration(uow, granted_to=tenant_id)
        _ungranted = _seed_model_configuration(uow)
        _another_tenants = _seed_model_configuration(uow, granted_to=uuid4())

        use_case = ListModelConfigurations(uow)
        result = await use_case.execute(
            ListModelConfigurationsQuery(
                actor_user_id=str(user_id), tenant_id=str(tenant_id), permissions=frozenset()
            )
        )
        assert {c.id for c in result} == {granted.id}

    async def test_an_archived_configuration_is_not_offered(self) -> None:
        """Archiving withdraws a model from new assignments while leaving
        assistants that already use it alone."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        live = _seed_model_configuration(uow, granted_to=tenant_id)
        retired = _seed_model_configuration(uow, granted_to=tenant_id)
        retired.archive(now=NOW)

        use_case = ListModelConfigurations(uow)
        result = await use_case.execute(
            ListModelConfigurationsQuery(
                actor_user_id=str(user_id), tenant_id=str(tenant_id), permissions=frozenset()
            )
        )
        assert {c.id for c in result} == {live.id}

    async def test_non_member_gets_empty_list(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        _seed_model_configuration(uow, tenant_id=tenant_id)

        use_case = ListModelConfigurations(uow)
        result = await use_case.execute(
            ListModelConfigurationsQuery(
                actor_user_id=str(uuid4()), tenant_id=str(tenant_id), permissions=frozenset()
            )
        )
        assert result == []


class TestModelConfigurationEntitlement:
    """Which configurations a tenant may *select*.

    The case that matters is the one the old design could not express: a
    configuration that exists, is perfectly valid, and belongs to no tenant --
    usable only by the tenants the platform granted it to.
    """

    async def test_tenant_can_create_an_assistant_with_a_granted_configuration(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        config = _seed_model_configuration(uow, granted_to=tenant_id)

        assistant_id = await CreateAssistant(uow, FixedClock(NOW)).execute(
            CreateAssistantCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({CREATE}),
                name="Helper",
                description=None,
                model_configuration_id=str(config.id),
            )
        )
        stored = await uow.assistants.get_by_id(assistant_id)
        assert stored is not None
        assert stored.model_configuration_id == config.id

    async def test_a_configuration_not_granted_to_this_tenant_is_refused(self) -> None:
        """Submitting the id by hand does not help: it is reported as
        not-found, so an unentitled configuration is indistinguishable from
        one that does not exist."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        # Exists, and is granted to somebody else entirely.
        config = _seed_model_configuration(uow, granted_to=uuid4())

        with pytest.raises(ModelConfigurationNotFoundError):
            await CreateAssistant(uow, FixedClock(NOW)).execute(
                CreateAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    permissions=frozenset({CREATE}),
                    name="Helper",
                    description=None,
                    model_configuration_id=str(config.id),
                )
            )

    async def test_update_cannot_switch_to_an_ungranted_configuration(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        granted = _seed_model_configuration(uow, granted_to=tenant_id)
        assistant = _seed_assistant(uow, tenant_id, membership.id)
        assistant.model_configuration_id = granted.id
        ungranted = _seed_model_configuration(uow, granted_to=uuid4())

        with pytest.raises(ModelConfigurationNotFoundError):
            await UpdateAssistant(uow, FixedClock(NOW)).execute(
                UpdateAssistantCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    assistant_id=str(assistant.id),
                    permissions=frozenset({CREATE}),
                    name="Renamed",
                    description=None,
                    system_prompt=None,
                    model_configuration_id=str(ungranted.id),
                )
            )
        # Unchanged -- a refused edit must not partially apply.
        stored = await uow.assistants.get_by_id(assistant.id)
        assert stored is not None
        assert stored.model_configuration_id == granted.id

    async def test_an_assistant_keeps_an_archived_configuration_through_other_edits(
        self,
    ) -> None:
        """Archiving withdraws a model from new assignments. An assistant
        already using it must still be editable, or archiving would silently
        freeze every assistant that had chosen it."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        config = _seed_model_configuration(uow, granted_to=tenant_id)
        assistant = _seed_assistant(uow, tenant_id, membership.id)
        assistant.model_configuration_id = config.id
        config.archive(now=NOW)

        await UpdateAssistant(uow, FixedClock(NOW)).execute(
            UpdateAssistantCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                assistant_id=str(assistant.id),
                permissions=frozenset({CREATE}),
                name="Renamed",
                description=None,
                system_prompt=None,
                model_configuration_id=str(config.id),
            )
        )
        stored = await uow.assistants.get_by_id(assistant.id)
        assert stored is not None
        assert stored.name == "Renamed"
