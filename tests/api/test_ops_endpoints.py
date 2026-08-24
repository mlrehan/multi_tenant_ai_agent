"""HTTP-level tests for the operational endpoints.

The unit tests in ``tests/unit/ops`` prove the probe logic; these prove the
*wiring* -- specifically that a failing probe becomes a 503 rather than a 200
with a sad-looking body, since orchestrators route on the status code and
would happily keep sending traffic to a pod that returns 200 {"status":
"not_ready"}.
"""

from __future__ import annotations

import httpx
import pytest

from iam_platform.application.ops.ports import DependencyStatus, HealthReport

pytestmark = pytest.mark.integration


class AlwaysFailingHealthCheck:
    async def check(self) -> HealthReport:
        return HealthReport(
            dependencies=[DependencyStatus("postgres_tenant", False, "unavailable")]
        )


class TestLiveness:
    async def test_livez_is_ok(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/livez")
        assert resp.status_code == 200
        assert resp.json() == {"status": "alive"}

    async def test_livez_stays_ok_when_dependencies_are_down(
        self, client: httpx.AsyncClient
    ) -> None:
        """The critical property: liveness must NOT track dependencies, or a
        database outage restarts every pod simultaneously."""
        client._transport.app.state.container.health_check = (  # type: ignore[attr-defined]
            AlwaysFailingHealthCheck()
        )

        resp = await client.get("/livez")
        assert resp.status_code == 200


class TestReadiness:
    async def test_readyz_reports_ready_against_the_real_dependencies(
        self, client: httpx.AsyncClient
    ) -> None:
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert {d["name"] for d in body["dependencies"]} == {
            "postgres_tenant",
            "postgres_platform",
            "redis",
        }
        assert all(d["healthy"] for d in body["dependencies"])

    async def test_readyz_returns_503_when_a_dependency_is_down(
        self, client: httpx.AsyncClient
    ) -> None:
        client._transport.app.state.container.health_check = (  # type: ignore[attr-defined]
            AlwaysFailingHealthCheck()
        )

        resp = await client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"

    async def test_healthz_returns_503_when_degraded(self, client: httpx.AsyncClient) -> None:
        client._transport.app.state.container.health_check = (  # type: ignore[attr-defined]
            AlwaysFailingHealthCheck()
        )

        resp = await client.get("/healthz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"


class TestMetrics:
    async def test_metrics_exposes_prometheus_format(self, client: httpx.AsyncClient) -> None:
        await client.get("/livez")  # generate at least one observation

        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "http_requests_total" in resp.text
        assert "http_request_duration_seconds_bucket" in resp.text

    async def test_metrics_labels_by_route_template_not_raw_path(
        self, client: httpx.AsyncClient
    ) -> None:
        """Label cardinality guard: labelling by raw path would mint a series
        per tenant UUID and eventually take down the metrics backend."""
        await client.get("/v1/tenants/11111111-1111-1111-1111-111111111111/knowledge-bases")
        await client.get("/v1/tenants/22222222-2222-2222-2222-222222222222/knowledge-bases")

        resp = await client.get("/metrics")
        assert "11111111-1111-1111-1111-111111111111" not in resp.text
        assert "22222222-2222-2222-2222-222222222222" not in resp.text
        assert "{tenant_id}" in resp.text

    async def test_unmatched_paths_collapse_to_a_single_series(
        self, client: httpx.AsyncClient
    ) -> None:
        """A URL scanner must not be able to create unbounded time series."""
        await client.get("/nope-one")
        await client.get("/nope-two")

        resp = await client.get("/metrics")
        assert 'route="unmatched"' in resp.text
        assert "nope-one" not in resp.text
        assert "nope-two" not in resp.text
