import json
import sqlite3

import pytest

from services.data.refresh_history import (
    RefreshHistoryIntegrityError,
    RefreshHistoryStore,
)


def _status(ran_at, reports, *, duration_ms=100):
    staleness = {
        report["source"]: {
            "freshest_days": 1,
            "worst_days": 1,
            "n_stale": 0,
            "n_total": 1,
        }
        for report in reports
    }
    return {
        "ran_at": ran_at,
        "duration_ms": duration_ms,
        "reports": reports,
        "staleness_before": staleness,
        "staleness_after": staleness,
        "failed_total": sum(len(report.get("failed", ())) for report in reports),
    }


def _report(source, *, outcome="PASS", watermark=100, failed_tickers=()):
    failed_tickers = list(failed_tickers)
    return {
        "source": source,
        "outcome": outcome,
        "duration_ms": 10,
        "watermark_after": watermark,
        "failed": ["failed"] if failed_tickers else [],
        "failure_details": [],
        "failed_tickers": failed_tickers,
        "quality_failed": [],
    }


def test_refresh_history_survives_restart_and_exposes_watermark_trend(tmp_path):
    path = str(tmp_path / "refresh.db")
    store = RefreshHistoryStore(path)
    first = store.append(
        _status(1_000, [_report("prices", watermark=100)]),
        recorded_at_ms=1_100,
    )
    second = store.append(
        _status(2_000, [_report("prices", watermark=200)]),
        recorded_at_ms=2_100,
    )
    assert first.prev_hash == ""
    assert second.prev_hash == first.entry_hash
    assert store.chain_valid()
    store.close()

    reopened = RefreshHistoryStore(path, create=False)
    assert reopened.count() == 2
    assert reopened.chain_valid()
    assert reopened.head_hash == second.entry_hash
    assert reopened.watermark_history("prices") == [
        {
            "ran_at_ms": 1_000,
            "outcome": "PASS",
            "watermark_after": 100,
            "freshest_days": 1,
            "n_stale": 0,
            "n_total": 1,
        },
        {
            "ran_at_ms": 2_000,
            "outcome": "PASS",
            "watermark_after": 200,
            "freshest_days": 1,
            "n_stale": 0,
            "n_total": 1,
        },
    ]


