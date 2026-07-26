from __future__ import annotations

from pytest import approx

from services.research import alpha_expression as ax
from tools import forward_track_advance
from tools.binance_usdc_alpha_retest import (
    ALPHA_IDS,
    CARRY,
    CROWD_CONTRA,
    DAY_MS,
    EXPRESSIONS,
    QUANTILE,
    STRATEGY_SPECS,
    TOP_CONTRA,
    _build_weights,
    _cadence_rows,
    _persist_continuous_rebalances,
    _quantize_target,
    _stats,
    _weekly_rows,
    fetch_exchange_info,
)
from tools.continuous_paper_ledger import ContinuousPaperLedger, ContinuousPaperLedgerError


def _filter(step: float = 0.001) -> dict[str, dict[str, float]]:
    return {
        "BTC": {
            "step_size": step,
            "min_qty": step,
            "max_qty": 1000.0,
            "min_notional": 50.0,
        }
    }


def test_usdc_quantity_uses_market_step_and_min_notional() -> None:
    targets, omitted = _quantize_target(
        {"BTC": 0.123456}, {"BTC": 1000.0}, _filter(), 1_000_000.0
    )
    assert omitted == []
    assert targets["BTC"] == approx(123.456)


def test_usdc_zero_combo_target_is_flat_not_min_qty_rejection() -> None:
    targets, omitted = _quantize_target(
        {"BTC": 0.0}, {"BTC": 1000.0}, _filter(), 1_000_000.0
    )
    assert targets == {}
    assert omitted == []


def test_stats_is_deterministic_and_reports_drawdown() -> None:
    result = _stats([100.0, 105.0, 95.0, 110.0], ppy=365)
    assert result["total_return"] == approx(0.1)
    assert result["max_drawdown"] == approx(1.0 - 95.0 / 105.0)
    assert result["sharpe"] is not None


