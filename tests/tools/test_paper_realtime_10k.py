from __future__ import annotations

import pytest

import tools.paper_realtime_10k as profile


def test_profile_delegates_to_paper_driver_with_fixed_isolated_paths(monkeypatch):
    captured = {}

    def fake_main(argv):
        captured["argv"] = tuple(argv)
        return 0

    monkeypatch.setattr(profile.paper_realtime, "main", fake_main)

    assert profile.main() == 0
    assert captured["argv"] == profile.PROFILE_ARGS
    assert "--health-check" in captured["argv"]
    assert "data/cache/paper_order_ledger_10k.db" in captured["argv"]
    assert "data/cache/paper_realtime_10k.db" in captured["argv"]
    assert "data/cache/paper_health_10k.json" in captured["argv"]


def test_profile_rejects_arbitrary_worker_arguments():
    with pytest.raises(ValueError, match="does not accept"):
        profile.main(("--aum", "1"))
