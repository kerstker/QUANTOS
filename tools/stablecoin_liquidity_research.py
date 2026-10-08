"""Retrospective stablecoin-liquidity association screen (never promotion evidence)."""
from __future__ import annotations

import argparse
import json
import os
import time

from services.data.persistent_store import SqlitePITStore
from services.data.available_time import fetch_bars_for_event_decisions
from services.research.cftc_positioning import (
    DAY_MS,
    align_forward_returns,
    evaluate_positions,
    trailing_zscores,
)
from tools.cache_io import atomic_write_json
from tools.defillama_stablecoin_fetch import DEFAULT_OUTPUT as DEFAULT_SNAPSHOT


DEFAULT_EVIDENCE = os.path.join("evidence", "stablecoin_liquidity_research.json")
PRICE_DB = os.path.join("data", "cache", "crypto_funding.db")
WEEKDAY_UTC = 4  # Unix day 0 is Thursday; remainder 4 is Monday UTC.
Z_WINDOW_DAYS = 365
HORIZON_DAYS = 7
COST_BPS = 10.0


def _spec(identifier, lookback, transform, direction, rationale):
    return {
        "id": identifier, "lookback_days": lookback, "transform": transform,
        "direction": float(direction), "z_window_days": Z_WINDOW_DAYS,
        "horizon_days": HORIZON_DAYS, "rationale": rationale,
    }


def build_variant_specs() -> tuple[dict, ...]:
    return (
        _spec("stable_supply_growth_7d_mom", 7, "growth", 1,
              "A one-week stablecoin liquidity impulse may precede crypto demand."),
        _spec("stable_supply_growth_7d_rev", 7, "growth", -1,
              "The fixed opposite-direction control tests whether issuance follows rather than leads price."),
        _spec("stable_supply_growth_30d_mom", 30, "growth", 1,
              "A monthly stablecoin expansion may support a persistent risk-on regime."),
        _spec("stable_supply_growth_30d_rev", 30, "growth", -1,
              "The fixed monthly reversal control tests reactive issuance."),
        _spec("stable_supply_growth_90d_mom", 90, "growth", 1,
              "Quarterly dollar-liquidity expansion may be the slower crypto cycle driver."),
        _spec("stable_supply_accel_30d_mom", 30, "acceleration", 1,
              "Acceleration of monthly stablecoin growth may matter more than its level."),
    )


PRE_REGISTRATION = {
    "family": "defillama_global_stablecoin_liquidity_v1",
    "date": "2026-08-11",
    "trial_count_planned": 6,
    "historical_data_class": "RETROSPECTIVE_REVISABLE_NO_VINTAGES",
    "formal_registry_eligible": False,
    "search_space": (
        "Exactly six weekly Monday observations: 7d and 30d growth momentum/reversal, "
        "90d growth momentum, and 30d acceleration momentum; 365d z-score, 7d horizon."
    ),
}


