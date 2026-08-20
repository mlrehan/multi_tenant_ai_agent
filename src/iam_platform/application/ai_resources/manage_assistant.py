"""Assistant lifecycle: create, list (visibility-filtered), get, publish, and
change visibility -- docs/16-schema-ai-resources.md.

Read paths go through ``load_visible_assistant``; write paths pass
``for_modification=True`` so "I can see it" never silently becomes "I can
change it".
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.authorize import load_visible_assistant
from iam_platform.application.ai_resources.entitlements import guard_capability
from iam_platform.application.ai_resources.exceptions import (
    AssistantNotFoundError,
    ModelConfigurationNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantStatus,
    ModelConfiguration,
    ResourceVisibility,
)
from iam_platform.domain.ai_resources.policies import (
    VIEW_ALL_ASSISTANTS,
    can_access_resource,
    describe_assistant,
)

CREATE_ASSISTANT_PERMISSION = "tenant.assistants.create"
PUBLISH_ASSISTANT_PERMISSION = "tenant.assistants.publish"


@dataclass(frozen=True, slots=True)
class CreateAssistantCommand:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    name: str
    description: str | None
    model_configuration_id: str
    visibility: str = "tenant"
    department_id: str | None = None
    team_id: str | None = None
    system_prompt: str | None = None


class CreateAssistant:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: CreateAssistantCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if CREATE_ASSISTANT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(CREATE_ASSISTANT_PERMISSION)

            # A capability, not a count: the platform either lets this tenant
            # build assistants or it does not. Existing assistants keep
            # working if it is later withdrawn -- nothing on a read path asks.
            await guard_capability(
                uow,
                tenant_id=tenant_id,
                clock=self._clock,
                capability="allow_create_assistant",
            )

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise PermissionDeniedError(CREATE_ASSISTANT_PERMISSION)

            model_configuration_id = UUID(command.model_configuration_id)
            # Entitlement, not visibility. RLS scopes *reads*, which is not
            # the same question as "may this tenant use this model" -- a
            # platform-owned configuration is readable by every tenant and
            # assignable only by the ones it was granted to. Reported as
            # not-found rather than forbidden so an unentitled id and a
            # nonexistent one are indistinguishable, and the
            # `fk_ai_assistants_model_configuration` constraint enforces the
            # same rule underneath in case this check is ever bypassed.
            if not await uow.model_configurations.is_available_to_tenant(
                tenant_id=tenant_id, model_configuration_id=model_configuration_id
            ):
                raise ModelConfigurationNotFoundError(command.model_configuration_id)

            assistant = AiAssistant(
                id=uuid4(),
                tenant_id=tenant_id,
                name=command.name,
                description=command.description,
                owner_membership_id=requester.membership_id,
                model_configuration_id=model_configuration_id,
                status=AssistantStatus.DRAFT,
                system_prompt=command.system_prompt,
                created_at=now,
                updated_at=now,
            )
            # Routed through the entity's own transition so the
            # "department visibility requires a department_id" invariant is
            # enforced at creation, not only on later edits.
            assistant.change_visibility(
                visibility=ResourceVisibility(command.visibility),
                department_id=UUID(command.department_id) if command.department_id else None,
                team_id=UUID(command.team_id) if command.team_id else None,
                now=now,
            )
            await uow.assistants.add(assistant)

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.assistant_created",
                resource_type="ai_assistant",
                resource_id=assistant.id,
                result="success",
                metadata={"name": command.name, "visibility": command.visibility},
            )
            return assistant.id


@dataclass(frozen=True, slots=True)
class ListAssistantsQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]


class ListAssistants:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListAssistantsQuery) -> list[AiAssistant]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                return []

            candidates = await uow.assistants.list_by_tenant(tenant_id)
            # One grants lookup for the whole list rather than per candidate.
            grants = {
                m.assistant_id: m.access_level
                for m in await uow.assistant_members.list_for_membership(requester.membership_id)
            }
            return [
                assistant
                for assistant in candidates
                if can_access_resource(
                    resource=describe_assistant(assistant),
                    requester=requester,
                    explicit_access_level=grants.get(assistant.id),
                    view_all_permission=VIEW_ALL_ASSISTANTS,
                )
            ]


@dataclass(frozen=True, slots=True)
class GetAssistantQuery:
    actor_user_id: str
    tenant_id: str
    assistant_id: str
    permissions: frozenset[str]


class GetAssistant:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: GetAssistantQuery) -> AiAssistant:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                raise AssistantNotFoundError(query.assistant_id)
            return await load_visible_assistant(
                uow, assistant_id=UUID(query.assistant_id), requester=requester
            )


@dataclass(frozen=True, slots=True)
class PublishAssistantCommand:
    actor_user_id: str
    tenant_id: str
    assistant_id: str
    permissions: frozenset[str]


class PublishAssistant:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: PublishAssistantCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if PUBLISH_ASSISTANT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(PUBLISH_ASSISTANT_PERMISSION)

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise AssistantNotFoundError(command.assistant_id)

            assistant = await load_visible_assistant(
                uow,
                assistant_id=UUID(command.assistant_id),
                requester=requester,
                for_modification=True,
            )
            assistant.publish(now=now)
            await uow.assistants.save(assistant)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.assistant_published",
                resource_type="ai_assistant",
                resource_id=assistant.id,
                result="success",
            )


@dataclass(frozen=True, slots=True)
class UpdateAssistantCommand:
    actor_user_id: str
    tenant_id: str
    assistant_id: str
    permissions: frozenset[str]
    name: str
    description: str | None
    system_prompt: str | None
    model_configuration_id: str


class UpdateAssistant:
    """Edits name/description/system prompt/model configuration.

    Gated purely by ``for_modification`` (ownership, an editor/owner grant, or
    ``tenant.assistants.manage``) -- unlike publishing, editing your own draft
    doesn't need a separate tenant-wide permission, matching
    ``ChangeAssistantVisibility``'s precedent.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: UpdateAssistantCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise AssistantNotFoundError(command.assistant_id)

            assistant = await load_visible_assistant(
                uow,
                assistant_id=UUID(command.assistant_id),
                requester=requester,
                for_modification=True,
            )

            model_configuration_id = UUID(command.model_configuration_id)
            # Same entitlement check as CreateAssistant. Skipped when the
            # configuration is unchanged, so an assistant whose model has
            # since been archived can still have its name or prompt edited --
            # archiving withdraws a model from *new* assignments, and making
            # every other edit impossible would be a different, harsher rule.
            if (
                model_configuration_id != assistant.model_configuration_id
                and not await uow.model_configurations.is_available_to_tenant(
                    tenant_id=tenant_id, model_configuration_id=model_configuration_id
                )
            ):
                raise ModelConfigurationNotFoundError(command.model_configuration_id)

            assistant.update_details(
                name=command.name,
                description=command.description,
                system_prompt=command.system_prompt,
                model_configuration_id=model_configuration_id,
                now=now,
            )
            await uow.assistants.save(assistant)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.assistant_updated",
                resource_type="ai_assistant",
                resource_id=assistant.id,
                result="success",
            )


