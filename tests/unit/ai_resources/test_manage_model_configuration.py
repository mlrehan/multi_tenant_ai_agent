"""Platform governance of model configurations.

The rules this pins down, in the order they matter:

1. **Only the platform governs the catalogue.** Every operation needs
   `platform.model_configurations.manage`; a tenant administrator has no
   route to these use cases at all, and holding every tenant permission there
   is grants none of them.
2. **Availability is an explicit grant.** Creating a configuration makes it
   usable by nobody. This is the whole point of separating ownership from
   availability -- before entitlements, a platform-owned row was either
   theoretically visible to everyone or (because of the old foreign key)
   usable by no one.
3. **Revocation cannot strand an assistant.** Refused while any of that
   tenant's assistants still uses the configuration, with a count so the
   operator knows what to do next.

The corresponding tenant-side rules -- who may *select* a configuration --
live in `test_manage_assistant.py`, and the database-level guarantee that
neither can be bypassed lives in
`tests/integration/db/test_model_configuration_entitlements.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationInUseError,
    ModelConfigurationManagementDeniedError,
    ModelConfigurationNotFoundError,
)
from iam_platform.application.ai_resources.manage_model_configuration import (
    MANAGE_PERMISSION,
    ArchiveModelConfiguration,
    CreateModelConfiguration,
    CreateModelConfigurationCommand,
    GrantModelConfigurationToTenant,
    ListModelConfigurationsForPlatform,
    ListModelConfigurationsForPlatformQuery,
    ModelConfigurationActionCommand,
    RestoreModelConfiguration,
    RevokeModelConfigurationFromTenant,
    TenantAccessCommand,
    UpdateModelConfiguration,
    UpdateModelConfigurationCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.platform_authz.entities import PlatformUserRole
from tests.unit.tenant_authz.fakes import FakePlatformUnitOfWork, make_platform_role

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _actor_with_permission(uow: FakePlatformUnitOfWork, *permissions: str) -> UUID:
    """A platform user holding exactly `permissions`."""
    actor_id = uuid4()
    role = make_platform_role(code="model-admin", rank=50, now=NOW)
    uow.platform_roles.by_id[role.id] = role
    uow.platform_permissions.role_permission_codes[role.id] = set(permissions)
    assignment = PlatformUserRole(
        id=uuid4(),
        user_id=actor_id,
        role_id=role.id,
        granted_by_user_id=uuid4(),
        granted_at=NOW,
    )
    uow.platform_user_roles.by_id[assignment.id] = assignment
    return actor_id


async def _create_configuration(uow: FakePlatformUnitOfWork, actor_id: UUID, name: str) -> UUID:
    return await CreateModelConfiguration(uow, FixedClock(NOW)).execute(
        CreateModelConfigurationCommand(actor_user_id=str(actor_id), model_name=name)
    )


class TestPlatformAdminGovernsTheCatalogue:
    async def test_platform_admin_can_create_a_configuration(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = _actor_with_permission(uow, MANAGE_PERMISSION)

        configuration_id = await _create_configuration(uow, actor_id, "claude-opus-5")

        stored = await uow.model_configurations.get_by_id(configuration_id)
        assert stored is not None
        assert stored.model_name == "claude-opus-5"
        # Platform-owned: there is no field on the command to say otherwise.
        assert stored.tenant_id is None

    async def test_a_new_configuration_is_available_to_nobody(self) -> None:
        """Creating is not granting. Availability is always a deliberate act."""
        uow = FakePlatformUnitOfWork()
        actor_id = _actor_with_permission(uow, MANAGE_PERMISSION)

        configuration_id = await _create_configuration(uow, actor_id, "claude-opus-5")

        assert (
            await uow.tenant_model_access.list_tenant_ids_for_configuration(configuration_id)
            == []
        )

    async def test_platform_admin_can_edit_and_archive(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = _actor_with_permission(uow, MANAGE_PERMISSION)
        configuration_id = await _create_configuration(uow, actor_id, "old-name")

        await UpdateModelConfiguration(uow, FixedClock(NOW)).execute(
            UpdateModelConfigurationCommand(
                actor_user_id=str(actor_id),
                model_configuration_id=str(configuration_id),
                model_name="new-name",
                token_budget_per_month=1_000,
            )
        )
        await ArchiveModelConfiguration(uow, FixedClock(NOW)).execute(
            ModelConfigurationActionCommand(
                actor_user_id=str(actor_id),
                model_configuration_id=str(configuration_id),
            )
        )

        stored = await uow.model_configurations.get_by_id(configuration_id)
        assert stored is not None
        assert stored.model_name == "new-name"
        assert stored.token_budget_per_month == 1_000
        assert stored.is_archived

        await RestoreModelConfiguration(uow, FixedClock(NOW)).execute(
            ModelConfigurationActionCommand(
                actor_user_id=str(actor_id),
                model_configuration_id=str(configuration_id),
            )
        )
        restored = await uow.model_configurations.get_by_id(configuration_id)
        assert restored is not None
        assert not restored.is_archived

    async def test_listing_reports_which_tenants_may_use_each_entry(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = _actor_with_permission(uow, MANAGE_PERMISSION)
        configuration_id = await _create_configuration(uow, actor_id, "claude-opus-5")
        tenant_a, tenant_b = uuid4(), uuid4()

        for tenant_id in (tenant_a, tenant_b):
            await GrantModelConfigurationToTenant(uow, FixedClock(NOW)).execute(
                TenantAccessCommand(
                    actor_user_id=str(actor_id),
                    model_configuration_id=str(configuration_id),
                    tenant_id=str(tenant_id),
                )
            )

        items = await ListModelConfigurationsForPlatform(uow, FixedClock(NOW)).execute(
            ListModelConfigurationsForPlatformQuery(actor_user_id=str(actor_id))
        )
        assert len(items) == 1
        assert set(items[0].tenant_ids) == {tenant_a, tenant_b}


class TestOnlyPlatformAdminGoverns:
    """A tenant administrator cannot reach any of this.

    Parametrised over every operation rather than spot-checking one: a
    governance surface where four of five actions are gated is not gated.
    """

    @pytest.mark.parametrize(
        "run",
        [
            pytest.param(
                lambda uow, actor: CreateModelConfiguration(uow, FixedClock(NOW)).execute(
                    CreateModelConfigurationCommand(actor_user_id=str(actor), model_name="x")
                ),
                id="create",
            ),
            pytest.param(
                lambda uow, actor: UpdateModelConfiguration(uow, FixedClock(NOW)).execute(
                    UpdateModelConfigurationCommand(
                        actor_user_id=str(actor),
                        model_configuration_id=str(uuid4()),
                        model_name="x",
                    )
                ),
                id="update",
            ),
            pytest.param(
                lambda uow, actor: ArchiveModelConfiguration(uow, FixedClock(NOW)).execute(
                    ModelConfigurationActionCommand(
                        actor_user_id=str(actor), model_configuration_id=str(uuid4())
                    )
                ),
                id="archive",
            ),
            pytest.param(
                lambda uow, actor: GrantModelConfigurationToTenant(
                    uow, FixedClock(NOW)
                ).execute(
                    TenantAccessCommand(
                        actor_user_id=str(actor),
                        model_configuration_id=str(uuid4()),
                        tenant_id=str(uuid4()),
                    )
                ),
                id="grant",
            ),
            pytest.param(
                lambda uow, actor: RevokeModelConfigurationFromTenant(
                    uow, FixedClock(NOW)
                ).execute(
                    TenantAccessCommand(
                        actor_user_id=str(actor),
                        model_configuration_id=str(uuid4()),
                        tenant_id=str(uuid4()),
                    )
                ),
                id="revoke",
            ),
            pytest.param(
                lambda uow, actor: ListModelConfigurationsForPlatform(
                    uow, FixedClock(NOW)
                ).execute(
                    ListModelConfigurationsForPlatformQuery(actor_user_id=str(actor))
                ),
                id="list",
            ),
        ],
    )
    async def test_every_operation_requires_the_platform_permission(self, run) -> None:
        uow = FakePlatformUnitOfWork()
        # Every *tenant* permission that exists, and the two other platform
        # ones -- none of which is the one this surface requires.
        actor_id = _actor_with_permission(
            uow, "platform.users.manage", "platform.tenants.create"
        )

        with pytest.raises(ModelConfigurationManagementDeniedError):
            await run(uow, actor_id)

    async def test_an_actor_with_no_platform_roles_is_refused(self) -> None:
        uow = FakePlatformUnitOfWork()

        with pytest.raises(ModelConfigurationManagementDeniedError):
            await _create_configuration(uow, uuid4(), "claude-opus-5")


class TestGrantAndRevoke:
    async def test_granting_twice_is_not_an_error(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = _actor_with_permission(uow, MANAGE_PERMISSION)
        configuration_id = await _create_configuration(uow, actor_id, "claude-opus-5")
        tenant_id = uuid4()

        command = TenantAccessCommand(
            actor_user_id=str(actor_id),
            model_configuration_id=str(configuration_id),
            tenant_id=str(tenant_id),
        )
        await GrantModelConfigurationToTenant(uow, FixedClock(NOW)).execute(command)
        await GrantModelConfigurationToTenant(uow, FixedClock(NOW)).execute(command)

        assert await uow.tenant_model_access.list_tenant_ids_for_configuration(
            configuration_id
        ) == [tenant_id]

    async def test_granting_a_nonexistent_configuration_is_not_found(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = _actor_with_permission(uow, MANAGE_PERMISSION)

        with pytest.raises(ModelConfigurationNotFoundError):
            await GrantModelConfigurationToTenant(uow, FixedClock(NOW)).execute(
                TenantAccessCommand(
                    actor_user_id=str(actor_id),
                    model_configuration_id=str(uuid4()),
                    tenant_id=str(uuid4()),
                )
            )

    async def test_revoking_an_unused_configuration_succeeds(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = _actor_with_permission(uow, MANAGE_PERMISSION)
        configuration_id = await _create_configuration(uow, actor_id, "claude-opus-5")
        tenant_id = uuid4()
        command = TenantAccessCommand(
            actor_user_id=str(actor_id),
            model_configuration_id=str(configuration_id),
            tenant_id=str(tenant_id),
        )
        await GrantModelConfigurationToTenant(uow, FixedClock(NOW)).execute(command)

        await RevokeModelConfigurationFromTenant(uow, FixedClock(NOW)).execute(command)

        assert (
            await uow.tenant_model_access.list_tenant_ids_for_configuration(configuration_id)
            == []
        )

    async def test_revocation_is_refused_while_assistants_depend_on_it(self) -> None:
        """The explicit policy: an operator cannot leave production assistants
        pointing at a configuration their tenant may no longer use."""
        uow = FakePlatformUnitOfWork()
        actor_id = _actor_with_permission(uow, MANAGE_PERMISSION)
        configuration_id = await _create_configuration(uow, actor_id, "claude-opus-5")
        tenant_id = uuid4()
        command = TenantAccessCommand(
            actor_user_id=str(actor_id),
            model_configuration_id=str(configuration_id),
            tenant_id=str(tenant_id),
        )
        await GrantModelConfigurationToTenant(uow, FixedClock(NOW)).execute(command)
        uow.tenant_model_access.blocking_assistants[(tenant_id, configuration_id)] = 3

        with pytest.raises(ModelConfigurationInUseError) as excinfo:
            await RevokeModelConfigurationFromTenant(uow, FixedClock(NOW)).execute(command)

        # The count is in the message: "move them first" is only actionable if
        # the operator knows how many there are.
        assert "3 assistant" in str(excinfo.value)
        # And the grant survives, so the assistants keep working.
        assert await uow.tenant_model_access.list_tenant_ids_for_configuration(
            configuration_id
        ) == [tenant_id]

    async def test_archiving_does_not_revoke_existing_grants(self) -> None:
        """Archive and revoke are different actions with different blast
        radii -- archiving stops new assignments, it does not take the model
        away from tenants already using it."""
        uow = FakePlatformUnitOfWork()
        actor_id = _actor_with_permission(uow, MANAGE_PERMISSION)
        configuration_id = await _create_configuration(uow, actor_id, "claude-opus-5")
        tenant_id = uuid4()
        await GrantModelConfigurationToTenant(uow, FixedClock(NOW)).execute(
            TenantAccessCommand(
                actor_user_id=str(actor_id),
                model_configuration_id=str(configuration_id),
                tenant_id=str(tenant_id),
            )
        )

        await ArchiveModelConfiguration(uow, FixedClock(NOW)).execute(
            ModelConfigurationActionCommand(
                actor_user_id=str(actor_id),
                model_configuration_id=str(configuration_id),
            )
        )

        assert await uow.tenant_model_access.list_tenant_ids_for_configuration(
            configuration_id
        ) == [tenant_id]
