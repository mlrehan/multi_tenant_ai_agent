"""Request-scoped observability context.

Per docs/20-dependency-rules.md: this contextvar carries correlation/tracing
metadata ONLY. Authorization-relevant state (current user, tenant, effective
permissions) is never stored here -- it is passed as explicit function
arguments into application-layer use cases.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: UUID
    correlation_id: UUID
    ip: str | None = None
    user_agent: str | None = None


_current: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def new_context(
    *, correlation_id: UUID | None = None, ip: str | None = None, user_agent: str | None = None
) -> RequestContext:
    return RequestContext(
        request_id=uuid4(),
        correlation_id=correlation_id or uuid4(),
        ip=ip,
        user_agent=user_agent,
    )


def bind(ctx: RequestContext) -> Token[RequestContext | None]:
    return _current.set(ctx)


def reset(token: Token[RequestContext | None]) -> None:
    _current.reset(token)


def current() -> RequestContext | None:
    return _current.get()


@contextmanager
def bound(ctx: RequestContext) -> Iterator[RequestContext]:
    token = bind(ctx)
    try:
        yield ctx
    finally:
        reset(token)
