"""Pre-registered CFTC institutional-positioning Alpha research (L2 only)."""
from __future__ import annotations

import argparse
import json
import os
import time

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.cost import CostModel
from quantos.contracts.hashing import stable_hash
from services.alpha.config import AlphaFactoryConfig
from services.alpha.panel_backtest import PanelPeriod
from services.data.persistent_store import SqlitePITStore
from services.intelligence.registered_candidate_batch import run_registered_candidate_batch
from services.research.cftc_positioning import (
    align_forward_returns,
    evaluate_positions,
    trailing_zscores,
)
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.research_budget import ResearchBudget
from tools.cache_io import atomic_write_json
from tools.cftc_tff_fetch import DEFAULT_OUTPUT as DEFAULT_SNAPSHOT
from tools.search_trial_registry import production_experiment_registry


DEFAULT_EVIDENCE = os.path.join("evidence", "cftc_positioning_research.json")
PRICE_DB = os.path.join("data", "cache", "crypto_funding.db")
FAMILY = "cftc_institutional_positioning_v1"
DATA_SOURCE = "cftc_tff_crypto.json+crypto_funding.db"
WINDOW_WEEKS = 52
HORIZON_WEEKS = 1
COST_BPS = 10.0


def _spec(identifier, target, feature, transform, direction, rationale):
    return {
        "id": identifier,
        "target": target,
        "feature": feature,
        "transform": transform,
        "direction": float(direction),
        "window_weeks": WINDOW_WEEKS,
        "horizon_weeks": HORIZON_WEEKS,
        "rationale": rationale,
    }


def build_variant_specs() -> tuple[dict, ...]:
    specs = (
        _spec("btc_lev_money_level_reversal", "BTC", "leveraged_money_net_pct", "level_z", -1,
              "Extreme leveraged-fund net positioning is crowded and reverses in BTC."),
        _spec("btc_lev_money_change_reversal", "BTC", "leveraged_money_net_pct", "delta4_z", -1,
              "A rapid four-week leveraged-fund positioning change is exhaustion, not confirmation."),
        _spec("btc_asset_manager_level_momentum", "BTC", "asset_manager_net_pct", "level_z", 1,
              "Asset-manager positioning is slower capital and may persist in BTC."),
        _spec("btc_asset_vs_leverage_momentum", "BTC", "asset_minus_leverage", "level_z", 1,
              "Asset managers leading leveraged funds may identify a more durable BTC demand regime."),
        _spec("eth_lev_money_level_reversal", "ETH", "leveraged_money_net_pct", "level_z", -1,
              "Leveraged-fund crowding may reverse in CME Ether as it does in BTC."),
        _spec("eth_btc_lev_diff_reversal", "ETH_BTC_RELATIVE", "eth_btc_leverage_diff", "level_z", -1,
              "Relative leveraged-fund crowding between Ether and Bitcoin may reverse in ETH/BTC."),
    )
    if len({spec["id"] for spec in specs}) != 6:
        raise AssertionError("CFTC family must contain six unique variants")
    return specs


PRE_REGISTRATION = {
    "family": FAMILY,
    "registered_before_evaluation": True,
    "date": "2026-08-11",
    "trial_count_planned": 6,
    "primary_horizon": "one non-overlapping weekly CFTC observation",
    "window_weeks": WINDOW_WEEKS,
    "cost_bps": COST_BPS,
    "verdict_bar": {
        "min_t_stat": 2.5,
        "min_net_sharpe": 1.0,
        "min_falsification_passes": 4,
        "max_drawdown": 0.20,
    },
    "search_space": (
        "Exactly six fixed level/change/spread variants over CME BTC and ETH TFF percentages; "
        "52-week trailing z-score, one-week primary horizon, no post-result direction/window choice."
    ),
}
RESEARCH_BUDGET = ResearchBudget(
    max_trials=6,
    max_feature_count=2,
    max_parameter_dimensions=7,
    allowed_data_sources=(DATA_SOURCE,),
)


