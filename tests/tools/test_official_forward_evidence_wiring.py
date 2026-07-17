from pathlib import Path
from types import SimpleNamespace

import tools.forward_track_advance as driver


def test_official_driver_uses_attested_api_and_no_boolean_bypass():
    source = Path("tools/forward_track_advance.py").read_text(encoding="utf-8")

    assert "load_forward_admission_ref" in source
    assert "admission_ref=" in source
    assert "quality_evidence_current=" not in source
    assert 'source="FORWARD_LIVE' not in source


def test_blocked_admission_never_records_or_realizes(tmp_path, monkeypatch):
    calls = {"record": 0, "realize": 0}

    class Ledger:
        def __init__(self, *args, **kwargs):
            pass

        def decisions(self, alpha_id):
            return []

        def record_decision(self, *args, **kwargs):
            calls["record"] += 1
            raise AssertionError("BLOCK must not record")

        def attested_realizations(self, alpha_id):
            return []

        def attested_pending(self, alpha_id):
            return []

        def legacy_decisions(self):
            return []

    class Store:
        def __init__(self, *args, **kwargs):
            pass

        def track(self, alpha_id):
            return {}

    now_ms = 1_700_000_000_000
    event_times = (now_ms - 14 * 86_400_000, now_ms - 7 * 86_400_000)
    panel = SimpleNamespace(
        instruments=("BTC",),
        event_times=event_times,
        matrices={"close": ((100.0,), (101.0,))},
    )
    funding_path = tmp_path / "funding.json"
    funding_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(driver, "_FUND", str(funding_path))
    monkeypatch.setattr(driver, "_STATUS", str(tmp_path / "status.json"))
    monkeypatch.setattr(driver, "ForwardLedger", Ledger)
    monkeypatch.setattr(driver, "PaperTrackStore", Store)
    monkeypatch.setattr(driver, "_ALPHAS", {"alpha": object()})
    monkeypatch.setattr(driver.ax, "evaluate", lambda expr, current: ((1.0,), (1.0,)))
    monkeypatch.setattr(driver, "build_panel", lambda: (panel, None))
    monkeypatch.setattr(driver, "load_mark_prices", lambda: {"BTC": {
        event_times[0]: 100.0,
        event_times[1]: 101.0,
    }})
    monkeypatch.setattr(driver, "rows_with_acceptable_coverage", lambda sig, rows: rows)
    monkeypatch.setattr(driver, "_weights_and_signals", lambda sig, insts: ({"BTC": 1.0}, {"BTC": 1.0}))
    monkeypatch.setattr(driver, "_reference_sharpe", lambda *args, **kwargs: (None, 0))
    monkeypatch.setattr(driver, "staleness_days", lambda current: {
        name: {"freshest_days": 0, "n_stale": 0, "n_total": 1}
        for name in driver.STALE_LIMITS
    })
    monkeypatch.setattr(driver, "run_refresh", lambda **kwargs: {
        "reports": [{"source": name} for name in ("prices", "funding", "oi_metrics", "perp_mark_prices")],
        "raw_lineage": {"status": "PASS", "datasets": {
            "funding": 1,
            "oi_metrics": 1,
            "perp_mark_prices": 1,
        }},
    })
    monkeypatch.setattr(driver, "authoritative_admission_status", lambda **kwargs: (
        None,
        {"status": "PASS", "verdict": "BLOCK"},
        ["data-quality admission BLOCK (reasons=1)"],
    ))
    monkeypatch.setattr(driver, "realize_due", lambda *args, **kwargs: calls.__setitem__("realize", calls["realize"] + 1))
    monkeypatch.setattr(driver, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver.time, "time", lambda: now_ms / 1000)
    monkeypatch.setattr(driver.sys, "argv", ["forward_track_advance"])

    driver._run()

    assert calls == {"record": 0, "realize": 0}
