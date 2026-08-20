"""Tenant-side chatbot configuration and support teams.

Everything here is the tenant's own decision about their own bot, so it runs on
the tenant unit of work under RLS and is gated on a tenant permission -- not on
a platform one. The platform's say over the same subject is the entitlement
ceiling, applied separately and from the other side.

**The one place the two meet is `daily_message_limit`,** and it is guarded in
both directions: the write refuses a value above the platform ceiling with a
message naming the ceiling, and `TenantEntitlements.effective_daily_message_limit`
clamps on *read* as well. Either alone would be enough today; both means a row
written before the check existed, or by a future path that forgets it, still
cannot raise the cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.entitlements import resolve_entitlements
from iam_platform.application.ai_resources.exceptions import (
    ChatbotSettingsInvalidError,
    PermissionDeniedError,
    TeamNotFoundError,
)
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.chatbot import (
    DEFAULT_RETENTION_DAYS,
    MAX_COMPANY_DESCRIPTION_CHARS,
    MAX_INDUSTRY_CHARS,
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    TenantChatbotSettings,
)
from iam_platform.domain.tenancy.teams import TenantTeam

#: Configuring the chatbot changes what an assistant says to the public, which
#: is the same authority as changing what it knows. Reused rather than minted
#: fresh: a permission per screen produces a catalogue nobody can reason about.
MANAGE_CHATBOT_PERMISSION = "tenant.documents.upload"


@dataclass(frozen=True, slots=True)
class GetChatbotSettingsQuery:
    actor_user_id: str
    tenant_id: str


class GetChatbotSettings:
    """Reads settings, materialising defaults for a tenant that has none.

    Returns a real object rather than `None` so the console never has to
    special-case "never configured" -- the defaults *are* what the answer path
    uses, so showing them is showing the truth.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: GetChatbotSettingsQuery) -> TenantChatbotSettings:
        tenant_id = UUID(query.tenant_id)
        async with self._uow_factory(UUID(query.actor_user_id), tenant_id) as uow:
            stored = await uow.chatbot_settings.get_for_tenant(tenant_id)
        if stored is not None:
            return stored
        now = self._clock.now()
        return TenantChatbotSettings(
            id=uuid4(), tenant_id=tenant_id, created_at=now, updated_at=now
        )


@dataclass(frozen=True, slots=True)
class UpdateChatbotSettingsCommand:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    ai_chatbot_enabled: bool
    company_name: str | None
    company_description: str
    industry: str
    allow_human_handoff: bool
    add_ai_summary_as_internal_comment: bool
    allow_ai_for_unassigned_conversations: bool
    daily_message_limit: int | None
    share_visitor_location: bool
    conversation_retention_days: int = DEFAULT_RETENTION_DAYS


