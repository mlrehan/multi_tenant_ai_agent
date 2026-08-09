"""Unauthenticated operational endpoints -- no auth, no tenant resolution.

The three health endpoints answer genuinely different questions, and
conflating them is the classic deployment bug:

- ``/livez`` -- "is this process wedged?" Never touches a dependency. A
  liveness probe that fails during a database outage makes the orchestrator
  *restart every pod*, turning a recoverable blip into a full outage.
- ``/readyz`` -- "should this pod receive traffic right now?" Probes every
  dependency and returns 503 when any is down, so the load balancer routes
  away from it.
- ``/healthz`` -- a simple aggregate for humans and external uptime checks.

Until Phase 9, all three returned a hard-coded "ok" -- see the module
docstring of ``infrastructure/ops/health.py`` for why that was a real hazard.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from iam_platform.api.deps.authn import get_container
from iam_platform.api.deps.container import AppContainer

router = APIRouter(tags=["system"])


@router.get("/livez")
async def livez() -> dict[str, str]:
    """Liveness: the process is running and its event loop is responsive.

    Deliberately dependency-free -- see the module docstring.
    """
    return {"status": "alive"}


@router.get("/readyz")
async def readyz(
    response: Response, container: AppContainer = Depends(get_container)
) -> dict[str, object]:
    report = await container.health_check.check()
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if report.ready else "not_ready",
        "dependencies": [
            {
                "name": d.name,
                "healthy": d.healthy,
                **({"detail": d.detail} if d.detail else {}),
            }
            for d in report.dependencies
        ],
    }


@router.get("/healthz")
async def healthz(
    response: Response, container: AppContainer = Depends(get_container)
) -> dict[str, str]:
    report = await container.health_check.check()
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if report.ready else "degraded"}
