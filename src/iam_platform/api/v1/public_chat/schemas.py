"""Request/response shapes for the public widget surface.

Note what these do **not** carry. No tenant id, no knowledge-base id, no
`top_k`, no model name: everything that decides *what* is read or *how much* is
spent comes from the widget row or the signed session, never from an anonymous
caller.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class StartWidgetSessionRequest(BaseModel):
    public_key: str = Field(min_length=8, max_length=200)
    #: A previously-issued session token, so a returning visitor keeps the
    #: conversation they already had. It is a *token*, not a session id, on
    #: purpose: a bare id in a request body would let any caller name a
    #: stranger's session and read their thread, whereas this has to have been
    #: signed by us and handed to this browser. Expiry is not required -- see
    #: `WidgetTokenService.read_resumable` -- because closing the tab overnight
    #: is exactly the case persistent history is for.
    resume_token: str | None = Field(default=None, max_length=4000)


class WidgetSessionResponse(BaseModel):
    session_token: str
    expires_at: datetime

    #: How the tenant configured this widget to introduce itself. Returned
    #: here rather than left to `data-` attributes on the script tag, because
    #: the console's Identity tab writes these to the database and a setting
    #: the widget never reads is a setting that does not exist.
    #:
    #: Safe on an anonymous surface: a name, a title, an avatar *key* from a
    #: fixed allowlist, and a greeting are all things the widget displays to
    #: everyone who opens it anyway.
    chatbot_name: str
    chatbot_title: str
    avatar_key: str
    greeting: str | None
    show_quick_reply_suggestions: bool

    #: Already resolved against the tenant's handoff policy -- empty when the
    #: widget has suggestions switched off. The widget renders this list and
    #: makes no decision of its own, so it cannot show a pill the console's
    #: preview hides.
    quick_replies: list[str]


class AskWidgetRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class PublicCitation(BaseModel):
    """What a visitor is told about a source.

    A label and a human-meaningful location -- a page number, a URL. No chunk
    id, no document id: those are internal identifiers, and publishing them
    would expose the shape of a tenant's corpus to the open internet for no
    reader benefit.
    """

    label: str
    source: str | None


class SelectTeamRequest(BaseModel):
    """Which team the visitor picked.

    A team *id*, not a name: the server matches it against the tenant's own
    rows, so a transfer cannot be aimed by typing a team name into the chat.
    """

    team_id: UUID
    #: What the visitor said when they asked, carried through so the agent
    #: picking the conversation up sees why it was escalated.
    reason: str | None = Field(default=None, max_length=500)


class VisitorMessageRequest(BaseModel):
    """A visitor's reply to a human colleague, once the AI has stepped aside."""

    content: str = Field(min_length=1, max_length=2000)


class VisitorTypingRequest(BaseModel):
    """`false` is sent explicitly, not left to the key expiring: an indicator
    still showing under a message that has already arrived reads as a second
    message coming that never does."""

    typing: bool
