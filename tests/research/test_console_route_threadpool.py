"""Regression coverage for console snapshot event-loop isolation."""

from __future__ import annotations

import asyncio
import inspect
import threading

from httpx import ASGITransport, AsyncClient

from services.console.main import app


def test_synchronous_snapshot_routes_are_dispatched_by_fastapi_threadpool() -> None:
    """Heavy sync readers must be ``def``; async-with-sync-IO blocks /health."""

    from services.console.api import (
        alpha_family_views,
        alpha_library_views,
        artifact_paper_views,
        data_catalog,
        go_live_views,
        mexc_views,
        primary_views,
        registry_snapshots,
        research_run_views,
    )

    handlers = (
        primary_views.overview,
        primary_views.alpha_factory,
        primary_views.risk_dashboard,
        primary_views.validation_dashboard,
        primary_views.paper_trading,
        primary_views.portfolio_dashboard,
        data_catalog.data_catalog,
        alpha_family_views.alpha_families,
        alpha_library_views.population_optimizer,
        alpha_library_views.dsl_explorer,
        alpha_library_views.alpha_graph,
        alpha_library_views.crypto_live,
        registry_snapshots.experiments,
        research_run_views.research_runs,
        research_run_views.research_run_detail,
        research_run_views.backtest_results,
        artifact_paper_views.dry_run,
        artifact_paper_views.gonogo,
        go_live_views.go_live_readiness,
        mexc_views.mexc_market,
        mexc_views.mexc_strategy,
        mexc_views.mexc_readiness,
    )
    assert all(not inspect.iscoroutinefunction(handler) for handler in handlers)


def test_health_remains_responsive_while_synchronous_snapshot_is_blocked(monkeypatch) -> None:
    """One slow snapshot must occupy a worker thread, never the ASGI event loop."""

    from services.console.api import primary_views

    started = threading.Event()
    release = threading.Event()

    def slow_overview_payload() -> dict:
        started.set()
        if not release.wait(timeout=3):
            raise RuntimeError("test snapshot was not released")
        return {
            "source": "BACKEND_COMPUTED_SNAPSHOT",
            "read_only": True,
            "api_version": "0.1.0",
            "data_provenance": "REPOSITORY_EVIDENCE",
        }

    monkeypatch.setattr(primary_views, "_overview_payload", slow_overview_payload)

    async def scenario() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            blocked = asyncio.create_task(client.get("/api/v1/console/overview"))
            try:
                observed = await asyncio.to_thread(started.wait, 1.0)
                assert observed is True
                health = await asyncio.wait_for(client.get("/health"), timeout=0.5)
                assert health.status_code == 200
                assert health.json()["status"] == "healthy"
            finally:
                release.set()
            assert (await blocked).status_code == 200

    asyncio.run(scenario())