def historical_signal_observations(spec: dict, snapshot: dict) -> list[tuple[int, float]]:
    if not isinstance(snapshot, dict) or (
        snapshot.get("provenance") != "REAL_DEFILLAMA_GLOBAL_STABLECOIN_HISTORY"
        or snapshot.get("revision_policy") != "RETROSPECTIVE_REVISABLE_NO_VINTAGES"
        or snapshot.get("pit_admissible_for_promotion") is not False
    ):
        raise ValueError("unexpected stablecoin snapshot contract")
    rows = sorted((int(timestamp), float(value)) for timestamp, value in snapshot["series"].items())
    times = [timestamp for timestamp, _value in rows]
    values = [value for _timestamp, value in rows]
    index_by_time = {timestamp: index for index, timestamp in enumerate(times)}
    raw = []
    lookback = spec["lookback_days"]
    for index, timestamp in enumerate(times):
        previous = index_by_time.get(timestamp - lookback * DAY_MS)
        if previous is None or values[previous] <= 0.0:
            raw.append(None)
            continue
        growth = values[index] / values[previous] - 1.0
        if spec["transform"] == "growth":
            raw.append(growth)
            continue
        prior_end = previous
        prior_start = index_by_time.get(timestamp - 2 * lookback * DAY_MS)
        if prior_start is None or values[prior_start] <= 0.0:
            raw.append(None)
        else:
            raw.append(growth - (values[prior_end] / values[prior_start] - 1.0))
    zscores = trailing_zscores(raw, window=spec["z_window_days"])
    observations = []
    for timestamp, zscore in zip(times, zscores):
        if zscore is None or (timestamp // DAY_MS) % 7 != WEEKDAY_UTC:
            continue
        signal = max(-1.0, min(1.0, spec["direction"] * zscore / 2.0))
        observations.append((timestamp, signal))
    return observations


def _price_rows(path: str):
    store = SqlitePITStore(path, create=False)
    return [(int(bar.event_time), float(bar.close))
            for bar in fetch_bars_for_event_decisions(
                store,
                "BTC",
                0,
                10**18,
                decision_lag=86_400_000,
                minimum_knowledge_lag=86_400_000,
            )]


def _lagged(aligned):
    return [{**aligned[index], "signal": aligned[index - 1]["signal"]}
            for index in range(1, len(aligned))]


def _without_series(result):
    return {key: value for key, value in result.items() if key not in ("gross_returns", "net_returns")}


def _summarize(aligned):
    primary = evaluate_positions(aligned, cost_bps=COST_BPS)
    stressed = evaluate_positions(aligned, cost_bps=20.0)
    lagged = evaluate_positions(_lagged(aligned), cost_bps=COST_BPS)
    flipped = evaluate_positions(
        [{**row, "signal": -row["signal"]} for row in aligned], cost_bps=COST_BPS
    )
    years = list(primary["per_year_mean"].values())
    checks = {
        "survives_20bps": stressed["net_sharpe"] > 0,
        "survives_one_week_lag": lagged["net_sharpe"] > 0,
        "broad_across_years": len(years) >= 3 and sum(v > 0 for v in years) / len(years) >= 0.6,
        "both_sides_positive": all(v is not None and v > 0 for v in primary["side_mean"].values()),
        "opposite_direction_loses": flipped["t_stat"] < 0,
    }
    failed = []
    if primary["t_stat"] < 2.5:
        failed.append("t_stat")
    if primary["net_sharpe"] < 1.0:
        failed.append("net_sharpe")
    if sum(checks.values()) < 4:
        failed.append("falsification")
    if primary["max_drawdown"] > 0.20:
        failed.append("max_drawdown")
    return {
        "primary": _without_series(primary), "cost_20bps": _without_series(stressed),
        "lagged": _without_series(lagged), "opposite_direction": _without_series(flipped),
        "falsification_checks": checks, "falsification_passes": sum(checks.values()),
        "association_verdict": "PROMISING_ASSOCIATION" if not failed else "REJECTED",
        "failed_bars": failed,
    }


def run_research(*, snapshot_path: str = DEFAULT_SNAPSHOT, price_db: str = PRICE_DB,
                 output_path: str = DEFAULT_EVIDENCE, generated_at_ms: int | None = None) -> dict:
    with open(snapshot_path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    prices = _price_rows(price_db)
    specs = build_variant_specs()
    results = {}
    for spec in specs:
        observations = historical_signal_observations(spec, snapshot)
        aligned = align_forward_returns(observations, prices, horizon_days=HORIZON_DAYS)
        results[spec["id"]] = {
            "spec": spec, "n_periods": len(aligned), **_summarize(aligned),
        }
    leads = [identifier for identifier, result in results.items()
             if result["association_verdict"] == "PROMISING_ASSOCIATION"]
    payload = {
        "schema_version": 1,
        "evidence_class": "RETROSPECTIVE_ASSOCIATION_DIAGNOSTIC",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": int(time.time() * 1000) if generated_at_ms is None else int(generated_at_ms),
        "pre_registration": {**PRE_REGISTRATION, "variants": list(specs)},
        "dataset": {
            "snapshot": os.path.abspath(snapshot_path), "price_db": os.path.abspath(price_db),
            "revision_policy": snapshot.get("revision_policy"),
        },
        "results": results,
        "promising_associations": leads,
        "candidates": [],
        "safety": {
            "public_data_only": True, "credentials_accessed": False,
            "order_submission_allowed": False, "promotion_eligible": False,
            "testnet_intent_generated": False,
        },
        "honesty": {
            "historical_rows_are_not_pit": True,
            "formal_trial_registration_skipped": True,
            "forward_vintage_required_for_admission": True,
            "notes": [
                "Historical associations can prioritize forward monitoring but cannot establish Alpha admission.",
                "The fetcher separately records one real bitemporal vintage per refresh.",
            ],
        },
    }
    atomic_write_json(output_path, payload, indent=2)
    return payload


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description="Screen stablecoin-liquidity associations")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    parser.add_argument("--price-db", default=PRICE_DB)
    parser.add_argument("--out", default=DEFAULT_EVIDENCE)
    parser.add_argument("--state-root", default=None)
    args = parser.parse_args(argv)
    if args.state_root:
        root = os.path.abspath(args.state_root)
        if not os.path.isdir(root):
            raise ValueError(f"state root does not exist: {root}")
        os.chdir(root)
    payload = run_research(
        snapshot_path=args.snapshot, price_db=args.price_db, output_path=args.out,
    )
    print(
        f"stablecoin liquidity screen: associations={len(payload['promising_associations'])}, "
        f"formal_candidates=0 -> {args.out}", flush=True,
    )
    for identifier, result in payload["results"].items():
        primary = result["primary"]
        print(
            f"  {identifier}: {result['association_verdict']} "
            f"t={primary['t_stat']:.3f} SR={primary['net_sharpe']:.3f} "
            f"failed={result['failed_bars']}", flush=True,
        )
    return payload


if __name__ == "__main__":
    main()


__all__ = [
    "PRE_REGISTRATION", "WEEKDAY_UTC", "build_variant_specs",
    "historical_signal_observations", "run_research",
]
