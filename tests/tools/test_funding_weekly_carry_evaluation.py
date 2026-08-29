from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from quantos.contracts.hashing import stable_hash
from services.execution.backtest_authority import BacktestIntegrityBlocked
from services.research.funding_settlement import HOUR_MS
from tools import funding_weekly_carry_evaluation as driver


def _snapshot(periods: int = 90) -> dict:
    series = {}
    for column in range(10):
        instrument = f"S{column:02d}USDT"
        rows = []
        for index in range(periods + 1):
            settlement = index * 8 * HOUR_MS
            rows.append(
                {
                    "instrument_id": instrument,
                    "source_funding_time_ms": settlement,
                    "settlement_time_ms": settlement,
                    "decision_time_ms": settlement + HOUR_MS,
                    "settled_rate": (column - 4.5) * 0.00001,
                    "decision_mark_price": 100.0 + column,
                    "mark_kline_close_time_ms": settlement + HOUR_MS - 1,
                }
            )
        series[instrument] = rows
    return {
        "schema_version": "binance_funding_settlement_history.v1",
        "provenance": "REAL_BINANCE_PUBLIC_FUNDING_SETTLEMENT_HISTORY",
        "observed_at_ms": 1_000,
        "universe": sorted(series),
        "series": series,
        "dataset_hash": stable_hash(series),
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }


def test_public_evaluation_is_quarantined_before_reading_source() -> None:
    with pytest.raises(BacktestIntegrityBlocked, match="INVALIDATED_PERP_LEDGER_V1"):
        driver.run(source_commit="not-even-validated")


def test_batch_is_exactly_one_fixed_weekly_candidate(tmp_path) -> None:
    source = tmp_path / "settlements.json"
    source.write_text(json.dumps(_snapshot()), encoding="utf-8")
    history, _metadata = driver.load_history(
        source, min_rows_per_instrument=10
    )

    candidates, evidence, mapping = driver.build_candidate_batch(history)

    assert len(candidates) == 1
    definition, periods = candidates[0]
    assert mapping["alias"] == "weekly_carry"
    assert definition.factor_ids == (
        "settled_funding_rate_7d",
        "post_settlement_mark_price",
    )
    assert len(periods) == 2
    assert evidence == {definition.alpha_id: {"leakage_ok": True}}
    assert driver.EVALUATION_CONTRACT["parameter_scan"] is False
    assert driver.EVALUATION_CONTRACT["rebalance"] == "MONDAY_00_UTC"


def test_run_registers_one_trial_and_never_grants_testnet_authority(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "settlements.json"
    output = tmp_path / "evaluation.json"
    source.write_text(json.dumps(_snapshot()), encoding="utf-8")
    captured = {}

    class Registry:
        pass

    @contextmanager
    def registry_context():
        yield Registry()

    def run_batch(candidates, **kwargs):
        captured["count"] = len(candidates)
        captured["cost"] = kwargs["cost"]
        captured["quantile"] = kwargs["quantile"]
        captured["periods_per_year"] = kwargs["periods_per_year"]
        captured["search_space"] = kwargs["search_space"]
        return {"recorded_run_ids": [], "registered_candidate_count": len(candidates)}

    monkeypatch.setattr(driver, "production_experiment_registry", registry_context)
    monkeypatch.setattr(driver, "run_registered_candidate_batch", run_batch)

    result = driver._run_legacy_diagnostic(
        source_snapshot=source,
        output=output,
        source_commit="a" * 40,
        recorded_at=2_000,
        min_rows_per_instrument=10,
    )

    assert captured["count"] == 1
    assert captured["cost"].fee_bps == 4.0
    assert captured["cost"].slippage_bps == 2.0
    assert captured["quantile"] == 0.2
    assert captured["periods_per_year"] == 52
    assert "no parameter" in captured["search_space"]
    assert result["decision"]["formal_evaluation_pass"] is False
    assert result["decision"]["forward_admission_allowed"] is False
    assert result["decision"]["testnet_eligible"] is False
    assert result["safety"]["order_submission"] is False
