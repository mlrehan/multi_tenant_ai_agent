"""Request/response shapes for the public widget surface.

Note what these do **not** carry. No tenant id, no knowledge-base id, no
`top_k`, no model name: everything that decides *what* is read or *how much* is
spent comes from the widget row or the signed session, never from an anonymous
caller.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StartWidgetSessionRequest(BaseModel):
    public_key: str = Field(min_length=8, max_length=200)


class WidgetSessionResponse(BaseModel):
    session_token: str
    expires_at: datetime


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
