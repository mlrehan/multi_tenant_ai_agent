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


async def _serve(host: str, port: int) -> None:
    settings = Settings()
    configure_logging(settings.log_level)

    app = await build_app()

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        # The app's JSON formatter is already installed on the root logger;
        # uvicorn's default dictConfig would replace those handlers and lose
        # both the structured output and the correlation-id enrichment.
        log_config=None,
        # Behind a TLS-terminating load balancer. `forwarded_allow_ips` must be
        # narrowed to the actual proxy CIDR in a real deployment (see
        # docs/22-deployment-and-operations.md) -- trusting "*" lets a client
        # spoof X-Forwarded-For and with it their rate-limit bucket.
        proxy_headers=True,
        forwarded_allow_ips="*",
        server_header=False,
        # Let in-flight requests finish before the container dies. Must be
        # shorter than the orchestrator's termination grace period, or the
        # shutdown is a hard kill regardless of what this says.
        timeout_graceful_shutdown=20,
    )
    await uvicorn.Server(config).serve()


def main(host: str = "0.0.0.0", port: int = 8000) -> None:  # noqa: S104
    asyncio.run(_serve(host, port))


if __name__ == "__main__":
    main()
