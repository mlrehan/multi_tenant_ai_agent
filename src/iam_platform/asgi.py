"""Production entrypoint -- ``python -m iam_platform.asgi``.

**Why this isn't the usual module-level ``app = build_app()``.** ``build_app``
became async in Phase 9 so it can resolve ``secret://`` references before
wiring anything. Building at import time would mean ``asyncio.run()`` at
module scope, which creates *and closes* an event loop -- and asyncpg/redis
connection pools bind to the loop they were created on, so anything
constructed eagerly there would be attached to a dead loop (the same failure
the integration-test fixtures hit in Phase 5).

Running uvicorn programmatically builds the container inside the very loop
that serves requests, and gives one place to configure logging before
anything else emits a line.
"""

from __future__ import annotations

import asyncio

import uvicorn

from iam_platform.bootstrap import build_app
from iam_platform.core.config import Settings
from iam_platform.core.logging import configure_logging


def server_config(app: object, settings: Settings, *, host: str, port: int) -> uvicorn.Config:
    """How uvicorn serves the app. Separate so a test can inspect it."""
    return uvicorn.Config(
        app,  # type: ignore[arg-type]
        host=host,
        port=port,
        # The app's JSON formatter is already installed on the root logger;
        # uvicorn's default dictConfig would replace those handlers and lose
        # both the structured output and the correlation-id enrichment.
        log_config=None,
        # The client's IP comes from `X-Forwarded-For`, but only when the
        # peer sending it is trusted -- see `Settings.forwarded_allow_ips`.
        # This used to be "*", which let any caller choose its own rate-limit
        # bucket by sending the header.
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        server_header=False,
        # Let in-flight requests finish before the container dies. Must be
        # shorter than the orchestrator's termination grace period, or the
        # shutdown is a hard kill regardless of what this says.
        timeout_graceful_shutdown=20,
    )


async def _serve(host: str, port: int) -> None:
    settings = Settings()
    configure_logging(settings.log_level)

    app = await build_app()
    await uvicorn.Server(server_config(app, settings, host=host, port=port)).serve()


def main(host: str = "0.0.0.0", port: int = 8000) -> None:  # noqa: S104
    asyncio.run(_serve(host, port))


if __name__ == "__main__":
    main()
