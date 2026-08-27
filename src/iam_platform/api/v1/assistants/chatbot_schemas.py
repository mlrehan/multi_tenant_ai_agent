"""Request/response shapes for the AI Chatbot console and the handoff inbox.

Kept beside `schemas.py` rather than inside it: that module is already the
largest schema file in the project, and these belong to one coherent product
surface (the chatbot configuration screen and the agent inbox) rather than to
the assistant/knowledge-base CRUD it describes.

**Length caps are declared here as well as in the domain and the database.**
Three places sounds redundant and is not: Pydantic gives the client a field-
level error it can render next to the input, the domain gives a useful message
to any caller that bypasses the API, and the CHECK constraint means a
migration or a direct write cannot store something the prompt builder would
then have to truncate silently.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from iam_platform.domain.ai_resources.chatbot import (
    MAX_COMPANY_DESCRIPTION_CHARS,
    MAX_DIRECT_TEXT_CHARS,
    MAX_INDUSTRY_CHARS,
)


class ChatbotSettingsResponse(BaseModel):
    ai_chatbot_enabled: bool
    #: **Resolved**, not raw. A tenant who has never opened the Company tab is
    #: shown the shipped default rather than an empty box, and saving persists
    #: what they were shown -- so the prompt the assistant runs on and the text
    #: the administrator can see are the same words.
    company_name: str | None
    company_description: str
    industry: str
    #: The shipped assistant brief and restrictions, named for this company.
    #:
    #: Served from here rather than restated in the console, because they are
    #: the exact strings the prompt builder falls back to. A copy in TypeScript
    #: would drift the moment either side is edited, and the drift would be
    #: invisible: the form would show one brief and the model would follow
    #: another.
    default_role: str
    default_avoid: str
    allow_human_handoff: bool
    add_ai_summary_as_internal_comment: bool
    allow_ai_for_unassigned_conversations: bool
    #: What the tenant asked for. `None` => inherit the platform ceiling.
    daily_message_limit: int | None
    #: What is actually enforced, after clamping to the platform ceiling.
    #: Returned separately so a tenant admin can see that their 5,000 is
    #: being applied as 1,000 rather than discovering it from a 429.
    effective_daily_message_limit: int | None
    share_visitor_location: bool
    conversation_retention_days: int
    #: The tenant's own brief, raw ("" when never written) so the console can
    #: distinguish "unset, showing the default above as a placeholder" from
    #: "the tenant typed the default in". Written by the separate
    #: `PUT /chatbot-settings/behaviour` route, which is why they are absent
    #: from `UpdateChatbotSettingsRequest`.
    role_instructions: str = ""
    avoid_instructions: str = ""
    personality: str = "neutral"
    response_length: str = "balanced"
    #: IANA name the daily message allowance resets on, e.g. `Europe/London`.
    quota_timezone: str = "UTC"
    updated_at: datetime


class UpdateChatbotSettingsRequest(BaseModel):
    ai_chatbot_enabled: bool
    company_name: str | None = Field(default=None, max_length=200)
    company_description: str = Field(default="", max_length=MAX_COMPANY_DESCRIPTION_CHARS)
    industry: str = Field(default="", max_length=MAX_INDUSTRY_CHARS)
    allow_human_handoff: bool = True
    add_ai_summary_as_internal_comment: bool = False
    allow_ai_for_unassigned_conversations: bool = True
    daily_message_limit: int | None = Field(default=None, ge=0)
    share_visitor_location: bool = True
    conversation_retention_days: int = 30
    #: Bounded to match the column. Not validated against a zone list here --
    #: the IANA database changes, and the application checks against the
    #: zoneinfo actually installed, degrading an unknown name to UTC rather
    #: than refusing to answer.
    quota_timezone: str = Field(default="UTC", max_length=64)


class TenantPlanResponse(BaseModel):
    """A tenant's own plan, with usage beside each limit.

    Usage fields are `int | None`: `None` means the counter could not be read,
    deliberately distinct from `0`, which would claim nothing has been spent.
    The console renders `?` rather than a reassuring zero.
    """

    max_knowledge_bases: int | None
    max_chat_widgets: int | None
    max_messages_per_day: int | None
    max_tokens_per_month: int | None
    allow_invite_members: bool
    allow_create_roles: bool
    knowledge_bases_used: int
    chat_widgets_used: int
    messages_used_today: int | None
    tokens_used_this_month: int | None
    effective_daily_message_limit: int | None


class TeamResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    is_active: bool
    member_ids: list[UUID]


class TeamListResponse(BaseModel):
    teams: list[TeamResponse]


class SaveTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    member_ids: list[UUID] = Field(default_factory=list)


class ChatbotBehaviourRequest(BaseModel):
    """The chatbot's brief, as the Behaviour tab edits it. Tenant-wide."""

    role_instructions: str = ""
    avoid_instructions: str = ""
    personality: str = "neutral"
    response_length: str = "balanced"