def test_exchange_info_filters_the_requested_quote_asset() -> None:
    def fake_get(_url: str) -> dict:
        def row(symbol: str, base: str, quote: str) -> dict:
            return {
                "symbol": symbol,
                "baseAsset": base,
                "quoteAsset": quote,
                "marginAsset": quote,
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "filters": [
                    {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        return {"symbols": [
            row("BTCUSDT", "BTC", "USDT"),
            row("ETHUSDT", "ETH", "USDT"),
            row("SOLUSDT", "SOL", "USDT"),
            row("BNBUSDT", "BNB", "USDT"),
            row("BTCUSDC", "BTC", "USDC"),
        ]}

    result = fetch_exchange_info("USDT", http_get=fake_get)
    assert set(result) == {"BTC", "ETH", "SOL", "BNB"}
    assert all(row["quote_asset"] == "USDT" for row in result.values())


def test_continuous_paper_ledger_is_append_only_and_idempotent(tmp_path) -> None:
    ledger = ContinuousPaperLedger(str(tmp_path / "paper.db"))
    payload = {"target_quantities": {"BTC": 0.25}, "trade_count": 1}
    assert ledger.append("crowd_contra", 123, payload, status="DIAGNOSTIC")
    assert not ledger.append("crowd_contra", 123, payload, status="DIAGNOSTIC")
    try:
        ledger.append("crowd_contra", 123, {"target_quantities": {"BTC": 0.5}}, status="DIAGNOSTIC")
    except ContinuousPaperLedgerError:
        pass
    else:
        raise AssertionError("a changed historical rebalance must be rejected")
    rows = ledger.rows("crowd_contra")
    ledger.close()
    assert len(rows) == 1
    assert rows[0]["prev_record_hash"] == ""


def test_retest_can_use_an_explicit_versioned_ledger(tmp_path) -> None:
    ledger_path = tmp_path / "paper-24m.db"
    simulations = {
        "crowd_contra": {
            "rebalances": [{"day": 123, "target_quantities": {"BTC": 0.25}}],
        },
    }
    result = _persist_continuous_rebalances(
        "USDT",
        "data/cache/paper-24m.json",
        simulations,
        {"allowed": False},
        ledger_path=str(ledger_path),
    )
    assert result["ledger"] == str(ledger_path)
    assert result["rebalances"] == {"crowd_contra": {"inserted": 1, "replayed": 0}}

    replay = _persist_continuous_rebalances(
        "USDT",
        "data/cache/paper-24m.json",
        simulations,
        {"allowed": False},
        ledger_path=str(ledger_path),
    )
    assert replay["rebalances"] == {"crowd_contra": {"inserted": 0, "replayed": 1}}


# --- Shared forward-track strategy registry (2026-07-25) ----------------------

def test_strategy_registry_is_forward_track_registry_normalized() -> None:
    """The continuous paper measures EXACTLY the forward registry's strategies."""
    assert set(STRATEGY_SPECS) == set(forward_track_advance._ALPHAS)
    for alpha_id, entry in forward_track_advance._ALPHAS.items():
        assert STRATEGY_SPECS[alpha_id] == forward_track_advance._alpha_spec(entry)


def test_original_alphas_keep_frozen_expr_quantile_cadence() -> None:
    frozen = {
        "carry_oi_majors": CARRY,
        "crowd_contra": CROWD_CONTRA,
        "top_contra": TOP_CONTRA,
    }
    assert set(frozen) == set(ALPHA_IDS) == set(EXPRESSIONS)
    for alpha_id, expr in frozen.items():
        assert EXPRESSIONS[alpha_id] is expr or EXPRESSIONS[alpha_id] == expr
        spec = STRATEGY_SPECS[alpha_id]
        assert spec["expr"] == expr
        assert spec["quantile"] == approx(QUANTILE)
        assert spec["cadence_days"] == 7


def test_variant_family_specs_match_pre_registered_candidates() -> None:
    """Pin the 2026-07-24 pre-registered CANDIDATE specs (quantile x cadence)."""
    expected = {
        "crowd_contra_q20": (0.20, 7),
        "crowd_taker_combo": (0.30, 7),
        "crowd_contra_14d": (0.30, 14),
        "crowd_contra_q20_14d": (0.20, 14),
        "crowd_contra_3d": (0.30, 3),
        "crowd_contra_q25_3d": (0.25, 3),
        "crowd_contra_q35_3d": (0.35, 3),
    }
    assert set(expected) <= set(STRATEGY_SPECS)
    for alpha_id, (quantile, cadence_days) in expected.items():
        spec = STRATEGY_SPECS[alpha_id]
        assert spec["quantile"] == approx(quantile)
        assert spec["cadence_days"] == cadence_days
    # The taker combo expression is the pre-registered two-field composite.
    assert ax.serialize(STRATEGY_SPECS["crowd_taker_combo"]["expr"]) == (
        "(div (add (cs_zscore (neg (field global_ls))) "
        "(cs_zscore (neg (field taker_ls)))) (const 2.0))"
    )


def test_cadence_rows_weekly_is_byte_identical_and_variant_cadences_hold() -> None:
    base = 1_700_000_000_000 - (1_700_000_000_000 % DAY_MS)
    days = [base + index * DAY_MS for index in range(120)]

    def legacy_weekly(rows_days: list[int]) -> list[int]:
        rows: list[int] = []
        previous = None
        for index, day in enumerate(rows_days):
            if previous is None or day - previous >= 7 * DAY_MS:
                rows.append(index)
                previous = day
        return rows

    assert _cadence_rows(days, 7) == legacy_weekly(days) == _weekly_rows(days)
    for cadence in (3, 14):
        rows = _cadence_rows(days, cadence)
        assert rows[0] == 0
        assert all(
            days[rows[index + 1]] - days[rows[index]] >= cadence * DAY_MS
            for index in range(len(rows) - 1)
        )
        # densest grid consistent with the cadence: skipping a row would leave a
        # gap of at least 2 * cadence days
        assert all(
            days[rows[index + 1]] - days[rows[index]] < 2 * cadence * DAY_MS
            for index in range(len(rows) - 1)
        )


def _synthetic_panel(n_days: int = 60):
    import math as _math

    instruments = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX"]
    base = 1_700_000_000_000 - (1_700_000_000_000 % DAY_MS)
    days = [base + index * DAY_MS for index in range(n_days)]

    def series(offset: float, amp: float):
        return [
            [
                (None if (t + i) % 47 == 0 else offset + i * 0.01 + _math.sin(t * 0.1 + i) * amp)
                for i in range(len(instruments))
            ]
            for t in range(n_days)
        ]

    matrices = {
        "close": series(100.0, 5.0),
        "volume": [[1e8 + i * 1e7 + t * 1e5 for i in range(len(instruments))] for t in range(n_days)],
        "funding": series(0.0001, 0.00005),
        "oi": series(1e6, 1e5),
        "oi_value": series(1e8, 1e7),
        "top_ls": series(1.5, 0.3),
        "global_ls": series(1.2, 0.3),
        "taker_ls": series(1.1, 0.3),
    }
    return ax.FieldPanel(instruments, days, matrices), instruments, days


def test_build_weights_originals_and_combo_byte_identical_to_pre_variant() -> None:
    panel, instruments, days = _synthetic_panel()
    new = _build_weights(panel, instruments, days)

    def legacy_build():
        signals = {alpha_id: ax.evaluate(expr, panel) for alpha_id, expr in EXPRESSIONS.items()}
        rows = _weekly_rows(days)
        out: dict[str, dict[int, dict[str, float]]] = {alpha_id: {} for alpha_id in ALPHA_IDS}
        for alpha_id, signal in signals.items():
            for row in rows:
                weights = {}
                valid = [
                    i for i, value in enumerate(signal[row])
                    if value is not None and float(value) == float(value)
                ]
                if len(valid) >= 4:
                    valid.sort(key=lambda index: float(signal[row][index]))
                    k = max(1, int(round(len(valid) * QUANTILE)))
                    for index in valid[-k:]:
                        weights[instruments[index]] = 0.5 / k
                    for index in valid[:k]:
                        weights[instruments[index]] = weights.get(instruments[index], 0.0) - 0.5 / k
                if weights:
                    out[alpha_id][days[row]] = weights
        combo: dict[int, dict[str, float]] = {}
        for day in sorted(set().union(*(set(out[a]) for a in ALPHA_IDS))):
            merged: dict[str, float] = {}
            for alpha_id in ALPHA_IDS:
                for ticker, weight in out[alpha_id].get(day, {}).items():
                    merged[ticker] = merged.get(ticker, 0.0) + weight / len(ALPHA_IDS)
            combo[day] = merged
        out["equal_weight_combo"] = combo
        return out

    legacy = legacy_build()
    for alpha_id in (*ALPHA_IDS, "equal_weight_combo"):
        assert legacy[alpha_id].keys() == new[alpha_id].keys(), alpha_id
        for day in legacy[alpha_id]:
            assert legacy[alpha_id][day] == new[alpha_id][day], (alpha_id, day)


def test_build_weights_honours_variant_cadence_and_quantile() -> None:
    panel, instruments, days = _synthetic_panel()
    weights = _build_weights(panel, instruments, days)
    variant_ids = set(STRATEGY_SPECS) - set(ALPHA_IDS)
    assert variant_ids <= set(weights)
    for alpha_id in variant_ids:
        spec = STRATEGY_SPECS[alpha_id]
        cadence = int(spec["cadence_days"])
        strategy_days = sorted(weights[alpha_id])
        assert strategy_days, alpha_id
        assert all(
            strategy_days[index + 1] - strategy_days[index] >= cadence * DAY_MS
            for index in range(len(strategy_days) - 1)
        ), alpha_id
        # 14d cadence must be strictly sparser than the weekly grid on the same window
    assert len(weights["crowd_contra_14d"]) < len(weights["crowd_contra"])
    assert len(weights["crowd_contra_3d"]) > len(weights["crowd_contra"])


def test_variant_rebalance_ledger_is_idempotent(tmp_path) -> None:
    ledger_path = tmp_path / "paper-variants.db"
    simulations = {
        "crowd_contra_3d": {
            "rebalances": [
                {"day": 100, "target_quantities": {"BTC": 0.25}},
                {"day": 103, "target_quantities": {"BTC": -0.5}},
            ],
        },
        "crowd_taker_combo": {
            "rebalances": [{"day": 100, "target_quantities": {"ETH": 1.5}}],
        },
    }
    first = _persist_continuous_rebalances(
        "USDT", "data/cache/paper-variants.json", simulations, {"allowed": False},
        ledger_path=str(ledger_path),
    )
    assert first["rebalances"] == {
        "crowd_contra_3d": {"inserted": 2, "replayed": 0},
        "crowd_taker_combo": {"inserted": 1, "replayed": 0},
    }
    second = _persist_continuous_rebalances(
        "USDT", "data/cache/paper-variants.json", simulations, {"allowed": False},
        ledger_path=str(ledger_path),
    )
    assert second["rebalances"] == {
        "crowd_contra_3d": {"inserted": 0, "replayed": 2},
        "crowd_taker_combo": {"inserted": 0, "replayed": 1},
    }
    ledger = ContinuousPaperLedger(str(ledger_path), create=False)
    try:
        assert len(ledger.rows("crowd_contra_3d")) == 2
        assert len(ledger.rows("crowd_taker_combo")) == 1
    finally:
        ledger.close()
