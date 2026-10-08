from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from services.console.api.snapshot_cache import SingleFlightTtlCache


def test_system_completeness_prefers_release_bound_persisted_assessment(tmp_path, monkeypatch):
    import json

    from services.console.api import architecture_views

    assessment = {
        "schema_version": 1,
        "verdict": "NOT_TOP_TIER",
        "domains": [],
        "requirements": [{"red_line": False, "status": "PASS"}],
        "summary": {"total": 1},
    }
    path = tmp_path / "top_tier_assessment.json"
    path.write_text(json.dumps(assessment), encoding="utf-8")
    monkeypatch.setattr(architecture_views, "_TOP_TIER_ASSESSMENT", path)
    monkeypatch.setattr(
        architecture_views,
        "assess_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live rescan forbidden")),
    )

    payload = architecture_views._system_completeness_payload()

    assert payload["assessment"]["verdict"] == "NOT_TOP_TIER"
    assert payload["summary"] == {"total": 1}


def test_cache_reuses_until_ttl_and_rebuilds_after_expiry():
    now = [10.0]
    calls = 0

    def build() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"generation": calls}

    cache = SingleFlightTtlCache[dict[str, int]](5, clock=lambda: now[0])
    assert cache.get(build) == {"generation": 1}
    assert cache.get(build) == {"generation": 1}
    now[0] = 15.0
    assert cache.get(build) == {"generation": 2}
    assert calls == 2


def test_cache_coalesces_concurrent_builders():
    cache = SingleFlightTtlCache[int](30)
    entered = Event()
    release = Event()
    calls = 0
    guard = Lock()

    def build() -> int:
        nonlocal calls
        with guard:
            calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return 7

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(cache.get, build) for _ in range(4)]
        assert entered.wait(timeout=2)
        release.set()
        assert [future.result(timeout=2) for future in futures] == [7, 7, 7, 7]
    assert calls == 1


def test_cache_does_not_retain_builder_failure():
    cache = SingleFlightTtlCache[int](30)
    calls = 0

    def build() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("broken")
        return 9

    with pytest.raises(RuntimeError, match="broken"):
        cache.get(build)
    assert cache.get(build) == 9
    assert calls == 2