class WidgetPresentationRequest(BaseModel):
    """Identity and reply experience, per embed."""

    chatbot_name: str | None = Field(default=None, max_length=100)
    chatbot_title: str | None = Field(default=None, max_length=100)
    #: An asset key from a fixed allowlist, never a URL. A URL here would let
    #: a tenant point every visitor's browser at an arbitrary third-party
    #: origin from their own customers' pages.
    avatar_key: str | None = Field(default=None, max_length=50)
    greeting: str | None = Field(default=None, max_length=300)
    show_quick_reply_suggestions: bool = True


class CreateDirectTextSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=MAX_DIRECT_TEXT_CHARS)


class HandoffRequest(BaseModel):
    """`team_id` omitted means "a human, team not yet chosen" -- the state a
    conversation sits in between the visitor asking and pressing a button."""

    team_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


class AgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    #: True writes a staff-only note. It does not take the conversation over,
    #: because leaving oneself a reminder is not answering the visitor.
    internal: bool = False


class UnassignedConversationResponse(BaseModel):
    id: UUID
    assigned_team_id: UUID | None
    handoff_reason: str | None
    handoff_at: datetime | None
    handoff_initiated_by: str | None
    title: str | None
    last_message_at: datetime | None


class UnassignedInboxResponse(BaseModel):
    conversations: list[UnassignedConversationResponse]


class PushPublicKeyResponse(BaseModel):
    """What the browser needs to subscribe, or an honest "not available".

    `enabled` is false when the deployment has no VAPID keypair. The console
    then does not offer to subscribe at all -- which is better than a button
    that always fails and looks like a browser problem.
    """

    enabled: bool
    public_key: str | None


class SubscribeToPushRequest(BaseModel):
    """A `PushSubscription` as the browser's own `toJSON()` produces it.

    **No `membership_id` field, deliberately.** It is resolved from the
    authenticated session; accepting one would let any agent register their
    browser against a colleague's membership and receive that colleague's
    queue notifications.
    """

    endpoint: str = Field(min_length=1, max_length=2000)
    p256dh_key: str = Field(min_length=1, max_length=400)
    auth_key: str = Field(min_length=1, max_length=400)


class UnsubscribeFromPushRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)


class ChatbotBehaviourResponse(BaseModel):
    role_instructions: str
    avoid_instructions: str
    personality: str
    response_length: str


class WidgetPresentationResponse(BaseModel):
    """Identity as the widget will actually render it.

    Defaults are resolved server-side rather than left null, so the console
    shows what a visitor sees instead of empty fields that read as
    unconfigured -- and so the default lives in one place.
    """

    widget_id: UUID
    chatbot_name: str
    chatbot_title: str
    avatar_key: str
    greeting: str | None
    show_quick_reply_suggestions: bool


class SetConversationAiModeRequest(BaseModel):
    """Whether the assistant may take this conversation back on its own.

    A single explicit boolean rather than a "disable" flag, so the request body
    reads the same way round as the switch an agent sees. The stored column is
    the negation; inverting once at the boundary beats every caller having to
    remember which way a double negative points.
    """

    ai_fallback_enabled: bool


class AgentTypingRequest(BaseModel):
    typing: bool
