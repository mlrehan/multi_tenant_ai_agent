"""``/v1/tenants/{tenant_id}/...`` -- the AI Chatbot console and agent inbox.

A second router on the same prefix as `router.py`, mounted alongside it. Split
because these are a different product surface (configuring one chatbot, and
working the handoff queue) and `router.py` is already the largest module in the
API package -- not because the authorization model differs. It does not: every
handler here resolves effective tenant permissions through the same dependency
chain and passes them into the use case, which makes the decision.

**Nothing here trusts a client-supplied id.** `tenant_id` comes from the path
but is re-validated by the tenant resolver dependency against real membership
rows; team, conversation and membership ids are validated inside the use cases
against *this* tenant's rows, and a mismatch answers 404 rather than 403 so a
resource in another tenant is not provably real.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import StreamingResponse

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.deps.permission_resolver import get_effective_tenant_permissions
from iam_platform.api.v1.assistants import chatbot_schemas as schemas
from iam_platform.application.ai_resources.handoff import (
    AGENT_PERMISSION,
    ClaimConversation,
    ClaimConversationCommand,
    PostAgentMessage,
    PostAgentMessageCommand,
    RequestHandoff,
    RequestHandoffCommand,
    ReturnConversationToAi,
    ReturnConversationToAiCommand,
    SetAgentTyping,
    SetAgentTypingCommand,
    SetConversationAiMode,
    SetConversationAiModeCommand,
    resolve_queue_team_scope,
)
from iam_platform.application.ai_resources.manage_chatbot import (
    GetChatbotSettings,
    GetChatbotSettingsQuery,
    ListTeams,
    ListTeamsQuery,
    SaveTeam,
    SaveTeamCommand,
    UpdateChatbotSettings,
    UpdateChatbotSettingsCommand,
)
from iam_platform.application.ai_resources.manage_entitlements import (
    GetTenantEntitlements,
    GetTenantEntitlementsQuery,
)
from iam_platform.application.ai_resources.manage_presentation import (
    UpdateAssistantBehaviour,
    UpdateAssistantBehaviourCommand,
    UpdateWidgetPresentation,
    UpdateWidgetPresentationCommand,
)
from iam_platform.application.ai_resources.manage_push import (
    SubscribeToPush,
    SubscribeToPushCommand,
    UnsubscribeFromPush,
    UnsubscribeFromPushCommand,
)
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.domain.ai_resources.chatbot import DEFAULT_AVOID, default_role
from iam_platform.domain.ai_resources.entities import HandoffInitiator

logger = logging.getLogger("iam_platform.api.v1.assistants.chatbot_router")

router = APIRouter(prefix="/v1/tenants/{tenant_id}", tags=["ai-chatbot"])


# --- plan -------------------------------------------------------------------


@router.get("/plan", response_model=schemas.TenantPlanResponse)
async def get_tenant_plan(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.TenantPlanResponse:
    """The tenant's own limits and current usage.

    No permission beyond membership, deliberately: every member sees buttons
    disabled by a capability they lack, and a screen that cannot explain why is
    worse than one that can. Nothing here is another tenant's data, and the
    write path is a different authority *and* a different table grant.
    """
    del permissions
    use_case = GetTenantEntitlements(
        container.ai_resource_uow_factory, container.clock, container.tenant_quota
    )
    view = await use_case.execute(
        GetTenantEntitlementsQuery(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id
        )
    )
    e = view.entitlements
    return schemas.TenantPlanResponse(
        max_knowledge_bases=e.max_knowledge_bases,
        max_chat_widgets=e.max_chat_widgets,
        max_messages_per_day=e.max_messages_per_day,
        max_tokens_per_month=e.max_tokens_per_month,
        allow_own_provider_credentials=e.allow_own_provider_credentials,
        allow_create_assistant=e.allow_create_assistant,
        allow_invite_members=e.allow_invite_members,
        allow_create_roles=e.allow_create_roles,
        knowledge_bases_used=view.knowledge_bases_used,
        chat_widgets_used=view.chat_widgets_used,
        assistants_used=view.assistants_used,
        messages_used_today=view.messages_used_today,
        tokens_used_this_month=view.tokens_used_this_month,
        effective_daily_message_limit=view.effective_daily_message_limit,
    )


# --- chatbot settings -------------------------------------------------------


@router.get("/chatbot-settings", response_model=schemas.ChatbotSettingsResponse)
async def get_chatbot_settings(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ChatbotSettingsResponse:
    del permissions
    settings = await GetChatbotSettings(
        container.ai_resource_uow_factory, container.clock
    ).execute(
        GetChatbotSettingsQuery(actor_user_id=str(claims.user_id), tenant_id=tenant_id)
    )
    plan = await GetTenantEntitlements(
        container.ai_resource_uow_factory, container.clock
    ).execute(
        GetTenantEntitlementsQuery(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id
        )
    )
    return _settings_response(settings, plan.effective_daily_message_limit)


@router.put("/chatbot-settings", response_model=schemas.ChatbotSettingsResponse)
async def update_chatbot_settings(
    tenant_id: str,
    body: schemas.UpdateChatbotSettingsRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ChatbotSettingsResponse:
    settings = await UpdateChatbotSettings(
        container.ai_resource_uow_factory, container.clock
    ).execute(
        UpdateChatbotSettingsCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            permissions=permissions,
            ai_chatbot_enabled=body.ai_chatbot_enabled,
            company_name=body.company_name,
            company_description=body.company_description,
            industry=body.industry,
            allow_human_handoff=body.allow_human_handoff,
            add_ai_summary_as_internal_comment=body.add_ai_summary_as_internal_comment,
            allow_ai_for_unassigned_conversations=(
                body.allow_ai_for_unassigned_conversations
            ),
            daily_message_limit=body.daily_message_limit,
            share_visitor_location=body.share_visitor_location,
            conversation_retention_days=body.conversation_retention_days,
        )
    )
    plan = await GetTenantEntitlements(
        container.ai_resource_uow_factory, container.clock
    ).execute(
        GetTenantEntitlementsQuery(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id
        )
    )
    return _settings_response(settings, plan.effective_daily_message_limit)


# --- teams ------------------------------------------------------------------


@router.get("/teams", response_model=schemas.TeamListResponse)
async def list_teams(
    tenant_id: str,
    active_only: bool = False,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.TeamListResponse:
    del permissions
    items = await ListTeams(container.ai_resource_uow_factory).execute(
        ListTeamsQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            active_only=active_only,
        )
    )
    return schemas.TeamListResponse(
        teams=[
            schemas.TeamResponse(
                id=t.id,
                name=t.name,
                description=t.description,
                is_active=t.is_active,
                member_ids=members,
            )
            for t, members in items
        ]
    )


@router.post(
    "/teams", status_code=status.HTTP_201_CREATED, response_model=schemas.TeamResponse
)
async def create_team(
    tenant_id: str,
    body: schemas.SaveTeamRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.TeamResponse:
    return await _save_team(tenant_id, body, None, claims, permissions, container)


@router.put("/teams/{team_id}", response_model=schemas.TeamResponse)
async def update_team(
    tenant_id: str,
    team_id: UUID,
    body: schemas.SaveTeamRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.TeamResponse:
    return await _save_team(tenant_id, body, team_id, claims, permissions, container)


async def _save_team(
    tenant_id: str,
    body: schemas.SaveTeamRequest,
    team_id: UUID | None,
    claims: AccessTokenClaims,
    permissions: frozenset[str],
    container: AppContainer,
) -> schemas.TeamResponse:
    team = await SaveTeam(container.ai_resource_uow_factory, container.clock).execute(
        SaveTeamCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            permissions=permissions,
            name=body.name,
            description=body.description,
            team_id=str(team_id) if team_id else None,
            is_active=body.is_active,
            member_ids=tuple(str(m) for m in body.member_ids),
        )
    )
    return schemas.TeamResponse(
        id=team.id,
        name=team.name,
        description=team.description,
        is_active=team.is_active,
        member_ids=list(body.member_ids),
    )


# --- handoff and the agent inbox -------------------------------------------


@router.post(
    "/conversations/{conversation_id}/handoff",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def hand_off_conversation(
    tenant_id: str,
    conversation_id: UUID,
    body: schemas.HandoffRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """An agent routing a conversation to a team.

    `initiated_by=AGENT` because this endpoint is authenticated -- the
    visitor-initiated path runs on the public widget surface and records
    `VISITOR`. Recording which is which is the point: "who decided this needed
    a human?" is the first question asked when reviewing a queue that is too
    long or too short.
    """
    del permissions
    await RequestHandoff(
        container.ai_resource_uow_factory,
        container.clock,
        container.conversation_events,
    ).execute(
        RequestHandoffCommand(
            tenant_id=tenant_id,
            conversation_id=str(conversation_id),
            team_id=str(body.team_id) if body.team_id else None,
            reason=body.reason,
            initiated_by=HandoffInitiator.AGENT,
            actor_user_id=str(claims.user_id),
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/push/public-key", response_model=schemas.PushPublicKeyResponse)
async def get_push_public_key(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.PushPublicKeyResponse:
    """The VAPID public key, or `enabled: false` if push is not configured.

    The key is *meant* to be public -- it ships to every browser, which is how
    the push service verifies our signature. Behind authentication anyway
    because there is no reason for an anonymous caller to enumerate it.
    """
    del tenant_id, claims, permissions
    if not container.web_push.is_configured:
        return schemas.PushPublicKeyResponse(enabled=False, public_key=None)
    return schemas.PushPublicKeyResponse(
        enabled=True, public_key=container.settings.push.vapid_public_key
    )


@router.post("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe_to_push(
    tenant_id: str,
    body: schemas.SubscribeToPushRequest,
    request: Request,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Registers this browser for handoff notifications."""
    await SubscribeToPush(container.ai_resource_uow_factory, container.clock).execute(
        SubscribeToPushCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            permissions=permissions,
            endpoint=body.endpoint,
            p256dh_key=body.p256dh_key,
            auth_key=body.auth_key,
            # Diagnostics only -- which browser stopped working. Truncated by
            # the use case; a User-Agent is caller-controlled free text.
            user_agent=request.headers.get("user-agent"),
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_from_push(
    tenant_id: str,
    body: schemas.UnsubscribeFromPushRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Stops notifying this browser. Idempotent."""
    del permissions
    await UnsubscribeFromPush(container.ai_resource_uow_factory).execute(
        UnsubscribeFromPushCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            endpoint=body.endpoint,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/conversations/unassigned", response_model=schemas.UnassignedInboxResponse)
async def list_unassigned_conversations(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.UnassignedInboxResponse:
    """The queue.

    **Route order matters and this one is deliberate**: FastAPI matches in
    definition order, and `/conversations/{conversation_id}` in the sibling
    router would swallow `/conversations/unassigned` and try to parse
    "unassigned" as a UUID. This router is mounted first for exactly that
    reason -- the same trap that produced a 500 on `/conversations/search`.
    """
    if AGENT_PERMISSION not in permissions:
        from iam_platform.application.ai_resources.exceptions import (
            PermissionDeniedError,
        )

        raise PermissionDeniedError(AGENT_PERMISSION)

    tenant_uuid = UUID(tenant_id)
    async with container.ai_resource_uow_factory(claims.user_id, tenant_uuid) as uow:
        # Scoped to the caller's own teams unless they hold oversight. The
        # scope goes *into* the query, so another team's conversation is never
        # loaded rather than loaded and hidden.
        team_ids = await resolve_queue_team_scope(
            uow, tenant_id=tenant_uuid, user_id=claims.user_id, permissions=permissions
        )
        rows = await uow.handoff.list_unassigned(
            tenant_id=tenant_uuid, team_ids=team_ids
        )
        return schemas.UnassignedInboxResponse(
            conversations=[
                schemas.UnassignedConversationResponse(
                    id=r.id,
                    assigned_team_id=r.assigned_team_id,
                    handoff_reason=r.handoff_reason,
                    handoff_at=r.handoff_at,
                    handoff_initiated_by=r.handoff_initiated_by,
                    title=r.title,
                    last_message_at=r.last_message_at,
                )
                for r in rows
            ]
        )


@router.post(
    "/conversations/{conversation_id}/claim", status_code=status.HTTP_204_NO_CONTENT
)
async def claim_conversation(
    tenant_id: str,
    conversation_id: UUID,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Take ownership. 409 if another agent got there first."""
    membership_id = await _membership_id(container, claims, tenant_id)
    await ClaimConversation(
        container.ai_resource_uow_factory,
        container.clock,
        container.conversation_events,
    ).execute(
        ClaimConversationCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            conversation_id=str(conversation_id),
            membership_id=str(membership_id),
            permissions=permissions,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def post_agent_message(
    tenant_id: str,
    conversation_id: UUID,
    body: schemas.AgentMessageRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """An agent replies, or leaves an internal note.

    **Neither consumes AI quota.** The daily counter is reserved on the AI path
    only; a support team answering tickets costs no inference and must not be
    able to exhaust the chatbot's allowance by being busy.
    """
    membership_id = await _membership_id(container, claims, tenant_id)
    await PostAgentMessage(
        container.ai_resource_uow_factory,
        container.clock,
        container.conversation_events,
    ).execute(
        PostAgentMessageCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            conversation_id=str(conversation_id),
            membership_id=str(membership_id),
            permissions=permissions,
            content=body.content,
            internal=body.internal,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/return-to-ai",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def return_conversation_to_ai(
    tenant_id: str,
    conversation_id: UUID,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """The only supported way back to the AI after a human takeover."""
    await ReturnConversationToAi(
        container.ai_resource_uow_factory, container.clock
    ).execute(
        ReturnConversationToAiCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            conversation_id=str(conversation_id),
            permissions=permissions,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/typing",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_agent_typing(
    tenant_id: str,
    conversation_id: UUID,
    body: schemas.AgentTypingRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Heartbeat: this agent is composing a reply, or has stopped.

    Permission-gated like every other agent action -- otherwise any caller
    could make a widget announce that a human is about to reply.
    """
    await SetAgentTyping(
        container.ai_resource_uow_factory, container.typing_indicators
    ).execute(
        SetAgentTypingCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            conversation_id=str(conversation_id),
            typing=body.typing,
            permissions=permissions,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/ai-mode",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_conversation_ai_mode(
    tenant_id: str,
    conversation_id: UUID,
    body: schemas.SetConversationAiModeRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Holds a conversation against the automatic return-to-AI, or releases it.

    Distinct from `return-to-ai`, which moves the thread now. This decides
    whether it may be moved *without* an agent -- so an agent who is working a
    difficult conversation is not interrupted by the assistant after thirty
    seconds of reading.
    """
    await SetConversationAiMode(
        container.ai_resource_uow_factory, container.clock
    ).execute(
        SetConversationAiModeCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            conversation_id=str(conversation_id),
            ai_fallback_enabled=body.ai_fallback_enabled,
            permissions=permissions,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/conversation-events")
async def stream_conversation_events(
    tenant_id: str,
    request: Request,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> StreamingResponse:
    """Realtime inbox updates over SSE.

    **The tenant scope comes from the authenticated session, never the query
    string** -- it is resolved by the same dependency chain as every other
    route here, so a caller cannot subscribe to a tenant they are not a member
    of by editing a URL.

    A `: keepalive` comment every 20s stops idle proxies closing the
    connection; SSE comment lines are ignored by `EventSource` and by the
    console's reader, so they cost a few bytes and nothing else.
    """
    if AGENT_PERMISSION not in permissions:
        from iam_platform.application.ai_resources.exceptions import (
            PermissionDeniedError,
        )

        raise PermissionDeniedError(AGENT_PERMISSION)

    tenant_uuid = UUID(tenant_id)

    # Resolved once, at subscribe time, and applied to every event. The Redis
    # channel is per *tenant*, so without this an agent staffing Admissions is
    # notified about -- and chimed for -- a billing dispute routed to Accounts,
    # which the inbox they then open will not even show them.
    #
    # A stale scope lasts only as long as one stream: moving someone between
    # teams takes effect on their next reconnect, which is seconds, and the
    # alternative is a membership query per delivered event.
    async with container.ai_resource_uow_factory(claims.user_id, tenant_uuid) as scope_uow:
        visible_team_ids = await resolve_queue_team_scope(
            scope_uow,
            tenant_id=tenant_uuid,
            user_id=claims.user_id,
            permissions=permissions,
        )

    def _may_see(event: dict[str, Any]) -> bool:
        if visible_team_ids is None:
            return True
        team_id = event.get("team_id")
        if team_id is None:
            # An event carrying no team is tenant-wide housekeeping, not a
            # conversation another team owns. Withholding it would silently
            # stop scoped agents refreshing at all.
            return True
        return any(str(t) == str(team_id) for t in visible_team_ids)

    async def _events() -> AsyncIterator[str]:
        """Subscription events, with a keepalive on idle.

        **The subscription is drained by a separate task into a queue, and the
        timeout is applied to the queue -- never to the subscription itself.**
        `asyncio.wait_for` cancels whatever it is waiting on when it fires, and
        throwing `CancelledError` into an async generator suspended at an
        `await` *ends that generator*. Wrapping `subscription.__anext__()`
        therefore killed the subscription on the first idle 20 seconds; the
        next call raised `StopAsyncIteration` and this endpoint returned, so
        the stream closed. It looked like it worked because `EventSource`
        reconnects silently -- while every event published during the gap was
        lost outright, Redis pub/sub having no replay.

        Cancelling a `queue.get()` is safe: the queue keeps its contents and
        the producer is untouched.
        """
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        subscription = container.conversation_events.subscribe(tenant_id=tenant_uuid)

        async def _drain() -> None:
            try:
                async for event in subscription:
                    await queue.put(event)
            finally:
                # Also runs when this task is cancelled, so the consumer below
                # is always released rather than waiting out a full timeout on
                # a subscription that has already ended.
                await queue.put(None)

        pump = asyncio.create_task(_drain())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:  # the subscription ended
                    return
                if await request.is_disconnected():
                    return
                if not _may_see(event):
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # Cancel first and wait for it: the pump owns the iteration, so
            # closing the generator from here while that task still holds it
            # raises "asynchronous generator is already running". Once the
            # pump has unwound, the generator's own `finally` has already
            # unsubscribed and returned the Redis connection to the pool.
            pump.cancel()
            with suppress(asyncio.CancelledError):
                await pump
            await subscription.aclose()

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# --- helpers ----------------------------------------------------------------


def _settings_response(
    settings: object, effective_daily_limit: int | None
) -> schemas.ChatbotSettingsResponse:
    # Resolved against an empty display name: the tenant's own chatbot-facing
    # name if they set one, otherwise the shipped default. The account's
    # display name is deliberately not consulted here -- it is an identity for
    # operators and audit records, and quietly borrowing it as the nursery's
    # public-facing name is a decision the tenant never made.
    company = settings.resolved_company_name("")  # type: ignore[attr-defined]
    return schemas.ChatbotSettingsResponse(
        ai_chatbot_enabled=settings.ai_chatbot_enabled,  # type: ignore[attr-defined]
        company_name=company,
        company_description=settings.resolved_company_description(""),  # type: ignore[attr-defined]
        default_role=default_role(company),
        default_avoid=DEFAULT_AVOID,
        industry=settings.industry,  # type: ignore[attr-defined]
        allow_human_handoff=settings.allow_human_handoff,  # type: ignore[attr-defined]
        add_ai_summary_as_internal_comment=(
            settings.add_ai_summary_as_internal_comment  # type: ignore[attr-defined]
        ),
        allow_ai_for_unassigned_conversations=(
            settings.allow_ai_for_unassigned_conversations  # type: ignore[attr-defined]
        ),
        daily_message_limit=settings.daily_message_limit,  # type: ignore[attr-defined]
        effective_daily_message_limit=effective_daily_limit,
        share_visitor_location=settings.share_visitor_location,  # type: ignore[attr-defined]
        conversation_retention_days=settings.conversation_retention_days,  # type: ignore[attr-defined]
        updated_at=settings.updated_at,  # type: ignore[attr-defined]
    )


async def _membership_id(
    container: AppContainer, claims: AccessTokenClaims, tenant_id: str
) -> UUID:
    """The caller's membership in this tenant.

    Resolved server-side rather than accepted from the request: a membership id
    in a body is a claim about who you are, and an agent could otherwise claim
    a conversation *as* a colleague.
    """
    from iam_platform.application.ai_resources.exceptions import PermissionDeniedError

    tenant_uuid = UUID(tenant_id)
    async with container.ai_resource_uow_factory(claims.user_id, tenant_uuid) as uow:
        membership = await uow.tenant_memberships.get_by_tenant_and_user(
            tenant_uuid, claims.user_id
        )
    if membership is None:
        raise PermissionDeniedError(AGENT_PERMISSION)
    return membership.id


# --- assistant behaviour and widget presentation ----------------------------


@router.put("/assistants/{assistant_id}/behaviour")
async def update_assistant_behaviour(
    tenant_id: str,
    assistant_id: UUID,
    body: schemas.AssistantBehaviourRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.AssistantBehaviourResponse:
    """The Behaviour and Tone tabs.

    Authorized like any other assistant edit -- through the visibility policy
    with `for_modification=True` -- not with a permission of its own. Changing
    an assistant's brief *is* editing the assistant.
    """
    assistant = await UpdateAssistantBehaviour(
        container.ai_resource_uow_factory, container.clock
    ).execute(
        UpdateAssistantBehaviourCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            assistant_id=str(assistant_id),
            permissions=permissions,
            role_instructions=body.role_instructions,
            avoid_instructions=body.avoid_instructions,
            personality=body.personality,
            response_length=body.response_length,
        )
    )
    return schemas.AssistantBehaviourResponse(
        assistant_id=assistant.id,
        role_instructions=assistant.role_instructions or "",
        avoid_instructions=assistant.avoid_instructions or "",
        personality=assistant.personality.value,
        response_length=assistant.response_length.value,
    )


@router.put("/chat-widgets/{widget_id}/presentation")
async def update_widget_presentation(
    tenant_id: str,
    widget_id: UUID,
    body: schemas.WidgetPresentationRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.WidgetPresentationResponse:
    """The Identity and Reply Experience tabs, for one embed."""
    widget = await UpdateWidgetPresentation(
        container.ai_resource_uow_factory, container.clock
    ).execute(
        UpdateWidgetPresentationCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            widget_id=str(widget_id),
            permissions=permissions,
            chatbot_name=body.chatbot_name,
            chatbot_title=body.chatbot_title,
            avatar_key=body.avatar_key,
            greeting=body.greeting,
            show_quick_reply_suggestions=body.show_quick_reply_suggestions,
            assistant_id=str(body.assistant_id) if body.assistant_id else None,
        )
    )
    return _presentation_response(widget)


@router.get("/chat-widgets/{widget_id}/presentation")
async def get_widget_presentation(
    tenant_id: str,
    widget_id: UUID,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.WidgetPresentationResponse:
    del permissions
    from iam_platform.application.ai_resources.exceptions import ChatWidgetNotFoundError

    tenant_uuid = UUID(tenant_id)
    async with container.ai_resource_uow_factory(claims.user_id, tenant_uuid) as uow:
        widget = await uow.chat_widgets.get_for_tenant(tenant_uuid, widget_id)
    if widget is None:
        raise ChatWidgetNotFoundError(str(widget_id))
    return _presentation_response(widget)


def _presentation_response(widget: object) -> schemas.WidgetPresentationResponse:
    from iam_platform.domain.ai_resources.chatbot import (
        DEFAULT_AVATAR_KEY,
        DEFAULT_CHATBOT_NAME,
        DEFAULT_CHATBOT_TITLE,
    )

    # Defaults are resolved *here*, so the console shows the identity the
    # widget actually renders rather than empty fields that look unconfigured.
    return schemas.WidgetPresentationResponse(
        widget_id=widget.id,  # type: ignore[attr-defined]
        assistant_id=widget.assistant_id,  # type: ignore[attr-defined]
        chatbot_name=widget.chatbot_name or DEFAULT_CHATBOT_NAME,  # type: ignore[attr-defined]
        chatbot_title=widget.chatbot_title or DEFAULT_CHATBOT_TITLE,  # type: ignore[attr-defined]
        avatar_key=widget.avatar_key or DEFAULT_AVATAR_KEY,  # type: ignore[attr-defined]
        greeting=widget.greeting,  # type: ignore[attr-defined]
        show_quick_reply_suggestions=widget.show_quick_reply_suggestions,  # type: ignore[attr-defined]
    )
