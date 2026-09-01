from __future__ import annotations

import pytest

from services.console.api import system_views


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_probe"),
    [
        (
            ({"artifact_kind": "runtime"}, "REAL_ASHARE_TECHNICAL_RUNTIME", "RUNTIME_STATE"),
            "healthy",
            "RUNTIME_TECHNICAL_SEARCH_CANONICAL_TDX_BOUND",
        ),
        (
            ({"artifact_kind": "release"}, "REPOSITORY_EVIDENCE", "RELEASE_EVIDENCE"),
            "unknown",
            "REPOSITORY_EVIDENCE_DIAGNOSTIC_ONLY",
        ),
        (
            (None, "REPOSITORY_EVIDENCE", "RELEASE_EVIDENCE"),
            "unknown",
            "RUNTIME_TECHNICAL_SEARCH_NOT_PUBLISHED",
        ),
        (
            (None, "UNAVAILABLE", "RUNTIME_SOURCE_BINDING_INVALID"),
            "degraded",
            "RUNTIME_TECHNICAL_SEARCH_UNAVAILABLE:RUNTIME_SOURCE_BINDING_INVALID",
        ),
        (
            (None, "UNAVAILABLE", "RUNTIME_CANONICAL_SOURCE_UNAVAILABLE"),
            "degraded",
            "RUNTIME_TECHNICAL_SEARCH_UNAVAILABLE:RUNTIME_CANONICAL_SOURCE_UNAVAILABLE",
        ),
    ],
)
def test_ashare_system_health_requires_source_bound_runtime_evidence(
    monkeypatch,
    result,
    expected_status,
    expected_probe,
):
    monkeypatch.setattr(
        system_views._ashare_views,
        "_load_institutional_search",
        lambda: result,
    )

    service = system_views._ashare_search_runtime_service()

    assert service == {
        "service": "ashare-pit-alpha-search",
        "status": expected_status,
        "latency_ms": None,
        "error_rate": None,
        "probe": expected_probe,
    }


def test_ashare_system_health_fails_closed_when_availability_probe_raises(monkeypatch):
    def unavailable():
        raise OSError("canonical source cannot be read")

    monkeypatch.setattr(
        system_views._ashare_views,
        "_load_institutional_search",
        unavailable,
    )

    service = system_views._ashare_search_runtime_service()

    assert service["status"] == "degraded"
    assert service["probe"] == "RUNTIME_SEARCH_AVAILABILITY_ERROR:OSError"


def test_system_health_payload_uses_runtime_ashare_service_projection(monkeypatch):
    expected = {
        "service": "ashare-pit-alpha-search",
        "status": "unknown",
        "latency_ms": None,
        "error_rate": None,
        "probe": "REPOSITORY_EVIDENCE_DIAGNOSTIC_ONLY",
    }
    monkeypatch.setattr(
        system_views,
        "_ashare_search_runtime_service",
        lambda: expected,
    )
    monkeypatch.setattr(
        system_views,
        "_real_monitoring_snapshot",
        lambda: {"healthy": True},
    )
    monkeypatch.setattr(
        system_views,
        "_receipt_service",
        lambda **kwargs: {
            "service": kwargs["service"],
            "status": "unknown",
            "latency_ms": None,
            "error_rate": None,
            "probe": "TEST",
        },
    )

    payload = system_views._system_health_payload()
    services = {row["service"]: row for row in payload["services"]}

    assert services["ashare-pit-alpha-search"] == expected