def _feature_rows(spec: dict, snapshot: dict) -> list[tuple[int, float]]:
    series = snapshot["series"]
    if spec["feature"] == "eth_btc_leverage_diff":
        btc = {row["knowledge_time_ms"]: row for row in series["BTC"]}
        eth = {row["knowledge_time_ms"]: row for row in series["ETH"]}
        times = sorted(set(btc) & set(eth))
        raw = [eth[t]["leveraged_money_net_pct"] - btc[t]["leveraged_money_net_pct"] for t in times]
    else:
        instrument = "ETH" if spec["target"] == "ETH" else "BTC"
        rows = series[instrument]
        times = [row["knowledge_time_ms"] for row in rows]
        if spec["feature"] == "asset_minus_leverage":
            raw = [row["asset_manager_net_pct"] - row["leveraged_money_net_pct"] for row in rows]
        else:
            raw = [row[spec["feature"]] for row in rows]
    if spec["transform"] == "delta4_z":
        transformed = [None] * 4 + [raw[index] - raw[index - 4] for index in range(4, len(raw))]
    else:
        transformed = raw
    zscores = trailing_zscores(transformed, window=spec["window_weeks"])
    observations = []
    for timestamp, zscore in zip(times, zscores):
        if zscore is None:
            continue
        signal = max(-1.0, min(1.0, spec["direction"] * zscore / 2.0))
        observations.append((int(timestamp), signal))
    return observations


def _price_rows(store, instrument: str):
    return [
        (int(bar.event_time), float(bar.close))
        for bar in store.fetch_bars_pit(instrument, 0, 10 ** 18)
    ]


def build_aligned_datasets(specs, snapshot: dict, *, price_db: str) -> dict[str, list[dict]]:
    if snapshot.get("provenance") != "REAL_CFTC_TFF_FUTURES_ONLY" or (
        snapshot.get("pit_admissible_for_research") is not True
    ):
        raise ValueError("unexpected CFTC snapshot contract")
    store = SqlitePITStore(price_db, create=False)
    btc = _price_rows(store, "BTC")
    eth = _price_rows(store, "ETH")
    aligned = {}
    for spec in specs:
        observations = _feature_rows(spec, snapshot)
        if spec["target"] == "BTC":
            aligned[spec["id"]] = align_forward_returns(observations, btc, horizon_days=7)
        elif spec["target"] == "ETH":
            aligned[spec["id"]] = align_forward_returns(observations, eth, horizon_days=7)
        else:
            aligned[spec["id"]] = align_forward_returns(
                observations, eth, benchmark_prices=btc, horizon_days=7,
            )
    return aligned


def panel_periods_for(target: str, aligned: list[dict]) -> tuple[PanelPeriod, ...]:
    periods = []
    for row in aligned:
        signal = float(row["signal"])
        if target == "ETH_BTC_RELATIVE":
            instruments = ("ETH", "BTC")
            forwards = (
                float(row.get("asset_leg_return", row["asset_return"])),
                float(row.get("benchmark_return", 0.0)),
            )
        else:
            instruments = (target, "CASH_USD")
            forwards = (float(row["asset_return"]), 0.0)
        periods.append(PanelPeriod(
            instruments=instruments,
            signals=(signal, -signal),
            forward_returns=forwards,
            event_time=int(row["entry_time_ms"]),
        ))
    return tuple(periods)


def _definition(spec: dict) -> AlphaDefinition:
    factor_ids = (
        ("eth_leveraged_money_net_pct", "btc_leveraged_money_net_pct")
        if spec["feature"] == "eth_btc_leverage_diff"
        else (spec["feature"],)
    )
    return AlphaDefinition(
        alpha_id=spec["id"], name=spec["id"], factor_ids=factor_ids,
        universe="cme_cot_two_leg_time_series",
        params={
            "family": FAMILY, "target": spec["target"], "feature": spec["feature"],
            "transform": spec["transform"], "direction": repr(spec["direction"]),
            "window_weeks": str(spec["window_weeks"]),
            "horizon_weeks": str(spec["horizon_weeks"]),
        },
    )