@dataclass(frozen=True, slots=True)
class ArchiveAssistantCommand:
    actor_user_id: str
    tenant_id: str
    assistant_id: str
    permissions: frozenset[str]


class ArchiveAssistant:
    """Soft-deletes an assistant -- there is no hard delete, matching the
    soft-delete convention used everywhere else in this project (users,
    documents). Archived assistants stay visible to whoever could already see
    them but can no longer be published or modified further."""

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: ArchiveAssistantCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise AssistantNotFoundError(command.assistant_id)

            assistant = await load_visible_assistant(
                uow,
                assistant_id=UUID(command.assistant_id),
                requester=requester,
                for_modification=True,
            )
            assistant.archive(now=now)
            await uow.assistants.save(assistant)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.assistant_archived",
                resource_type="ai_assistant",
                resource_id=assistant.id,
                result="success",
            )


@dataclass(frozen=True, slots=True)
class ChangeAssistantVisibilityCommand:
    actor_user_id: str
    tenant_id: str
    assistant_id: str
    permissions: frozenset[str]
    visibility: str
    department_id: str | None = None
    team_id: str | None = None


class ChangeAssistantVisibility:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: ChangeAssistantVisibilityCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise AssistantNotFoundError(command.assistant_id)

            assistant = await load_visible_assistant(
                uow,
                assistant_id=UUID(command.assistant_id),
                requester=requester,
                for_modification=True,
            )
            previous = assistant.visibility
            assistant.change_visibility(
                visibility=ResourceVisibility(command.visibility),
                department_id=UUID(command.department_id) if command.department_id else None,
                team_id=UUID(command.team_id) if command.team_id else None,
                now=now,
            )
            await uow.assistants.save(assistant)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.assistant_visibility_changed",
                resource_type="ai_assistant",
                resource_id=assistant.id,
                result="success",
                metadata={"from": str(previous), "to": command.visibility},
            )


@dataclass(frozen=True, slots=True)
class ListModelConfigurationsQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class TenantModelOption:
    """One assignable model, plus whose key pays for it.

    The credential is carried here rather than on `ModelConfiguration` because
    it is a property of *this tenant's grant*, not of the shared configuration
    -- the same model answers on a different account for a different tenant.
    """

    configuration: ModelConfiguration
    #: None means the platform's key answers, which is every grant's default.
    provider_credential_id: UUID | None


class ListModelConfigurations:
    """Exactly what this tenant may assign -- nothing decorative.

    Every row returned here is one `CreateAssistant`/`UpdateAssistant` will
    accept, so the console can offer all of them without disabled entries or
    explanations. That was not true before entitlements: this query used to
    return platform-owned rows the foreign key would then reject, and the
    console had to render them greyed out with a note about a foreign key.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, query: ListModelConfigurationsQuery
    ) -> list[TenantModelOption]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                return []
            configurations = await uow.model_configurations.list_available_to_tenant(
                tenant_id
            )
            attached = await uow.model_configurations.credentials_for_tenant(tenant_id)
            return [
                TenantModelOption(
                    configuration=c, provider_credential_id=attached.get(c.id)
                )
                for c in configurations
            ]
