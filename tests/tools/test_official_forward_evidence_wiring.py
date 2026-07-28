import json
from pathlib import Path
from types import SimpleNamespace

import tools.forward_track_advance as driver
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef


def test_official_driver_uses_attested_api_and_no_boolean_bypass():
    source = Path("tools/forward_track_advance.py").read_text(encoding="utf-8")

    assert "load_forward_admission_ref" in source
    assert "admission_ref=" in source
    assert '"quality_evidence_" + "current"' not in source
    assert "quality_evidence_current=admission_ref" not in source
    assert "quality_evidence_current=refresh_evidence_available" not in source
    assert 'source="FORWARD_LIVE' not in source


def test_pass_authority_with_incomplete_refresh_never_records_or_realizes(tmp_path, monkeypatch):
    calls = {"record": 0, "realize": 0}

    class Ledger:
        def __init__(self, *args, **kwargs):
            pass

        def decisions(self, alpha_id):
            return []

        def attested_decisions(self, alpha_id):
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
            return None

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
    monkeypatch.setattr(driver, "_FORWARD_BASELINE", str(tmp_path / "baseline.json"))
    monkeypatch.setattr(driver, "build_forward_evidence", lambda *args, **kwargs: {
        "schema_version": 1,
        "observed_at_ms": now_ms,
    })
    monkeypatch.setattr(driver, "ForwardLedger", Ledger)
    monkeypatch.setattr(driver, "AttestedForwardLedger", Ledger)
    monkeypatch.setattr(driver, "PaperTrackStore", Store)
    monkeypatch.setattr(driver, "_ALPHAS", {"alpha": object()})
    monkeypatch.setattr(driver.ax, "evaluate", lambda expr, current: ((1.0,), (1.0,)))
    monkeypatch.setattr(driver, "build_panel", lambda **_kwargs: (panel, None))
    monkeypatch.setattr(driver, "load_mark_prices", lambda: {"BTC": {
        event_times[0]: 100.0,
        event_times[1]: 101.0,
    }})
    monkeypatch.setattr(driver, "rows_with_acceptable_coverage", lambda sig, rows: rows)
    monkeypatch.setattr(
        driver,
        "_weights_and_signals",
        lambda sig, insts, **_kwargs: ({"BTC": 1.0}, {"BTC": 1.0}),
    )
    monkeypatch.setattr(driver, "_reference_sharpe", lambda *args, **kwargs: (None, 0))
    monkeypatch.setattr(driver, "staleness_days", lambda current, **_kwargs: {
        name: {"freshest_days": 0, "n_stale": 0, "n_total": 1}
        for name in driver.STALE_LIMITS
    })
    monkeypatch.setattr(driver, "run_refresh", lambda **kwargs: {
        "reports": [{"source": name} for name in ("prices", "funding", "oi_metrics")],
        "raw_lineage": {"status": "PASS", "datasets": {
            "funding": 1,
            "oi_metrics": 1,
            "perp_mark_prices": 1,
        }},
    })
    monkeypatch.setattr(
        driver,
        "refresh_lineage_backfill",
        lambda **_kwargs: (True, {"status": "PASS"}, []),
    )
    reference = ForwardAdmissionRef(
        admission_id="data-admission-test",
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        recorded_at_ms=now_ms,
        price_basis="PERP_MARK_PRICE",
        instrument_scope=("BTC",),
    )
    monkeypatch.setattr(driver, "authoritative_admission_status", lambda **kwargs: (
        reference,
        {"status": "PASS", "verdict": "PASS"},
        [],
    ))
    monkeypatch.setattr(driver, "realize_due", lambda *args, **kwargs: calls.__setitem__("realize", calls["realize"] + 1))
    monkeypatch.setattr(driver, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver.time, "time", lambda: now_ms / 1000)
    monkeypatch.setattr(driver.sys, "argv", ["forward_track_advance"])

    driver._run()

    assert calls == {"record": 0, "realize": 0}
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["refresh_evidence_available"] is False
    assert status["alphas"]["alpha"]["evidence_state"] == "DATA_BLOCKED"


