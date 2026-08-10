"""Platform governance of model configurations.

The platform owns the catalogue of models and decides which tenants may use
each entry. Tenants select from what they have been granted; they never create
or edit the underlying configuration. This module is the whole of the first
half -- the second half is `tenant_model_configurations` and the foreign key
`ai_assistants` carries to it.

**Everything here runs on the BYPASSRLS platform connection**, for the same
reason the user directory does: deciding which tenants may use a model is a
cross-tenant judgement, and the tenant-scoped connection deliberately cannot
see across tenants.

**One permission, not two.** `platform.model_configurations.manage` gates
reads as well as writes. The catalogue is an operator surface with no
read-only audience today; a `.read` permission nobody is granted separately
would be a catalogue row pretending to be a control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationInUseError,
    ModelConfigurationManagementDeniedError,
    ModelConfigurationNotFoundError,
)
from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import ModelConfiguration

#: See the module docstring for why this is a single permission.
MANAGE_PERMISSION = "platform.model_configurations.manage"


@dataclass(frozen=True, slots=True)
class CreateModelConfigurationCommand:
    actor_user_id: str
    model_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    token_budget_per_month: int | None = None
    provider_credential_id: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateModelConfigurationCommand:
    actor_user_id: str
    model_configuration_id: str
    model_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    token_budget_per_month: int | None = None
    provider_credential_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelConfigurationActionCommand:
    actor_user_id: str
    model_configuration_id: str


@dataclass(frozen=True, slots=True)
class TenantAccessCommand:
    actor_user_id: str
    model_configuration_id: str
    tenant_id: str


@dataclass(frozen=True, slots=True)
class ListModelConfigurationsForPlatformQuery:
    actor_user_id: str
    include_archived: bool = True


@dataclass(frozen=True, slots=True)
class ModelConfigurationWithAccess:
    configuration: ModelConfiguration
    #: Tenants currently granted this configuration. Returned with the
    #: configuration rather than behind a second call because the console's
    #: only useful view of the catalogue is "what exists, and who can use it".
    tenant_ids: list[UUID]


class _PlatformModelConfigurationUseCase:
    """Shared authorization preamble.

    Every operation here answers the same question first -- does this actor
    hold `platform.model_configurations.manage`? -- so it is asked in one
    place. A per-use-case copy is how one of them eventually ends up missing
    the check.
    """

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def _authorize(self, uow: Any, actor_id: UUID) -> None:
        state = await compute_effective_platform_state(uow, actor_id, now=self._clock.now())
        if MANAGE_PERMISSION not in state.permissions:
            raise ModelConfigurationManagementDeniedError(MANAGE_PERMISSION)


class CreateModelConfiguration(_PlatformModelConfigurationUseCase):
    """Adds a platform-owned model to the catalogue.

    `tenant_id` is `None` and there is no field to set it otherwise: a
    configuration created through this surface belongs to the platform. The
    nullable column still exists for the tenant-owned rows that predate
    entitlements, but nothing creates new ones.

    Creating does not make it available to anybody -- that is a separate,
    deliberate grant.
    """

    async def execute(self, command: CreateModelConfigurationCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            await self._authorize(uow, actor_id)

            configuration = ModelConfiguration(
                id=uuid4(),
                tenant_id=None,
                provider_credential_id=(
                    UUID(command.provider_credential_id)
                    if command.provider_credential_id
                    else None
                ),
                model_name=command.model_name,
                parameters=dict(command.parameters),
                token_budget_per_month=command.token_budget_per_month,
                created_at=now,
                updated_at=now,
            )
            await uow.model_configurations.add(configuration)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=None,
                action="platform.model_configuration_created",
                resource_type="model_configuration",
                resource_id=configuration.id,
                result="success",
                metadata={"model_name": command.model_name},
            )
            return configuration.id


class UpdateModelConfiguration(_PlatformModelConfigurationUseCase):
    async def execute(self, command: UpdateModelConfigurationCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        configuration_id = UUID(command.model_configuration_id)

        async with self._uow_factory(actor_id) as uow:
            await self._authorize(uow, actor_id)

            configuration = await uow.model_configurations.get_by_id(configuration_id)
            if configuration is None:
                raise ModelConfigurationNotFoundError(command.model_configuration_id)

            configuration.update_details(
                model_name=command.model_name,
                parameters=dict(command.parameters),
                token_budget_per_month=command.token_budget_per_month,
                provider_credential_id=(
                    UUID(command.provider_credential_id)
                    if command.provider_credential_id
                    else None
                ),
                now=self._clock.now(),
            )
            await uow.model_configurations.save(configuration)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=None,
                action="platform.model_configuration_updated",
                resource_type="model_configuration",
                resource_id=configuration_id,
                result="success",
                metadata={"model_name": command.model_name},
            )


class ArchiveModelConfiguration(_PlatformModelConfigurationUseCase):
    """Withdraws a configuration from new assignments.

    **Assistants already using it keep working**, and their tenants' grants
    stay in place. That is the difference between archiving and revoking: this
    stops the model being offered, revoking takes it away -- and the database
    refuses the latter while an assistant still depends on it. Retiring a
    model across a fleet is therefore archive first, then migrate assistants,
    then revoke, in that order, with nothing broken at any step.
    """

    async def execute(self, command: ModelConfigurationActionCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        configuration_id = UUID(command.model_configuration_id)

        async with self._uow_factory(actor_id) as uow:
            await self._authorize(uow, actor_id)

            configuration = await uow.model_configurations.get_by_id(configuration_id)
            if configuration is None:
                raise ModelConfigurationNotFoundError(command.model_configuration_id)

            configuration.archive(now=self._clock.now())
            await uow.model_configurations.save(configuration)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=None,
                action="platform.model_configuration_archived",
                resource_type="model_configuration",
                resource_id=configuration_id,
                result="success",
                metadata={},
            )


class RestoreModelConfiguration(_PlatformModelConfigurationUseCase):
    async def execute(self, command: ModelConfigurationActionCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        configuration_id = UUID(command.model_configuration_id)

        async with self._uow_factory(actor_id) as uow:
            await self._authorize(uow, actor_id)

            configuration = await uow.model_configurations.get_by_id(configuration_id)
            if configuration is None:
                raise ModelConfigurationNotFoundError(command.model_configuration_id)

            configuration.restore(now=self._clock.now())
            await uow.model_configurations.save(configuration)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=None,
                action="platform.model_configuration_restored",
                resource_type="model_configuration",
                resource_id=configuration_id,
                result="success",
                metadata={},
            )


class ListModelConfigurationsForPlatform(_PlatformModelConfigurationUseCase):
    async def execute(
        self, query: ListModelConfigurationsForPlatformQuery
    ) -> list[ModelConfigurationWithAccess]:
        actor_id = UUID(query.actor_user_id)

        async with self._uow_factory(actor_id) as uow:
            await self._authorize(uow, actor_id)

            configurations = await uow.model_configurations.list_all(
                include_archived=query.include_archived
            )
            return [
                ModelConfigurationWithAccess(
                    configuration=configuration,
                    tenant_ids=await uow.tenant_model_access.list_tenant_ids_for_configuration(
                        configuration.id
                    ),
                )
                for configuration in configurations
            ]


class GrantModelConfigurationToTenant(_PlatformModelConfigurationUseCase):
    """Makes a configuration available to one tenant.

    Idempotent: granting an existing grant is the same state, not an error.
    """

    async def execute(self, command: TenantAccessCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        configuration_id = UUID(command.model_configuration_id)
        tenant_id = UUID(command.tenant_id)

        async with self._uow_factory(actor_id) as uow:
            await self._authorize(uow, actor_id)

            if await uow.model_configurations.get_by_id(configuration_id) is None:
                raise ModelConfigurationNotFoundError(command.model_configuration_id)

            await uow.tenant_model_access.grant(
                tenant_id=tenant_id,
                model_configuration_id=configuration_id,
                granted_by_user_id=actor_id,
            )
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="platform.model_configuration_granted",
                resource_type="model_configuration",
                resource_id=configuration_id,
                result="success",
                metadata={"tenant_id": str(tenant_id)},
            )


class RevokeModelConfigurationFromTenant(_PlatformModelConfigurationUseCase):
    """Takes a configuration away from one tenant.

    Refused while any of that tenant's assistants still uses it. The
    alternative -- revoking anyway -- would leave production assistants
    pointing at a configuration their tenant may no longer use, which is
    exactly the state this design exists to make unrepresentable. The operator
    is told how many assistants block it so the next step is obvious.
    """

    async def execute(self, command: TenantAccessCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        configuration_id = UUID(command.model_configuration_id)
        tenant_id = UUID(command.tenant_id)

        async with self._uow_factory(actor_id) as uow:
            await self._authorize(uow, actor_id)

            blocking = await uow.tenant_model_access.revoke(
                tenant_id=tenant_id, model_configuration_id=configuration_id
            )
            if blocking:
                raise ModelConfigurationInUseError(
                    f"{blocking} assistant(s) in this tenant still use this model "
                    "configuration. Move them to another model first."
                )

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="platform.model_configuration_revoked",
                resource_type="model_configuration",
                resource_id=configuration_id,
                result="success",
                metadata={"tenant_id": str(tenant_id)},
            )
