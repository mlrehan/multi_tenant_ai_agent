"""Readiness semantics -- the Phase 9 gap where ``/readyz`` returned a
hard-coded "ready" and so could never pull a broken pod out of rotation.

These exercise ``DependencyHealthCheck`` against fake engines/clients rather
than real ones: the interesting cases are *failure* cases, and reliably
breaking a real Postgres mid-test is far harder than asserting the probe
handles a raising client correctly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from iam_platform.application.ops.ports import DependencyStatus, HealthReport
from iam_platform.infrastructure.ops.health import DependencyHealthCheck


class _FakeConn:
    def __init__(self, *, error: Exception | None = None, delay: float = 0.0) -> None:
        self._error = error
        self._delay = delay

    async def execute(self, *_: Any) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeEngine:
    def __init__(self, *, error: Exception | None = None, delay: float = 0.0) -> None:
        self._error = error
        self._delay = delay

    def connect(self) -> _FakeConn:
        return _FakeConn(error=self._error, delay=self._delay)


class FakeRedis:
    def __init__(self, *, error: Exception | None = None, delay: float = 0.0) -> None:
        self._error = error
        self._delay = delay

    async def ping(self) -> bool:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return True


def _check(**kwargs: Any) -> DependencyHealthCheck:
    return DependencyHealthCheck(
        engine=kwargs.get("engine", FakeEngine()),
        platform_engine=kwargs.get("platform_engine", FakeEngine()),
        redis=kwargs.get("redis", FakeRedis()),
    )


class TestHealthReport:
    def test_ready_only_when_every_dependency_is_healthy(self) -> None:
        assert HealthReport(dependencies=[DependencyStatus("a", True)]).ready
        assert not HealthReport(
            dependencies=[DependencyStatus("a", True), DependencyStatus("b", False)]
        ).ready

    def test_empty_report_is_vacuously_ready(self) -> None:
        # Documents the edge case rather than leaving it to chance -- a check
        # with no dependencies configured shouldn't report "not ready".
        assert HealthReport(dependencies=[]).ready


class TestDependencyHealthCheck:
    async def test_all_healthy_reports_ready(self) -> None:
        report = await _check().check()
        assert report.ready
        assert {d.name for d in report.dependencies} == {
            "postgres_tenant",
            "postgres_platform",
            "redis",
        }

    async def test_tenant_database_down_makes_the_pod_not_ready(self) -> None:
        report = await _check(engine=FakeEngine(error=OSError("connection refused"))).check()

        assert not report.ready
        failed = next(d for d in report.dependencies if d.name == "postgres_tenant")
        assert not failed.healthy
        # Others are still reported healthy -- the report says *what* is broken.
        assert next(d for d in report.dependencies if d.name == "redis").healthy

    async def test_platform_database_can_fail_independently(self) -> None:
        """The two roles use different credentials, so a pod that can serve
        tenant traffic but not platform traffic is still degraded."""
        report = await _check(
            platform_engine=FakeEngine(error=OSError("password authentication failed"))
        ).check()

        assert not report.ready
        assert not next(d for d in report.dependencies if d.name == "postgres_platform").healthy
        assert next(d for d in report.dependencies if d.name == "postgres_tenant").healthy

    async def test_redis_down_makes_the_pod_not_ready(self) -> None:
        report = await _check(redis=FakeRedis(error=OSError("connection refused"))).check()

        assert not report.ready
        assert not next(d for d in report.dependencies if d.name == "redis").healthy

    async def test_probe_never_raises_even_when_a_dependency_explodes(self) -> None:
        """A probe failure must be a *result*, not an exception -- otherwise
        /readyz returns 500 and the orchestrator can't distinguish
        "not ready" from "crashed"."""
        report = await _check(
            engine=FakeEngine(error=RuntimeError("something unexpected")),
            redis=FakeRedis(error=ValueError("also unexpected")),
        ).check()

        assert not report.ready  # reported, not raised

    async def test_slow_dependency_times_out_rather_than_hanging(self) -> None:
        """Readiness is polled every few seconds; a hung probe would pile up
        and the pod would never be marked unhealthy."""
        report = await _check(redis=FakeRedis(delay=10.0)).check()

        redis_status = next(d for d in report.dependencies if d.name == "redis")
        assert not redis_status.healthy
        assert redis_status.detail == "timeout"

    async def test_failure_detail_never_leaks_driver_internals(self) -> None:
        """/readyz is typically unauthenticated, so a driver error string
        (hostnames, usernames, query fragments) must not reach the response."""
        secret_ish = "host=db.internal user=app_tenant password=hunter2"
        report = await _check(engine=FakeEngine(error=OSError(secret_ish))).check()

        for dependency in report.dependencies:
            assert dependency.detail in (None, "unavailable", "timeout")
            assert "hunter2" not in (dependency.detail or "")