def test_complete_refresh_invokes_authority_and_binds_same_reference(tmp_path, monkeypatch):
    calls = {"authority": 0, "record": [], "realize": []}

    class Ledger:
        def __init__(self, *args, **kwargs):
            self._decisions = []

        def decisions(self, alpha_id):
            return list(self._decisions)

        def attested_decisions(self, alpha_id):
            return list(self._decisions)

        def record_decision(self, alpha_id, decided_ts, **kwargs):
            calls["record"].append(kwargs)
            self._decisions.append({"decided_ts": decided_ts})
            return True

        def attested_realizations(self, alpha_id):
            return []

        def attested_pending(self, alpha_id):
            return list(self._decisions)

        def legacy_decisions(self):
            return []

    class Store:
        def __init__(self, *args, **kwargs):
            pass

        def track(self, alpha_id):
            return None

    now_ms = 1_700_000_000_000
    event_times = (now_ms - 14 * 86_400_000, now_ms - 7 * 86_400_000)
    reference = ForwardAdmissionRef(
        admission_id="data-admission-test",
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        recorded_at_ms=now_ms,
        price_basis="PERP_MARK_PRICE",
        instrument_scope=("BTC",),
    )
    funding_path = tmp_path / "funding.json"
    funding_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(driver, "_FUND", str(funding_path))
    monkeypatch.setattr(driver, "_STATUS", str(tmp_path / "status.json"))
    monkeypatch.setattr(driver, "_FORWARD_BASELINE", str(tmp_path / "baseline.json"))
    monkeypatch.setattr(driver, "build_forward_evidence", lambda *args, **kwargs: {
        "schema_version": 1,
        "observed_at_ms": now_ms,
    })
    monkeypatch.setattr(driver, "ForwardLedger", Ledger)
    monkeypatch.setattr(driver, "AttestedForwardLedger", Ledger)
    monkeypatch.setattr(driver, "PaperTrackStore", Store)
    monkeypatch.setattr(driver, "_ALPHAS", {"alpha": object()})
    monkeypatch.setattr(driver.ax, "evaluate", lambda expr, current: ((1.0,), (1.0,)))
    monkeypatch.setattr(driver, "build_panel", lambda **_kwargs: (SimpleNamespace(
        instruments=("BTC",),
        event_times=event_times,
        matrices={"close": ((100.0,), (101.0,))},
    ), None))
    monkeypatch.setattr(driver, "load_mark_prices", lambda: {"BTC": {
        event_times[0]: 100.0,
        event_times[1]: 101.0,
    }})
    monkeypatch.setattr(driver, "rows_with_acceptable_coverage", lambda sig, rows: rows)
    monkeypatch.setattr(
        driver,
        "_weights_and_signals",
        lambda sig, insts, **_kwargs: ({"BTC": 1.0}, {"BTC": 1.0}),
    )
    monkeypatch.setattr(driver, "_reference_sharpe", lambda *args, **kwargs: (None, 0))
    monkeypatch.setattr(driver, "staleness_days", lambda current, **_kwargs: {
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
    monkeypatch.setattr(
        driver,
        "refresh_lineage_backfill",
        lambda **_kwargs: (True, {"status": "PASS"}, []),
    )

    def authority(**kwargs):
        calls["authority"] += 1
        return reference, {"status": "PASS", "verdict": "PASS"}, []

    def realize(*args, **kwargs):
        calls["realize"].append(kwargs)
        return {"alerts": [], "n_realized": 0, "n_quality_blocked": 0}

    monkeypatch.setattr(driver, "authoritative_admission_status", authority)
    monkeypatch.setattr(driver, "realize_due", realize)
    monkeypatch.setattr(driver, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver.time, "time", lambda: now_ms / 1000)
    monkeypatch.setattr(driver.sys, "argv", ["forward_track_advance"])

    driver._run()

    assert calls["authority"] == 1
    assert calls["record"][0]["admission_ref"] is reference
    assert calls["realize"][0]["admission_ref"] is reference
    assert calls["realize"][0]["committed_at_ms"] == now_ms
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert (
        status["alphas"]["alpha"]["evidence_state"]
        == "ATTESTED_PENDING_REALIZATION"
    )
    assert status["quality_evidence_current"] is True
    assert status["raw_lineage_backfill"]["status"] == "PASS"