def register_family_trials(
    specs,
    aligned_by_id,
    *,
    registry: ExperimentRegistry,
    recorded_at: int,
    dataset_version: str,
) -> dict:
    before = registry._store.trial_count()
    candidates = tuple(
        (_definition(spec), panel_periods_for(spec["target"], aligned_by_id[spec["id"]]))
        for spec in specs
    )
    search_id = f"search-{stable_hash({'family': FAMILY, 'dataset': dataset_version})[:24]}"
    batch = run_registered_candidate_batch(
        candidates,
        registry=registry,
        search_id=search_id,
        search_kind="CFTC_INSTITUTIONAL_POSITIONING_FAMILY",
        search_source="tools.cftc_positioning_research",
        dataset_version=dataset_version,
        recorded_at=recorded_at,
        periods_per_year=52,
        quantile=0.5,
        config=AlphaFactoryConfig(compute_regime_evidence=True),
        cost=CostModel(fee_bps=5.0, slippage_bps=5.0, impact_coef=10.0),
        budget=RESEARCH_BUDGET,
        parent_hypothesis=(
            "Public CFTC trader categories contain a slower institutional crowding axis independent "
            "of crypto-exchange funding and open-interest telemetry."
        ),
        search_space=PRE_REGISTRATION["search_space"],
        data_source=DATA_SOURCE,
    )
    report = batch.get("report", {})
    evaluations = {}
    for spec in specs:
        entry = report.get(spec["id"], {})
        evaluation = entry.get("evaluation")
        evaluations[spec["id"]] = {
            "evaluator_verdict": evaluation.verdict.value if evaluation else None,
            "reasons": list(evaluation.reasons) if evaluation else [],
            "state": entry.get("state"),
            "mean_breadth": evaluation.mean_breadth if evaluation else None,
            "net_sharpe": evaluation.net_sharpe if evaluation else None,
            "deflated_sharpe": evaluation.deflated_sharpe if evaluation else None,
            "n_periods": evaluation.n_periods if evaluation else None,
        }
    after = registry._store.trial_count()
    return {
        "search_id": search_id,
        "trial_count_before": before,
        "trial_count_after": after,
        "trial_count_delta": after - before,
        "budget_declaration": batch.get("budget_declaration"),
        "evaluations": evaluations,
        "recorded_run_ids": list(batch.get("recorded_run_ids", ())),
        "promotion_eligible": False,
        "live_allowed": False,
    }


def _lagged(aligned):
    return [
        {**aligned[index], "signal": aligned[index - 1]["signal"]}
        for index in range(1, len(aligned))
    ]


def _summarize(aligned):
    primary = evaluate_positions(aligned, cost_bps=COST_BPS)
    lagged = evaluate_positions(_lagged(aligned), cost_bps=COST_BPS)
    stressed = evaluate_positions(aligned, cost_bps=20.0)
    flipped = evaluate_positions(
        [{**row, "signal": -row["signal"]} for row in aligned], cost_bps=COST_BPS
    )
    year_means = list(primary["per_year_mean"].values())
    checks = {
        "survives_20bps": stressed["net_sharpe"] > 0.0,
        "survives_one_report_lag": lagged["net_sharpe"] > 0.0,
        "broad_across_years": len(year_means) >= 3 and sum(v > 0 for v in year_means) / len(year_means) >= 0.6,
        "both_sides_positive": all(
            value is not None and value > 0.0 for value in primary["side_mean"].values()
        ),
        "opposite_direction_loses": flipped["t_stat"] < 0.0,
    }
    n_pass = sum(checks.values())
    bar = PRE_REGISTRATION["verdict_bar"]
    failed = []
    if primary["t_stat"] < bar["min_t_stat"]:
        failed.append("t_stat")
    if primary["net_sharpe"] < bar["min_net_sharpe"]:
        failed.append("net_sharpe")
    if n_pass < bar["min_falsification_passes"]:
        failed.append("falsification")
    if primary["max_drawdown"] > bar["max_drawdown"]:
        failed.append("max_drawdown")
    return {
        "primary": {key: value for key, value in primary.items() if key not in ("gross_returns", "net_returns")},
        "lagged": {key: value for key, value in lagged.items() if key not in ("gross_returns", "net_returns")},
        "cost_20bps": {key: value for key, value in stressed.items() if key not in ("gross_returns", "net_returns")},
        "opposite_direction": {key: value for key, value in flipped.items() if key not in ("gross_returns", "net_returns")},
        "falsification_checks": checks,
        "falsification_passes": n_pass,
        "diagnostic_verdict": "PROMISING_DIAGNOSTIC" if not failed else "REJECTED",
        "failed_bars": failed,
    }