class UpdateChatbotSettings:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self, command: UpdateChatbotSettingsCommand
    ) -> TenantChatbotSettings:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        if len(command.company_description) > MAX_COMPANY_DESCRIPTION_CHARS:
            raise ChatbotSettingsInvalidError(
                f"the company description must be "
                f"{MAX_COMPANY_DESCRIPTION_CHARS} characters or fewer"
            )
        if len(command.industry) > MAX_INDUSTRY_CHARS:
            raise ChatbotSettingsInvalidError(
                f"the industry must be {MAX_INDUSTRY_CHARS} characters or fewer"
            )

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_CHATBOT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_CHATBOT_PERMISSION)

            entitlements = await resolve_entitlements(
                uow, tenant_id=tenant_id, clock=self._clock
            )
            requested = command.daily_message_limit
            ceiling = entitlements.max_messages_per_day
            if requested is not None and ceiling is not None and requested > ceiling:
                # Refused rather than silently clamped: a tenant admin who
                # types 5,000 and is shown 5,000 afterwards will believe it
                # took effect. Naming the ceiling is safe -- it is their own
                # plan -- and is the only way they know what to ask for.
                raise ChatbotSettingsInvalidError(
                    f"your plan allows at most {ceiling} AI messages per day; "
                    f"{requested} is above that ceiling"
                )

            # Bounded here as well as by the database CHECK. The constraint
            # protects the table; this protects the *person*, who otherwise
            # gets an IntegrityError-shaped 500 for typing a zero.
            retention = command.conversation_retention_days
            if not MIN_RETENTION_DAYS <= retention <= MAX_RETENTION_DAYS:
                raise ChatbotSettingsInvalidError(
                    f"conversation retention must be between {MIN_RETENTION_DAYS} "
                    f"and {MAX_RETENTION_DAYS} days; {retention} is outside that range"
                )

            existing = await uow.chatbot_settings.get_for_tenant(tenant_id)
            settings = existing or TenantChatbotSettings(
                id=uuid4(), tenant_id=tenant_id, created_at=now, updated_at=now
            )
            settings.ai_chatbot_enabled = command.ai_chatbot_enabled
            settings.company_name = (command.company_name or "").strip() or None
            settings.company_description = command.company_description
            settings.industry = command.industry
            settings.allow_human_handoff = command.allow_human_handoff
            settings.add_ai_summary_as_internal_comment = (
                command.add_ai_summary_as_internal_comment
            )
            settings.allow_ai_for_unassigned_conversations = (
                command.allow_ai_for_unassigned_conversations
            )
            settings.daily_message_limit = requested
            settings.share_visitor_location = command.share_visitor_location
            settings.conversation_retention_days = retention
            settings.updated_at = now

            await uow.chatbot_settings.upsert(settings)
            # Turning the AI on or off, and changing the handoff policy, both
            # alter what the public gets -- audited for the same reason an
            # entitlement change is.
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="tenant.chatbot_settings.updated",
                resource_type="tenant_chatbot_settings",
                resource_id=settings.id,
                result="success",
                metadata={
                    "ai_chatbot_enabled": command.ai_chatbot_enabled,
                    "allow_human_handoff": command.allow_human_handoff,
                    "add_ai_summary_as_internal_comment": (
                        command.add_ai_summary_as_internal_comment
                    ),
                    "allow_ai_for_unassigned_conversations": (
                        command.allow_ai_for_unassigned_conversations
                    ),
                    "daily_message_limit": requested,
                },
            )
        return settings


# --- teams ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListTeamsQuery:
    actor_user_id: str
    tenant_id: str
    active_only: bool = False


class ListTeams:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, query: ListTeamsQuery
    ) -> list[tuple[TenantTeam, list[UUID]]]:
        tenant_id = UUID(query.tenant_id)
        async with self._uow_factory(UUID(query.actor_user_id), tenant_id) as uow:
            teams = await uow.teams.list_for_tenant(
                tenant_id, active_only=query.active_only
            )
            return [
                (
                    team,
                    await uow.teams.list_members(tenant_id=tenant_id, team_id=team.id),
                )
                for team in teams
            ]


@dataclass(frozen=True, slots=True)
class SaveTeamCommand:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    name: str
    description: str | None = None
    team_id: str | None = None
    is_active: bool = True
    member_ids: tuple[str, ...] = ()


class SaveTeam:
    """Create or update. One use case because the console has one form.

    Splitting them would duplicate the permission check, the tenant scoping
    and the member-roster write for no gain -- the only difference is whether
    an id came in.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: SaveTeamCommand) -> TenantTeam:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_CHATBOT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_CHATBOT_PERMISSION)

            if command.team_id:
                team = await uow.teams.get(
                    tenant_id=tenant_id, team_id=UUID(command.team_id)
                )
                if team is None:
                    # 404 rather than 403: a team id belonging to another
                    # tenant must not be provably real. RLS already hides the
                    # row; this is what the caller is told.
                    raise TeamNotFoundError(command.team_id)
                team.rename(command.name, now=now)
                team.description = command.description
                if command.is_active:
                    team.activate(now=now)
                else:
                    team.deactivate(now=now)
                await uow.teams.save(team)
            else:
                team = TenantTeam(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    name=command.name,
                    description=command.description,
                    is_active=command.is_active,
                    created_at=now,
                    updated_at=now,
                )
                await uow.teams.add(team)

            # Membership ids are re-validated against this tenant by the
            # composite FK on `tenant_team_members` -- another tenant's
            # membership id is refused by Postgres, not by a check here that
            # a future edit might drop.
            await uow.teams.set_members(
                tenant_id=tenant_id,
                team_id=team.id,
                membership_ids=[UUID(m) for m in command.member_ids],
            )
        return team
