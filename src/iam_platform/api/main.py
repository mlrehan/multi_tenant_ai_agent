"""FastAPI app factory -- docs/19-folder-structure.md.

Takes an already-built ``AppContainer`` rather than constructing one
itself, so this module only ever imports ``application``/``core`` types and
stays clean against the "API does not import infrastructure directly"
import-linter contract. The container is built by ``iam_platform.bootstrap``
(the actual composition root).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from iam_platform.api.deps.container import AppContainer
from iam_platform.api.exception_handlers import register_exception_handlers
from iam_platform.api.middleware.correlation_id import CorrelationIdMiddleware
from iam_platform.api.middleware.metrics import (
    MetricsCollector,
    MetricsMiddleware,
    build_metrics_endpoint,
)
from iam_platform.api.middleware.public_cors import PublicChatCorsMiddleware
from iam_platform.api.middleware.rate_limit import RateLimitMiddleware
from iam_platform.api.middleware.security_headers import SecurityHeadersMiddleware
from iam_platform.api.v1.assistants.router import router as assistants_router
from iam_platform.api.v1.auth.router import router as auth_router
from iam_platform.api.v1.impersonation.router import router as impersonation_router
from iam_platform.api.v1.memberships.router import router as memberships_router
from iam_platform.api.v1.platform.router import router as platform_router
from iam_platform.api.v1.public_chat.router import router as public_chat_router
from iam_platform.api.v1.rbac.router import router as rbac_router
from iam_platform.api.v1.system.router import router as system_router
from iam_platform.api.v1.tenants.router import router as tenants_router


def create_app(container: AppContainer) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Startup work already happened in `build_container` (which is async
        # precisely so secret resolution can do I/O). What lifespan adds is the
        # *shutdown* half: releasing DB pools, Redis, and the HTTP client so a
        # terminating pod doesn't hold connections a rolling deploy needs.
        yield
        await container.shutdown()

    app = FastAPI(title="IAM Platform API", version="1", lifespan=lifespan)
    app.state.container = container

    # Starlette applies middleware in reverse registration order, so the last
    # one added is the outermost. Correlation ID is registered last on purpose:
    # it must wrap everything, so a request rejected by the rate limiter still
    # gets an ID in its response and its log line.
    app.add_middleware(CORSMiddleware, allow_origins=container.settings.cors_allowed_origins)
    # Added *after* CORSMiddleware so it is the outer of the two and sees
    # preflights first. The global policy is a fixed deploy-time origin list
    # (the console); the widget surface's allowlist is per widget and lives in
    # the database, so one middleware cannot serve both. See public_cors.py.
    app.add_middleware(PublicChatCorsMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        rate_limiter=container.rate_limiter,
        limit=container.settings.rate_limit.requests_per_window,
        window_seconds=container.settings.rate_limit.window_seconds,
    )
    metrics_collector = MetricsCollector()
    app.add_middleware(MetricsMiddleware, collector=metrics_collector)
    app.add_middleware(CorrelationIdMiddleware)

    register_exception_handlers(app)

    # Unauthenticated by design -- scrape access is controlled at the network
    # layer (see docs/22-deployment-and-operations.md), not with a bearer
    # token, because scrapers don't hold one.
    app.add_api_route(
        "/metrics", build_metrics_endpoint(metrics_collector), methods=["GET"], tags=["system"]
    )

    app.include_router(auth_router)
    app.include_router(platform_router)
    app.include_router(tenants_router)
    app.include_router(memberships_router)
    app.include_router(rbac_router)
    app.include_router(assistants_router)
    app.include_router(impersonation_router)
    # Last, and visibly separate: this is the unauthenticated surface. It
    # deliberately does not sit under the tenant-scoped prefix, so no future
    # change to that tree can accidentally make it require a login it cannot
    # have -- or, worse, make it look protected while it is not.
    app.include_router(public_chat_router)
    app.include_router(system_router)

    return app