def run_research(
    *, registry: ExperimentRegistry, snapshot_path: str = DEFAULT_SNAPSHOT,
    price_db: str = PRICE_DB, output_path: str = DEFAULT_EVIDENCE,
    recorded_at: int | None = None,
) -> dict:
    recorded_at = int(time.time() * 1000) if recorded_at is None else int(recorded_at)
    with open(snapshot_path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    specs = build_variant_specs()
    aligned = build_aligned_datasets(specs, snapshot, price_db=price_db)
    dataset_version = stable_hash({
        "snapshot": snapshot,
        "aligned": {
            identifier: [
                (row["entry_time_ms"], row["exit_time_ms"], row["signal"], row["asset_return"])
                for row in rows
            ]
            for identifier, rows in sorted(aligned.items())
        },
    })
    registration = register_family_trials(
        specs, aligned, registry=registry, recorded_at=recorded_at,
        dataset_version=dataset_version,
    )
    results = {
        spec["id"]: {
            "spec": spec,
            "coverage": {
                "n_periods": len(aligned[spec["id"]]),
                "first_entry_time_ms": aligned[spec["id"]][0]["entry_time_ms"] if aligned[spec["id"]] else None,
                "last_exit_time_ms": aligned[spec["id"]][-1]["exit_time_ms"] if aligned[spec["id"]] else None,
            },
            **_summarize(aligned[spec["id"]]),
        }
        for spec in specs
    }
    leads = [identifier for identifier, result in results.items()
             if result["diagnostic_verdict"] == "PROMISING_DIAGNOSTIC"]
    payload = {
        "schema_version": 1,
        "evidence_class": "RESEARCH_CLUSTER_PREREGISTERED",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": recorded_at,
        "pre_registration": {**PRE_REGISTRATION, "variants": list(specs)},
        "dataset": {
            "source": DATA_SOURCE, "dataset_version": dataset_version,
            "snapshot": os.path.abspath(snapshot_path),
            "price_db": os.path.abspath(price_db),
            "knowledge_time_policy": snapshot.get("knowledge_time_policy"),
        },
        "trial_registration": registration,
        "results": results,
        "promising_diagnostics": leads,
        "candidates": [],
        "safety": {
            "public_data_only": True, "credentials_accessed": False,
            "account_data_accessed": False, "order_submission_allowed": False,
            "promotion_eligible": False, "live_allowed": False,
            "testnet_intent_generated": False,
        },
        "honesty": {
            "formal_candidate_blocked_by_two_asset_breadth": True,
            "historical_research_is_not_forward_evidence": True,
            "notes": [
                "CFTC report-date rows are delayed seven days before use and enter on a strictly later close.",
                "The formal evaluator sees the honest two-leg breadth; no synthetic instruments are added.",
                "Even a promising diagnostic requires an independent forward gate before candidate admission.",
            ],
        },
    }
    atomic_write_json(output_path, payload, indent=2)
    return payload


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description="Run pre-registered CFTC positioning Alpha research")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    parser.add_argument("--price-db", default=PRICE_DB)
    parser.add_argument("--out", default=DEFAULT_EVIDENCE)
    parser.add_argument("--state-root", default=None)
    args = parser.parse_args(argv)
    if args.state_root:
        state_root = os.path.abspath(args.state_root)
        if not os.path.isdir(state_root):
            raise ValueError(f"state root does not exist: {state_root}")
        os.chdir(state_root)
    with production_experiment_registry() as registry:
        payload = run_research(
            registry=registry, snapshot_path=args.snapshot,
            price_db=args.price_db, output_path=args.out,
        )
    print(
        f"CFTC positioning research: promising={len(payload['promising_diagnostics'])}, "
        f"formal_candidates=0 -> {args.out}", flush=True,
    )
    for identifier, result in payload["results"].items():
        primary = result["primary"]
        print(
            f"  {identifier}: {result['diagnostic_verdict']} "
            f"t={primary['t_stat']:.3f} SR={primary['net_sharpe']:.3f} "
            f"failed={result['failed_bars']}", flush=True,
        )
    return payload


if __name__ == "__main__":
    main()


__all__ = [
    "PRE_REGISTRATION", "build_aligned_datasets", "build_variant_specs",
    "panel_periods_for", "register_family_trials", "run_research",
]
