"""Dedicated offline refutation battery for ``top_contra``.

The original OI verification harness is intentionally centered on
``crowd_contra``.  This companion keeps the same PIT-safe streams and
thresholds, but records independent evidence for the third candidate:
year/subset robustness, costs, execution lag, quantile sensitivity, and
incremental information versus carry.  It is research evidence only; it does
not touch the AlphaCatalog, promotion state, paper ledger, or order path.
"""
from __future__ import annotations

import os
import statistics

from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.factor_set import incremental_ir
from services.research.oos_validation import walk_forward_selection_oos
from tools.cache_io import atomic_write_json
from tools.crypto_oi_research import CARRY, build_panel
from tools.crypto_oi_verify import TOP_CONTRA, _per_year, _streams


OUTPUT = os.path.join("data", "cache", "crypto_oi_top_verify.json")
TOP_FOLLOW = ("cs_zscore", ("field", "top_ls"))


def _round(value):
    return None if value is None else round(float(value), 3)


def run_top_refutation() -> dict:
    panel, instruments = build_panel()
    prices = panel.matrices["close"]
    funding = panel.matrices["funding"]
    times = panel.event_times
    signal = ax.evaluate(TOP_CONTRA, panel)
    price, economic, turnover = _streams(signal, prices, funding, q=0.3, lag=0)

    result: dict = {
        "schema_version": 1,
        "evidence_class": "RESEARCH_REFUTATION_DIAGNOSTIC",
        "candidate": "top_contra = cs_zscore(neg(top_ls))",
        "universe": instruments,
        "n_instruments": len(instruments),
        "n_days": len(price),
        "baseline": {
            "price_sharpe": _round(sharpe(price)),
            "economic_sharpe": _round(sharpe(economic)),
            "avg_turnover": _round(statistics.fmean(turnover)),
        },
        "per_year_price": _per_year(price, times),
        "per_year_economic": _per_year(economic, times),
        "net_sharpe_by_fee_bps": {},
        "price_sharpe_by_quantile": {},
        "price_sharpe_by_lag": {},
        "price_sharpe_by_subset": {},
    }

    for fee in (2, 5, 10, 20):
        net = [p - t * fee / 1e4 for p, t in zip(price, turnover)]
        result["net_sharpe_by_fee_bps"][str(fee)] = _round(sharpe(net))
    for quantile in (0.2, 0.3, 0.4):
        q_price, _q_economic, _q_turnover = _streams(
            signal, prices, funding, q=quantile, lag=0
        )
        result["price_sharpe_by_quantile"][str(quantile)] = _round(sharpe(q_price))
    for lag in (0, 1, 2):
        lag_price, _lag_economic, _lag_turnover = _streams(
            signal, prices, funding, q=0.3, lag=lag
        )
        result["price_sharpe_by_lag"][str(lag)] = _round(sharpe(lag_price))

    top4 = {"BTC", "ETH", "BNB", "SOL"}
    subsets = {
        "drop_BTC": [ticker for ticker in instruments if ticker != "BTC"],
        "drop_top4": [ticker for ticker in instruments if ticker not in top4],
        "even_idx": instruments[::2],
        "odd_idx": instruments[1::2],
    }
    for name, subset in subsets.items():
        if len(subset) < 5:
            continue
        subset_panel, subset_instruments = build_panel(only=set(subset))
        subset_signal = ax.evaluate(TOP_CONTRA, subset_panel)
        subset_price, _subset_economic, _subset_turnover = _streams(
            subset_signal,
            subset_panel.matrices["close"],
            subset_panel.matrices["funding"],
            q=0.3,
            lag=0,
        )
        result["price_sharpe_by_subset"][name] = {
            "n": len(subset_instruments),
            "price_sharpe": _round(sharpe(subset_price)),
        }

    carry_signal = ax.evaluate(CARRY, panel)
    carry_price, _carry_economic, _carry_turnover = _streams(
        carry_signal, prices, funding, q=0.3, lag=0
    )
    follow_signal = ax.evaluate(TOP_FOLLOW, panel)
    follow_price, _follow_economic, _follow_turnover = _streams(
        follow_signal, prices, funding, q=0.3, lag=0
    )
    top_vs_carry = incremental_ir(price, {"carry": tuple(carry_price)}, min_overlap=120)
    result["combine"] = {
        "carry_sharpe": _round(sharpe(carry_price)),
        "top_contra_sharpe": _round(sharpe(price)),
        "incremental_ir_vs_carry": _round(top_vs_carry),
    }
    result["direction_sanity"] = {
        "top_contra": _round(sharpe(price)),
        "top_follow": _round(sharpe(follow_price)),
    }

    streams = {
        "carry": tuple(carry_price),
        "top_contra": tuple(price),
        "top_follow": tuple(follow_price),
    }
    oos = walk_forward_selection_oos(
        streams,
        train_size=max(200, len(carry_price) // 6),
        test_size=max(90, len(carry_price) // 12),
        embargo=2,
        top_k=1,
    )
    result["embargoed_oos"] = {
        "n_folds": oos["n_folds"],
        "mean_is": oos.get("mean_is_sharpe"),
        "mean_oos": oos.get("mean_oos_sharpe"),
        "haircut": oos.get("oos_minus_is"),
        "picks": [
            (fold["window"], fold["selected"], _round(fold["oos_sharpe"]))
            for fold in oos.get("folds", [])
        ],
    }

    lag_1 = result["price_sharpe_by_lag"].get("1")
    net_10 = result["net_sharpe_by_fee_bps"].get("10")
    per_year = result["per_year_price"]
    subset_sharpes = [
        value["price_sharpe"]
        for value in result["price_sharpe_by_subset"].values()
        if value.get("price_sharpe") is not None
    ]
    checks = {
        "survives_1d_lag": lag_1 is not None and lag_1 > 0.5,
        "survives_10bps_cost": net_10 is not None and net_10 > 0.5,
        "broad_across_years": sum(value > 0 for value in per_year.values())
        >= max(2, len(per_year) - 1),
        "survives_subsets": bool(subset_sharpes)
        and all(value > 0.3 for value in subset_sharpes),
        "adds_to_carry": top_vs_carry is not None and top_vs_carry > 0.3,
    }
    result["refutation_checks"] = checks
    passed = sum(checks.values())
    result["n_pass"] = passed
    result["verdict"] = (
        f"SURVIVES {passed}/5 refutation checks"
        if passed >= 4
        else f"PARTIAL {passed}/5 refutation checks"
        if passed >= 2
        else f"FAILS {passed}/5 refutation checks"
    )
    return result


def main() -> None:
    report = run_top_refutation()
    atomic_write_json(OUTPUT, report, indent=2)
    print(
        f"top_contra refutation: {report['verdict']} "
        f"baseline_sharpe={report['baseline']['price_sharpe']} -> {OUTPUT}",
        flush=True,
    )


if __name__ == "__main__":
    main()

