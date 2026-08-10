"""Pre-registered three-venue funding-crowding Alpha research.

The new information axis is Hyperliquid's immutable hourly funding/premium
history, aligned to the existing Binance and Bybit daily funding panels.  Six
hypotheses are fixed before real evaluation.  Results remain L2 research only:
no candidate is promoted, routed to Testnet or granted order capability.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time

from quantos.contracts.alpha import AlphaDefinition
from services.alpha.config import AlphaFactoryConfig
from services.alpha.panel_backtest import PanelPeriod
from services.intelligence.registered_candidate_batch import run_registered_candidate_batch
from services.research import alpha_expression as ax
from services.research.alpha_expression import FieldPanel
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.research_budget import ResearchBudget
from services.research.leakage_evidence import panel_expression_leakage_ok
from tools.cache_io import atomic_write_json
from tools.cross_venue_research import _subset_universes, _xv_panel
from tools.crowd_contra_variants import cadence_rows, streams_cadence
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE
from tools.hyperliquid_public_fetch import DEFAULT_OUTPUT as DEFAULT_SNAPSHOT
from tools.mean_reversion_cluster import (
    stage_capacity,
    stage_dry_run,
    stage_gauntlet,
    stage_ic,
)
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)


DEFAULT_EVIDENCE = os.path.join("evidence", "hyperliquid_funding_research.json")
FAMILY = "hyperliquid_three_venue_funding_v1"
SEARCH_KIND = "HYPERLIQUID_THREE_VENUE_FUNDING_FAMILY"
SOURCE = "tools.hyperliquid_funding_research"
UNIVERSE = "realtime_14_three_venue_funding"
DATA_SOURCE = "crypto_funding.db+bybit_perp.db+hyperliquid_public_market.json"
QUANTILE = 0.30
CADENCE_DAYS = 7
Z_WINDOW = 20
MIN_INSTRUMENTS = 10
DAY_MS = 86_400_000
WEEKDAY_UTC = 4  # Unix day 0 is Thursday; remainder 4 is Monday UTC.


def _z(expression, window: int = Z_WINDOW):
    return (
        "div",
        ("sub", expression, ("ts_mean", expression, window)),
        ("ts_std", expression, window),
    )


_BINANCE_FUNDING = ("field", "funding")
_BYBIT_FUNDING = ("field", "xfunding")
_HL_FUNDING = ("field", "hlfunding")
_HL_PREMIUM = ("field", "hlpremium")
_CEX_MEAN = (
    "div", ("add", _BINANCE_FUNDING, _BYBIT_FUNDING), ("const", 2.0)
)
_HL_CEX_SPREAD = ("sub", _HL_FUNDING, _CEX_MEAN)
_THREE_VENUE_MEAN = (
    "div",
    ("add", ("add", _BINANCE_FUNDING, _BYBIT_FUNDING), _HL_FUNDING),
    ("const", 3.0),
)


def _spec(identifier, kind, expression, fields, rationale):
    return {
        "id": identifier,
        "kind": kind,
        "expr": expression,
        "fields": tuple(fields),
        "quantile": QUANTILE,
        "cadence_days": CADENCE_DAYS,
        "rationale": rationale,
    }


def build_variant_specs() -> tuple[dict, ...]:
    """Return the six immutable hypotheses in the v1 third-venue family."""
    specs = (
        _spec(
            "hl_cex_spread_z_rev",
            "hyperliquid_cex_funding_spread_reversal",
            ("cs_rank", ("neg", _z(_HL_CEX_SPREAD))),
            ("funding", "xfunding", "hlfunding"),
            "A Hyperliquid funding premium versus the two CEX venues is crowded leverage "
            "that mean-reverts cross-sectionally.",
        ),
        _spec(
            "hl_cex_spread_z_mom",
            "hyperliquid_cex_funding_spread_continuation_control",
            ("cs_rank", _z(_HL_CEX_SPREAD)),
            ("funding", "xfunding", "hlfunding"),
            "The opposite direction is fixed as a control: venue-specific leverage demand "
            "may continue rather than mean-revert.",
        ),
        _spec(
            "hl_cex_spread_level_rev",
            "hyperliquid_cex_funding_level_reversal",
            ("cs_rank", ("neg", _HL_CEX_SPREAD)),
            ("funding", "xfunding", "hlfunding"),
            "Raw venue funding dislocation may contain the edge without a fitted trailing window.",
        ),
        _spec(
            "hl_cex_spread_delta_rev",
            "hyperliquid_cex_funding_acceleration_reversal",
            ("cs_rank", ("neg", ("ts_delta", _HL_CEX_SPREAD, 7))),
            ("funding", "xfunding", "hlfunding"),
            "A rapid seven-day widening of the DEX-versus-CEX funding gap may mark leverage exhaustion.",
        ),
        _spec(
            "three_venue_consensus_carry",
            "three_venue_consensus_carry",
            ("cs_rank", ("neg", _THREE_VENUE_MEAN)),
            ("funding", "xfunding", "hlfunding"),
            "Funding agreed across Binance, Bybit and Hyperliquid is less venue-specific and may "
            "identify persistent cross-sectional carry.",
        ),
        _spec(
            "hl_premium_z_rev",
            "hyperliquid_impact_premium_reversal",
            ("cs_rank", ("neg", _z(_HL_PREMIUM))),
            ("hlpremium",),
            "Hyperliquid's impact-price premium directly measures local book pressure and may "
            "reverse even when settlement funding is capped or smoothed.",
        ),
    )
    if len({spec["id"] for spec in specs}) != 6:
        raise AssertionError("the pre-registered family must contain six unique ids")
    return specs


VERDICT_BAR = {
    "min_t_stat": 2.5,
    "t_stat_horizon": "weekly_non_overlapping_h7",
    "min_falsification_passes": 4,
    "min_vt_sharpe": 1.0,
    "max_drawdown_vt": 0.20,
}
PRE_REGISTRATION = {
    "family": FAMILY,
    "registered_before_evaluation": True,
    "date": "2026-08-11",
    "trial_count_planned": 6,
    "verdict_bar": dict(VERDICT_BAR),
    "parent_hypothesis": (
        "A decentralized perpetual venue contains an independent leverage-demand signal: "
        "its funding and impact premium relative to Binance and Bybit may reveal crowding, "
        "exhaustion or venue-consensus carry after costs."
    ),
    "search_space": (
        "Exactly six weekly q=0.30 variants: spread 20d z reversal and continuation, "
        "raw spread reversal, seven-day spread-delta reversal, three-venue mean carry, "
        "and Hyperliquid premium 20d z reversal. No post-result direction or window selection."
    ),
}

RESEARCH_BUDGET = ResearchBudget(
    max_trials=6,
    max_feature_count=3,
    max_parameter_dimensions=5,
    allowed_data_sources=(DATA_SOURCE,),
)


def enrich_panel_with_hyperliquid(
    base_panel: FieldPanel,
    snapshot: dict,
    *,
    min_instruments: int = MIN_INSTRUMENTS,
) -> tuple[FieldPanel, dict]:
    if min_instruments <= 0:
        raise ValueError("min_instruments must be positive")
    if not isinstance(snapshot, dict) or (
        snapshot.get("provenance") != "REAL_HYPERLIQUID_PUBLIC_MARKET_HISTORY"
        or snapshot.get("pit_admissible_for_research") is not True
    ):
        raise ValueError("unexpected Hyperliquid snapshot contract")
    series = snapshot.get("series")
    if not isinstance(series, dict):
        raise ValueError("Hyperliquid snapshot has no series")

    funding_matrix = []
    premium_matrix = []
    eligible_indices = []
    counts = []
    for row_index, event_time in enumerate(base_panel.event_times):
        funding_row = []
        premium_row = []
        for instrument in base_panel.instruments:
            by_day = series.get(instrument)
            values = by_day.get(str(int(event_time))) if isinstance(by_day, dict) else None
            if isinstance(values, dict) and values.get("observation_count") == 24:
                try:
                    funding = float(values["funding_sum"])
                    premium = float(values["premium_mean"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    funding = premium = None
            else:
                funding = premium = None
            funding_row.append(funding)
            premium_row.append(premium)
        present = sum(value is not None for value in funding_row)
        if present >= min_instruments:
            eligible_indices.append(row_index)
            counts.append(present)
        funding_matrix.append(funding_row)
        premium_matrix.append(premium_row)

    if not eligible_indices:
        raise ValueError(
            f"Hyperliquid daily cross-section never reaches {min_instruments} instruments"
        )
    matrices = {
        name: [matrix[index] for index in eligible_indices]
        for name, matrix in base_panel.matrices.items()
    }
    matrices["hlfunding"] = [funding_matrix[index] for index in eligible_indices]
    matrices["hlpremium"] = [premium_matrix[index] for index in eligible_indices]
    event_times = tuple(base_panel.event_times[index] for index in eligible_indices)
    panel = FieldPanel(base_panel.instruments, event_times, matrices)
    return panel, {
        "eligible_days": len(eligible_indices),
        "first_event_time_ms": event_times[0],
        "last_event_time_ms": event_times[-1],
        "minimum_cross_section": min(counts),
        "maximum_cross_section": max(counts),
        "requested_minimum_cross_section": min_instruments,
        "source_errors": dict(snapshot.get("errors") or {}),
    }


def hyperliquid_instruments(panel: FieldPanel, snapshot: dict) -> tuple[str, ...]:
    """Return panel names with at least one retained Hyperliquid daily row."""
    series = snapshot.get("series") if isinstance(snapshot, dict) else None
    if not isinstance(series, dict):
        return ()
    return tuple(
        instrument
        for instrument in panel.instruments
        if isinstance(series.get(instrument), dict) and bool(series[instrument])
    )


def _definition_for(spec: dict) -> AlphaDefinition:
    return AlphaDefinition(
        alpha_id=spec["id"],
        name=spec["id"],
        factor_ids=tuple(spec["fields"]),
        universe=UNIVERSE,
        params={
            "family": FAMILY,
            "kind": spec["kind"],
            "expr": ax.serialize(spec["expr"]),
            "quantile": repr(spec["quantile"]),
            "cadence_days": str(spec["cadence_days"]),
        },
    )


def fixed_weekly_periods(specs, signals, panel) -> dict[str, tuple[PanelPeriod, ...]]:
    """Build every family member on one fixed non-overlapping UTC calendar."""
    close = panel.matrices["close"]
    output = {}
    for spec in specs:
        alpha = signals[spec["id"]]
        periods = []
        for row in range(len(panel.event_times) - CADENCE_DAYS):
            event_time = int(panel.event_times[row])
            if (event_time // DAY_MS) % 7 != WEEKDAY_UTC:
                continue
            forward_row = row + CADENCE_DAYS
            instruments, signal_values, forward_returns = [], [], []
            for column, instrument in enumerate(panel.instruments):
                signal = alpha[row][column]
                p0, p1 = close[row][column], close[forward_row][column]
                if (
                    signal is None or p0 is None or p1 is None or p0 <= 0
                    or not math.isfinite(float(signal))
                    or not math.isfinite(float(p0))
                    or not math.isfinite(float(p1))
                ):
                    continue
                instruments.append(instrument)
                signal_values.append(float(signal))
                forward_returns.append(float(p1 / p0 - 1.0))
            if len(instruments) >= 2:
                periods.append(PanelPeriod(
                    instruments=tuple(instruments),
                    signals=tuple(signal_values),
                    forward_returns=tuple(forward_returns),
                    event_time=event_time,
                ))
        output[spec["id"]] = tuple(periods)
    return output


def register_family_trials(
    specs,
    signals,
    panel,
    *,
    registry: ExperimentRegistry,
    recorded_at: int,
) -> dict:
    dataset_version = field_panel_dataset_version(
        panel, source_versions={"family": FAMILY, "universe": UNIVERSE}
    )
    before = registry._store.trial_count()
    periods_by_id = fixed_weekly_periods(specs, signals, panel)
    target_rows = [
        row for row, event_time in enumerate(panel.event_times[:-CADENCE_DAYS])
        if (int(event_time) // DAY_MS) % 7 == WEEKDAY_UTC
    ]
    evidence = {
        spec["id"]: {
            "leakage_ok": panel_expression_leakage_ok(
                spec["expr"], panel, target_rows,
            )
        }
        for spec in specs
    }
    candidates = tuple(
        (
            _definition_for(spec),
            periods_by_id[spec["id"]],
        )
        for spec in specs
    )
    search_id = make_search_id(
        source=SOURCE,
        search_kind=SEARCH_KIND,
        dataset_version=dataset_version,
        recorded_at=recorded_at,
        params={
            "family": FAMILY,
            "cadence_days": CADENCE_DAYS,
            "candidate_count": len(candidates),
        },
    )
    batch = run_registered_candidate_batch(
        candidates,
        registry=registry,
        search_id=search_id,
        search_kind=SEARCH_KIND,
        search_source=SOURCE,
        dataset_version=dataset_version,
        recorded_at=recorded_at,
        periods_per_year=52,
        config=AlphaFactoryConfig(compute_regime_evidence=True),
        evidence=evidence,
        budget=RESEARCH_BUDGET,
        parent_hypothesis=PRE_REGISTRATION["parent_hypothesis"],
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
            "deflated_sharpe": None if evaluation is None else evaluation.deflated_sharpe,
            "pbo": None if evaluation is None else evaluation.pbo,
            "pbo_evidence_retained": bool(
                evaluation is not None and evaluation.pbo_evidence is not None
            ),
            "ic_tstat": None if evaluation is None else evaluation.ic_tstat,
            "net_sharpe": None if evaluation is None else evaluation.net_sharpe,
            "mean_breadth": None if evaluation is None else evaluation.mean_breadth,
            "n_periods": None if evaluation is None else evaluation.n_periods,
        }
    after = registry._store.trial_count()
    return {
        "dataset_version": dataset_version,
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


def verdict_for(stage_values: dict) -> dict:
    failed = []
    if stage_values.get("t_stat") is None or stage_values["t_stat"] < VERDICT_BAR["min_t_stat"]:
        failed.append("t_stat")
    if (
        stage_values.get("falsification_passes") is None
        or stage_values["falsification_passes"] < VERDICT_BAR["min_falsification_passes"]
    ):
        failed.append("falsification")
    if (
        stage_values.get("vt_sharpe") is None
        or stage_values["vt_sharpe"] < VERDICT_BAR["min_vt_sharpe"]
    ):
        failed.append("vt_sharpe")
    if (
        stage_values.get("max_drawdown_vt") is None
        or stage_values["max_drawdown_vt"] > VERDICT_BAR["max_drawdown_vt"]
    ):
        failed.append("max_drawdown")
    return {"verdict": "CANDIDATE" if not failed else "REJECTED", "failed_bars": failed}


def _load_snapshot(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _load_panel(snapshot: dict, *, only=None, min_instruments: int = MIN_INSTRUMENTS):
    base, _instruments = _xv_panel(only=only)
    return enrich_panel_with_hyperliquid(
        base, snapshot, min_instruments=min_instruments
    )


def run_research(
    *,
    registry: ExperimentRegistry,
    snapshot_path: str = DEFAULT_SNAPSHOT,
    output_path: str = DEFAULT_EVIDENCE,
    recorded_at: int | None = None,
) -> dict:
    if recorded_at is None:
        recorded_at = int(time.time() * 1000)
    snapshot = _load_snapshot(snapshot_path)
    panel, coverage = _load_panel(snapshot)
    specs = build_variant_specs()
    signals = {spec["id"]: ax.evaluate(spec["expr"], panel) for spec in specs}

    # Register every trial before looking at any stage result.
    registration = register_family_trials(
        specs, signals, panel, registry=registry, recorded_at=recorded_at
    )

    available_instruments = hyperliquid_instruments(panel, snapshot)
    subset_panels = {}
    for name, instruments in _subset_universes(list(available_instruments)).items():
        if len(instruments) < 5:
            continue
        subset_panels[name] = _load_panel(
            snapshot,
            only=set(instruments),
            min_instruments=min(5, len(instruments)),
        )[0]
    subset_signals = {
        spec["id"]: {
            name: ax.evaluate(spec["expr"], subset_panel)
            for name, subset_panel in subset_panels.items()
        }
        for spec in specs
    }

    carry_signal = ax.evaluate(CARRY_COMPOSITE, panel)
    carry_daily, _carry_econ, _carry_turnover = streams_cadence(
        carry_signal,
        panel.matrices["close"],
        panel.matrices["funding"],
        QUANTILE,
        cadence_rows(panel.event_times, 1),
    )

    results = {}
    for spec in specs:
        identifier = spec["id"]
        stages = {}
        reached = "none"
        error = None
        try:
            reached = "ic"
            stages["ic"] = stage_ic(panel, signals[identifier])
            reached = "falsification"
            stages["falsification"] = stage_gauntlet(
                panel,
                {
                    name: (subset_panel, subset_signals[identifier][name])
                    for name, subset_panel in subset_panels.items()
                },
                spec,
                signals[identifier],
                carry_daily,
                panel.event_times,
            )
            reached = "capacity"
            stages["capacity"] = stage_capacity(panel, signals[identifier], spec)
            reached = "dry_run"
            stages["dry_run"] = stage_dry_run(panel, spec, signals[identifier])
            reached = "complete"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        h7 = (stages.get("ic") or {}).get("h7") or {}
        falsification = stages.get("falsification") or {}
        dry_run = stages.get("dry_run") or {}
        verdict = verdict_for(
            {
                "t_stat": h7.get("t_stat"),
                "falsification_passes": falsification.get("n_pass"),
                "vt_sharpe": dry_run.get("sharpe_vt"),
                "max_drawdown_vt": dry_run.get("max_drawdown_vt"),
            }
        )
        if reached != "complete":
            verdict = {"verdict": "REJECTED", "failed_bars": ["hard_failure"]}
        results[identifier] = {
            "spec": {
                "id": identifier,
                "kind": spec["kind"],
                "expression": ax.serialize(spec["expr"]),
                "fields": list(spec["fields"]),
                "rationale": spec["rationale"],
            },
            "stages": stages,
            "stage_reached": reached,
            "error": error,
            "verdict": verdict,
        }

    candidates = [
        identifier
        for identifier, result in results.items()
        if result["verdict"]["verdict"] == "CANDIDATE"
    ]
    payload = {
        "schema_version": 1,
        "evidence_class": "RESEARCH_CLUSTER_PREREGISTERED",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": int(recorded_at),
        "pre_registration": {
            **PRE_REGISTRATION,
            "variants": [
                {
                    "id": spec["id"],
                    "kind": spec["kind"],
                    "expression": ax.serialize(spec["expr"]),
                    "fields": list(spec["fields"]),
                    "rationale": spec["rationale"],
                }
                for spec in specs
            ],
        },
        "dataset": {
            "source": DATA_SOURCE,
            "source_snapshot": os.path.abspath(snapshot_path),
            "instruments": list(panel.instruments),
            "instrument_count": len(panel.instruments),
            "coverage": coverage,
            "dataset_version": registration["dataset_version"],
        },
        "trial_registration": registration,
        "results": results,
        "candidates": candidates,
        "safety": {
            "paper_only": True,
            "public_market_data_only": True,
            "credentials_accessed": False,
            "account_data_accessed": False,
            "broker_connectivity": False,
            "order_submission_allowed": False,
            "promotion_eligible": False,
            "live_allowed": False,
        },
        "honesty": {
            "historical_research_is_not_forward_evidence": True,
            "three_venue_execution_not_modeled": True,
            "testnet_intent_generated": False,
            "notes": [
                "Hyperliquid funding history consists of public settlement events; only complete UTC days are used.",
                "The dry-run prices the book on the existing Binance panel and does not claim simultaneous three-venue execution.",
                "A historical candidate would still require locked forward evidence, formal validation and capacity admission.",
            ],
        },
    }
    atomic_write_json(output_path, payload, indent=2)
    return payload


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description="Run pre-registered Hyperliquid/Binance/Bybit funding Alpha research"
    )
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    parser.add_argument("--out", default=DEFAULT_EVIDENCE)
    parser.add_argument(
        "--state-root",
        default=None,
        help="Optional runtime root containing data/cache; code remains loaded from this checkout",
    )
    args = parser.parse_args(argv)
    if args.state_root:
        state_root = os.path.abspath(args.state_root)
        if not os.path.isdir(state_root):
            raise ValueError(f"state root does not exist: {state_root}")
        os.chdir(state_root)
    with production_experiment_registry() as registry:
        payload = run_research(
            registry=registry,
            snapshot_path=args.snapshot,
            output_path=args.out,
        )
    print(
        f"three-venue funding research: candidates={len(payload['candidates'])} -> {args.out}",
        flush=True,
    )
    for identifier, result in payload["results"].items():
        h7 = (result["stages"].get("ic") or {}).get("h7") or {}
        dry_run = result["stages"].get("dry_run") or {}
        print(
            f"  {identifier}: {result['verdict']['verdict']} "
            f"t={h7.get('t_stat')} VT={dry_run.get('sharpe_vt')} "
            f"failed={result['verdict']['failed_bars']}",
            flush=True,
        )
    return payload


if __name__ == "__main__":
    main()


__all__ = [
    "DATA_SOURCE", "PRE_REGISTRATION", "RESEARCH_BUDGET", "VERDICT_BAR",
    "build_variant_specs", "enrich_panel_with_hyperliquid", "hyperliquid_instruments",
    "fixed_weekly_periods", "register_family_trials", "run_research", "verdict_for",
]
