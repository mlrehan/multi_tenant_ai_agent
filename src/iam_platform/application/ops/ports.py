"""Health-check port -- the contract behind ``/readyz``.

Kept in ``application`` (not ``api``) so the concrete probes live in
``infrastructure`` and the router only ever sees this Protocol, same as every
other dependency (docs/20-dependency-rules.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    name: str
    healthy: bool
    #: Populated only on failure, and deliberately generic -- ``/readyz`` is
    #: typically unauthenticated, so a driver error string (which can carry
    #: hostnames, usernames, or query fragments) must never reach it.
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class HealthReport:
    dependencies: list[DependencyStatus]

    @property
    def ready(self) -> bool:
        return all(d.healthy for d in self.dependencies)


class HealthCheck(Protocol):
    async def check(self) -> HealthReport:
        """Probes every dependency the service cannot serve traffic without.

        Must not raise -- a probe failure is a *result*, not an exception, or
        the readiness endpoint itself becomes a 500 and orchestrators lose the
        distinction between "not ready" and "crashed".
        """
        ...
