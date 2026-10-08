from services.data.refresh_history import RefreshHistoryStore
from tools.data_refresh_slo_evidence import build_evidence


_SOURCES = ("prices", "funding", "perp_mark_prices", "oi_metrics", "mexc_btc")


def test_slo_evidence_is_real_history_and_remains_blocked_without_minimum_window(tmp_path):
    path = str(tmp_path / "refresh.db")
    reports = [
        {
            "source": source,
            "outcome": "PASS",
            "duration_ms": 1,
            "watermark_after": 100,
            "failed": [],
            "failure_details": [],
            "failed_tickers": [],
            "quality_failed": [],
        }
        for source in _SOURCES
    ]
    stale = {
        source: {"freshest_days": 1, "worst_days": 1, "n_stale": 0, "n_total": 1}
        for source in _SOURCES
    }
    store = RefreshHistoryStore(path)
    entry = store.append(
        {
            "run_id": "refresh-1000",
            "ran_at": 1_000,
            "duration_ms": 10,
            "reports": reports,
            "staleness_before": stale,
            "staleness_after": stale,
            "failed_total": 0,
        },
        recorded_at_ms=1_100,
        run_id="refresh-1000",
    )
    store.close()

    evidence = build_evidence(history_path=path, generated_at_ms=1_200)
    assert evidence["provenance"] == "REAL_REFRESH_HISTORY"
    assert evidence["decision"] == "BLOCK"
    assert evidence["slo"]["verdict"] == "INSUFFICIENT_HISTORY"
    assert evidence["history"]["chain_valid"] is True
    assert evidence["history"]["head_hash"] == entry.entry_hash
    assert evidence["watermark_history"]["prices"][0]["watermark_after"] == 100