def test_refresh_history_sql_layer_rejects_update_and_delete(tmp_path):
    path = str(tmp_path / "refresh.db")
    store = RefreshHistoryStore(path)
    store.append(_status(1_000, [_report("prices")]), recorded_at_ms=1_100)
    external = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        external.execute("UPDATE refresh_history SET ran_at_ms=0 WHERE seq=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        external.execute("DELETE FROM refresh_source_history WHERE history_seq=1")
    external.close()


def test_refresh_history_detects_tamper_and_refuses_to_extend_bad_chain(tmp_path):
    path = str(tmp_path / "refresh.db")
    store = RefreshHistoryStore(path)
    store.append(_status(1_000, [_report("prices")]), recorded_at_ms=1_100)

    external = sqlite3.connect(path)
    external.execute("DROP TRIGGER refresh_history_no_update")
    payload = json.dumps({"ran_at": 1_000}, sort_keys=True, separators=(",", ":"))
    external.execute("UPDATE refresh_history SET status_json=? WHERE seq=1", (payload,))
    external.commit()
    external.close()

    assert not store.chain_valid()
    with pytest.raises(RefreshHistoryIntegrityError, match="hash-chain"):
        store.append(_status(2_000, [_report("prices")]), recorded_at_ms=2_100)


def test_refresh_history_detects_normalized_source_row_tamper(tmp_path):
    path = str(tmp_path / "refresh.db")
    store = RefreshHistoryStore(path)
    store.append(_status(1_000, [_report("prices")]), recorded_at_ms=1_100)

    external = sqlite3.connect(path)
    external.execute("DROP TRIGGER refresh_source_history_no_update")
    external.execute(
        "UPDATE refresh_source_history SET outcome='PASS', failed_count=0, watermark_after=999"
        " WHERE history_seq=1 AND source='prices'"
    )
    external.commit()
    external.close()

    assert not store.chain_valid()
    with pytest.raises(RefreshHistoryIntegrityError):
        store.append(_status(2_000, [_report("prices")]), recorded_at_ms=2_100)


def test_refresh_slo_requires_history_then_passes_clean_window(tmp_path):
    store = RefreshHistoryStore(str(tmp_path / "refresh.db"))
    store.append(_status(1_000, [_report("prices")]), recorded_at_ms=1_100)
    first = store.evaluate_slo(
        expected_sources=["prices"],
        stale_limits={"prices": 3},
        evaluated_at_ms=1_200,
        window_runs=2,
        minimum_runs=2,
    )
    assert first["verdict"] == "INSUFFICIENT_HISTORY"
    assert first["violations"] == []

    store.append(_status(2_000, [_report("prices", watermark=200)]), recorded_at_ms=2_100)
    second = store.evaluate_slo(
        expected_sources=["prices"],
        stale_limits={"prices": 3},
        evaluated_at_ms=2_200,
        window_runs=2,
        minimum_runs=2,
    )
    assert second["verdict"] == "PASS"
    assert second["sources"]["prices"]["success_rate"] == 1.0


def test_refresh_slo_detects_consecutive_failures_and_runtime_budget(tmp_path):
    store = RefreshHistoryStore(str(tmp_path / "refresh.db"))
    for index in range(3):
        store.append(
            _status(
                1_000 + index,
                [_report("funding", outcome="FAILED", failed_tickers=["BTC", "ETH"])],
            ),
            recorded_at_ms=2_000 + index,
        )
    slo = store.evaluate_slo(
        expected_sources=["funding"],
        stale_limits={"funding": 3},
        evaluated_at_ms=3_000,
        window_runs=3,
        minimum_runs=3,
        max_consecutive_failures=3,
    )
    assert slo["verdict"] == "BREACH"
    assert slo["sources"]["funding"]["consecutive_failures"] == 3
    assert {item["code"] for item in slo["violations"]} == {
        "CONSECUTIVE_FAILURES",
        "SUCCESS_RATE_BELOW_SLO",
    }

    budget_store = RefreshHistoryStore(str(tmp_path / "budget.db"))
    budget_store.append(
        _status(4_000, [_report("prices", outcome="BUDGET_EXCEEDED", failed_tickers=["BTC"])]),
        recorded_at_ms=4_100,
    )
    budget_slo = budget_store.evaluate_slo(
        expected_sources=["prices"],
        stale_limits={"prices": 3},
        evaluated_at_ms=4_200,
        window_runs=3,
        minimum_runs=3,
    )
    assert budget_slo["verdict"] == "BREACH"
    assert budget_slo["violations"][0]["code"] == "SOURCE_RUNTIME_FAILURE"


def test_refresh_slo_uses_instrument_coverage_not_binary_source_outcome(tmp_path):
    store = RefreshHistoryStore(str(tmp_path / "refresh.db"))
    for index in range(3):
        status = _status(
            1_000 + index,
            [_report("prices", outcome="FAILED", failed_tickers=["DELISTED"])],
        )
        status["staleness_before"]["prices"]["n_total"] = 115
        status["staleness_after"]["prices"]["n_total"] = 115
        store.append(status, recorded_at_ms=2_000 + index)

    slo = store.evaluate_slo(
        expected_sources=["prices"],
        stale_limits={"prices": 3},
        evaluated_at_ms=3_000,
        window_runs=3,
        minimum_runs=3,
        minimum_success_rate=0.99,
        minimum_instrument_coverage=0.99,
        max_consecutive_failures=3,
    )
    assert slo["verdict"] == "PASS"
    assert slo["sources"]["prices"]["success_rate"] == 1.0
    assert slo["sources"]["prices"]["instrument_coverage"] == pytest.approx(114 / 115)
    assert slo["sources"]["prices"]["consecutive_failures"] == 0
    assert slo["sources"]["prices"]["latest_outcome"] == "FAILED"
