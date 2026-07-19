from services.portfolio.crowding_calibration import (
    SIGNED_MAX_CROWDING_Z,
    summarize_calibration_records,
)


def _record(period, short_max_z):
    symbols = ("L1", "L2", "L3", "L4", "S1", "S2", "S3", "S4")
    weights = {symbol: (0.125 if symbol.startswith("L") else -0.125) for symbol in symbols}
    dry = {symbol: value for symbol, value in zip(symbols, (0.0, 0.1, 0.2, 0.3, 0.6, 0.7, 0.8, 1.0))}
    live = {symbol: value for symbol, value in zip(symbols, (0.1, 0.0, 0.3, 0.2, 0.7, 0.6, 1.0, 0.8))}
    return {
        "period": period,
        "weights": weights,
        "dry_crowding": dry,
        "live_crowding": live,
        "z_by_symbol": {"S1": short_max_z},
        "short_raw_gls": 3.0 + period / 10.0,
        "short_mean_z": short_max_z / 2.0,
        "short_max_z": short_max_z,
    }


def test_signed_threshold_summary_replays_the_real_gate_for_both_rank_modes():
    summary = summarize_calibration_records(
        [_record(1, 2.0), _record(2, 3.1), _record(3, 4.2)]
    )

    assert SIGNED_MAX_CROWDING_Z == 3.0
    assert summary["n_periods"] == 3
    assert summary["signed_max_crowding_z"] == 3.0
    assert summary["gate_at_signed_threshold"]["dry_run"] == {
        "n_pass": 0,
        "n_warn": 1,
        "n_block": 2,
        "block_rate": 0.666667,
        "block_periods": [2, 3],
    }
    assert summary["gate_at_signed_threshold"]["live"] == summary[
        "gate_at_signed_threshold"
    ]["dry_run"]
    assert summary["gate_at_signed_threshold"]["block_periods_equal"] is True


def test_z_threshold_sensitivity_is_monotonic_and_includes_signed_value():
    summary = summarize_calibration_records(
        [_record(1, 1.9), _record(2, 2.6), _record(3, 3.1), _record(4, 4.2)]
    )
    sensitivity = summary["z_threshold_sensitivity"]

    assert sensitivity["3.0"]["n_block"] == 2
    counts = [sensitivity[key]["n_block"] for key in ("2.0", "2.5", "3.0", "3.5", "4.0")]
    assert counts == sorted(counts, reverse=True)


def test_rank_statistics_remain_diagnostic_not_block_authority():
    summary = summarize_calibration_records([_record(1, 1.0), _record(2, 1.5)])

    assert summary["rank_statistics"]["dry_run_average"]["n"] == 2
    assert summary["rank_statistics"]["live_average"]["n"] == 2
    assert summary["max_name_weight"]["max"] == 0.125
    assert summary["provenance"] == "REAL_GATE_REPLAY_FROM_FROZEN_INPUTS"

