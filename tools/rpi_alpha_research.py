"""Pre-registered RPI-inclusive deep-liquidity Alpha research.

This L2-only worker consumes the existing append-only Binance public RPI depth
authority plus the retained public realtime feature store.  It evaluates one
fixed three-candidate, one-hour family and registers every attempt in the global
experiment ledger before publishing any ranking.  Fewer than 30 common days may
produce diagnostics, but can never produce a formal research PASS.

The tool has no network call, credential, broker adapter, lifecycle transition,
paper admission, or order-submission seam.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import json
from pathlib import Path
import statistics
import time
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct CLI compatibility
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import ValidationVerdict
from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from quantos.runtime_paths import runtime_state_root
from services.alpha.catalog import AlphaCatalog
from services.data.binance_systemic_store import BinanceSystemicStore
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.realtime_alpha_discovery import (
    RealtimeAlphaPolicy,
    RealtimeFrame,
    RealtimeHypothesisEvaluation,
    evaluate_hypothesis,
)
from services.research.rpi_alpha import (
    HOUR_MS,
    RPI_DATASET_ID,
    RpiDataset,
    RpiDepthObservation,
    assess_rpi_data_admission,
    default_rpi_hypotheses,
    enrich_frames_with_rpi,
)
from services.research.trial_provenance import trial_provenance_for_definition
from services.validation.deflated_sharpe import deflated_sharpe_ratio, pbo_cscv
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json
from tools.realtime_alpha_discovery import load_bucketed_frames
from tools.search_trial_registry import make_search_id


ROOT = runtime_state_root()
DEFAULT_RPI_STORE = ROOT / "data" / "cache" / "binance_rpi_depth.db"
DEFAULT_SOURCE_STORE = ROOT / "data" / "cache" / "binance_realtime_features.db"
DEFAULT_EXPERIMENT_DB = ROOT / "data" / "cache" / "experiment_registry.db"
DEFAULT_OUTPUT = ROOT / "data" / "cache" / "rpi_alpha_research.json"

STRICT_DSR_MIN = 0.95
STRICT_PBO_MAX = 0.25
STRICT_HOLDOUT_SHARPE_MIN = 0.80
STRICT_WF_POSITIVE_FRAC = 0.75
STRICT_REGIME_POSITIVE_FRAC = 0.75
STRICT_MAX_DRAWDOWN = 0.15
REMAINING_PROMOTION_GATES = (
    "CAPACITY_NOT_ASSESSED",
    "PORTFOLIO_INCREMENT_NOT_ASSESSED",
    "INDEPENDENT_CHALLENGE_NOT_ASSESSED",
    "SIGNAL_SHADOW_NOT_MATURE",
)


class RpiAlphaResearchError(RuntimeError):
    """The RPI research input or durable evidence path is invalid."""


def load_rpi_dataset(path: str | Path) -> RpiDataset:
    resolved = Path(path)
    if not resolved.is_file():
        raise RpiAlphaResearchError("RPI authority store is unavailable")
    store = BinanceSystemicStore(str(resolved), create=False)
    try:
        chain_valid = store.chain_valid()
        rows = store.range_observations(
            RPI_DATASET_ID,
            start_event_time_ms=0,
            end_event_time_ms=10**18,
        )
        head = store.authority_head()
        authority_hash = stable_hash(
            {
                "dataset": RPI_DATASET_ID,
                "head": head,
                "row_count": store.row_count(RPI_DATASET_ID),
            }
        )
    finally:
        store.close()
    observations: list[RpiDepthObservation] = []
    for row in rows:
        try:
            observations.append(
                RpiDepthObservation(
                    instrument_id=str(row["instrument_id"]),
                    event_time_ms=int(row["event_time_ms"]),
                    knowledge_time_ms=int(row["knowledge_time_ms"]),
                    notional_imbalance=float(row["notional_imbalance"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RpiAlphaResearchError("RPI observation payload is invalid") from exc
    return RpiDataset(
        observations=tuple(observations),
        authority_hash=authority_hash,
        chain_valid=chain_valid,
    )


def _definition(spec) -> AlphaDefinition:
    return AlphaDefinition(
        alpha_id=spec.candidate_id,
        name=f"RPI deep-liquidity {spec.mechanism}",
        factor_ids=tuple(sorted(spec.required_features)),
        universe="binance_usdm_14_public_rpi",
        params={
            "family_id": spec.family_id,
            "mechanism": spec.mechanism,
            "horizon_ms": str(spec.horizon_ms),
            "signal_version": spec.signal_version,
            "label_policy": "post_latency_first_known_mid",
            "rpi_dataset": RPI_DATASET_ID,
        },
    )


def _raw_observed_sr(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    deviation = statistics.pstdev(values)
    return statistics.fmean(values) / deviation if deviation > 0 else 0.0


def _family_pbo(
    evaluations: Mapping[str, RealtimeHypothesisEvaluation],
) -> float | None:
    items = [item for item in evaluations.values() if item.status == "EVALUATED"]
    if len(items) < 2:
        return None
    streams = {
        item.candidate_id: dict(zip(item.event_times, item.net_returns, strict=True))
        for item in items
    }
    common = set(next(iter(streams.values())))
    for stream in streams.values():
        common.intersection_update(stream)
    times = sorted(common)
    if len(times) < 16:
        return None
    block_count = min(8, len(times) // 2)
    matrix = []
    for block in range(block_count):
        lower = len(times) * block // block_count
        upper = len(times) * (block + 1) // block_count
        matrix.append(
            [
                statistics.fmean(
                    streams[item.candidate_id][event]
                    for event in times[lower:upper]
                )
                for item in items
            ]
        )
    try:
        return pbo_cscv(matrix)
    except (ValueError, ZeroDivisionError, ArithmeticError):
        return None


def _strict_gate(
    item: RealtimeHypothesisEvaluation,
    *,
    formal_validation_allowed: bool,
    dsr: float | None,
    pbo: float | None,
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if not formal_validation_allowed:
        reasons.append("RPI_HISTORY_BELOW_30D")
    if item.status != "EVALUATED":
        reasons.append("INSUFFICIENT_PIT_OBSERVATIONS")
    if dsr is None or dsr < STRICT_DSR_MIN:
        reasons.append("DSR_BELOW_0_95_OR_UNASSESSED")
    if pbo is None or pbo > STRICT_PBO_MAX:
        reasons.append("PBO_ABOVE_0_25_OR_UNASSESSED")
    if item.holdout_net_sharpe < STRICT_HOLDOUT_SHARPE_MIN:
        reasons.append("LOCKED_HOLDOUT_SHARPE_BELOW_0_80")
    if (
        item.walk_forward_positive_frac is None
        or item.walk_forward_positive_frac < STRICT_WF_POSITIVE_FRAC
    ):
        reasons.append("WALK_FORWARD_POSITIVE_FRAC_BELOW_75PCT")
    if (
        item.spread_regime_positive_frac is None
        or item.spread_regime_positive_frac < STRICT_REGIME_POSITIVE_FRAC
    ):
        reasons.append("SPREAD_REGIME_POSITIVE_FRAC_BELOW_75PCT")
    worst = item.pressure.get("2.0x", {})
    if float(worst.get("net_return", 0.0)) <= 0:
        reasons.append("DOUBLE_COST_NET_RETURN_NON_POSITIVE")
    if float(worst.get("net_sharpe", 0.0)) < STRICT_HOLDOUT_SHARPE_MIN:
        reasons.append("DOUBLE_COST_SHARPE_BELOW_0_80")
    if float(worst.get("max_drawdown", item.max_drawdown)) > STRICT_MAX_DRAWDOWN:
        reasons.append("MAX_DRAWDOWN_ABOVE_15PCT")
    return not reasons, tuple(reasons)


def _empty_report(*, now: int, admission, output_path: str | Path) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "generated_at_ms": now,
        "status": admission.status,
        "mode": "L2_RPI_ALPHA_RESEARCH",
        "data_admission": admission.as_dict(),
        "manifest": [item.as_dict() for item in default_rpi_hypotheses()],
        "search_id": None,
        "funnel": {
            "pre_registered": 3,
            "evaluated": 0,
            "statistical_passes": 0,
            "promotion_eligible": 0,
        },
        "candidates": [],
        "global_trial_count": 0,
        "remaining_promotion_gates": list(REMAINING_PROMOTION_GATES),
        "safety": {
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }
    return _publish_report(report, output_path)


def _publish_report(
    report: Mapping[str, Any], output_path: str | Path
) -> dict[str, Any]:
    """Return exactly the JSON shape persisted on disk (tuples become lists)."""
    try:
        normalized = json.loads(
            json.dumps(
                report,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise RpiAlphaResearchError("RPI report is not canonical JSON") from exc
    atomic_write_json(str(output_path), normalized, indent=2)
    return normalized


def run_cycle(
    *,
    rpi_store: str | Path = DEFAULT_RPI_STORE,
    source_store: str | Path = DEFAULT_SOURCE_STORE,
    experiment_db: str | Path = DEFAULT_EXPERIMENT_DB,
    output_path: str | Path = DEFAULT_OUTPUT,
    now_ms: int | None = None,
    rpi_loader: Callable[..., RpiDataset] = load_rpi_dataset,
    frame_loader: Callable[..., Mapping[str, Sequence[RealtimeFrame]]] = load_bucketed_frames,
) -> dict[str, Any]:
    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    dataset = rpi_loader(rpi_store)
    admission = assess_rpi_data_admission(
        dataset.observations,
        now_ms=now,
        chain_valid=dataset.chain_valid,
        expected_instruments=tuple(
            f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS
        ),
    )
    if not admission.exploration_allowed:
        return _empty_report(now=now, admission=admission, output_path=output_path)
    assert admission.common_start_ms is not None and admission.common_end_ms is not None
    policy = RealtimeAlphaPolicy(
        decision_interval_ms=HOUR_MS,
        max_feature_age_ms=3 * HOUR_MS,
        max_label_lateness_ms=HOUR_MS,
        min_cross_section=8,
        min_evaluation_periods=24,
        fee_bps=4.0,
        slippage_bps=2.0,
    )
    frames = frame_loader(
        source_store,
        instruments=admission.common_instruments,
        start_ms=admission.common_start_ms,
        end_ms=admission.common_end_ms,
        bucket_ms=HOUR_MS,
        frame_cache_path=None,
    )
    enriched = enrich_frames_with_rpi(frames, dataset.observations)
    specs = default_rpi_hypotheses()
    definitions = {item.candidate_id: _definition(item) for item in specs}
    evaluations = {
        item.candidate_id: evaluate_hypothesis(item, enriched, policy=policy)
        for item in specs
    }
    dataset_version = stable_hash(
        {
            "rpi_authority_hash": dataset.authority_hash,
            "admission": admission.as_dict(),
            "manifest": [item.as_dict() for item in specs],
            "frame_view": {
                instrument: [
                    {
                        "decision_time_ms": frame.decision_time_ms,
                        "mid_price": frame.mid_price,
                        "spread_bps": frame.spread_bps,
                        "depth_imbalance_5": frame.depth_imbalance_5,
                        "rpi_notional_imbalance_1000": frame.rpi_notional_imbalance_1000,
                    }
                    for frame in rows
                ]
                for instrument, rows in sorted(enriched.items())
            },
        }
    )

    store = SqliteExperimentStore(str(experiment_db), create=False)
    registry = ExperimentRegistry(store)
    catalog = AlphaCatalog()
    try:
        registry.load_for_evaluation(catalog)
        recorded_at = max(now, int(time.time() * 1_000))
        search_id = make_search_id(
            source="tools.rpi_alpha_research",
            search_kind="RPI_DEEP_LIQUIDITY_FIXED_V1",
            dataset_version=dataset_version,
            recorded_at=recorded_at,
            params={"candidate_ids": [item.candidate_id for item in specs]},
        )
        registry.begin_search_attempt(
            search_id=search_id,
            search_kind="RPI_DEEP_LIQUIDITY_FIXED_V1",
            source="tools.rpi_alpha_research",
            dataset_version=dataset_version,
            recorded_at=recorded_at,
        )
        registry.register_search_candidates(
            search_id,
            [definitions[item.candidate_id].definition_hash for item in specs],
            registered_at=recorded_at,
        )
        for item in specs:
            definition = definitions[item.candidate_id]
            registry.record_trial_provenance(
                trial_provenance_for_definition(
                    definition,
                    research_session_id=search_id,
                    actor_id="tools.rpi_alpha_research",
                    dataset_version=dataset_version,
                    recorded_at=recorded_at,
                    model_name="deterministic-rpi-depth-v1",
                )
            )
            catalog.register(definition)
        for candidate_id, item in evaluations.items():
            catalog.set_returns(candidate_id, item.net_returns)
        trial_count = catalog.global_trial_count()
        catalog_var_sr = catalog.catalog_var_sr()
        family_pbo = _family_pbo(evaluations)
        rows = []
        for spec in specs:
            item = evaluations[spec.candidate_id]
            dsr: float | None = None
            if catalog_var_sr is not None and len(item.net_returns) >= 2:
                try:
                    dsr = deflated_sharpe_ratio(
                        _raw_observed_sr(item.net_returns),
                        len(item.net_returns),
                        max(1, trial_count),
                        catalog_var_sr,
                    )
                except (ValueError, ZeroDivisionError, ArithmeticError):
                    dsr = None
            statistical_pass, reasons = _strict_gate(
                item,
                formal_validation_allowed=admission.formal_validation_allowed,
                dsr=dsr,
                pbo=family_pbo,
            )
            row = {
                **item.as_dict(),
                "evaluation_status": item.status,
                "statistical_validation_gate_pass": statistical_pass,
                "statistical_gate_reasons": list(reasons),
                "promotion_blockers": list(REMAINING_PROMOTION_GATES),
                "deflated_sharpe": dsr,
                "pbo": family_pbo,
                "trial_count": trial_count,
                "dataset_version": dataset_version,
                "economic_hypothesis": spec.economic_hypothesis,
                "falsification_standard": spec.falsification_standard,
                "promotion_eligible": False,
                "live_allowed": False,
            }
            rows.append(row)
            evaluation = AlphaEvaluation(
                alpha_id=spec.candidate_id,
                ic_mean=item.mean_ic,
                icir=None,
                ic_tstat=item.ic_t_stat,
                ic_pvalue=None,
                mean_breadth=float(item.mean_breadth or 0.0),
                n_periods=item.n_periods,
                coverage_ok=item.status == "EVALUATED",
                net_sharpe=float(item.net_sharpe or 0.0),
                # Capacity has not been measured for this new family.  A zero
                # placeholder plus an explicit blocker is safer than copying
                # gross/net Sharpe into a field that downstream consumers may
                # mistake for executable capacity evidence.
                capacity_adj_sharpe=0.0,
                observed_sr=_raw_observed_sr(item.net_returns),
                turnover=float(item.mean_turnover or 0.0),
                deflated_sharpe=max(0.0, min(1.0, float(dsr or 0.0))),
                pbo=max(0.0, min(1.0, float(family_pbo or 0.0))),
                catalog_trial_count=max(1, trial_count),
                verdict=ValidationVerdict.REQUIRES_HUMAN_REVIEW,
                reasons=tuple(reasons) + REMAINING_PROMOTION_GATES,
                as_of=now,
                reproducibility_hash=stable_hash(
                    {"candidate": row, "dataset_version": dataset_version}
                ),
            )
            recorded_at = max(recorded_at, int(time.time() * 1_000))
            registry.record_evaluation(
                evaluation,
                dataset_version=dataset_version,
                recorded_ts=recorded_at,
                search_id=search_id,
                config_hash=definitions[spec.candidate_id].definition_hash,
            )
        recorded_at = max(recorded_at, int(time.time() * 1_000))
        registry.finish_search_attempt(
            search_id,
            status="COMPLETED",
            recorded_at=recorded_at,
        )
    finally:
        store.close()

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            not bool(row["statistical_validation_gate_pass"]),
            -float(row.get("holdout_net_sharpe") or 0.0),
            str(row["candidate_id"]),
        ),
    )
    report = {
        "schema_version": 1,
        "generated_at_ms": now,
        "status": (
            "FORMAL_HISTORICAL_RESEARCH"
            if admission.formal_validation_allowed
            else "EXPLORATORY_ONLY"
        ),
        "mode": "L2_RPI_ALPHA_RESEARCH",
        "dataset_version": dataset_version,
        "data_admission": admission.as_dict(),
        "manifest": [item.as_dict() for item in specs],
        "search_id": search_id,
        "funnel": {
            "pre_registered": len(specs),
            "evaluated": len(rows),
            "statistical_passes": sum(
                bool(row["statistical_validation_gate_pass"]) for row in rows
            ),
            "promotion_eligible": 0,
        },
        "candidates": sorted_rows,
        "global_trial_count": trial_count,
        "catalog_var_sr": catalog_var_sr,
        "remaining_promotion_gates": list(REMAINING_PROMOTION_GATES),
        "safety": {
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }
    return _publish_report(report, output_path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpi-store", default=str(DEFAULT_RPI_STORE))
    parser.add_argument("--source-store", default=str(DEFAULT_SOURCE_STORE))
    parser.add_argument("--experiment-db", default=str(DEFAULT_EXPERIMENT_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        report = run_cycle(
            rpi_store=args.rpi_store,
            source_store=args.source_store,
            experiment_db=args.experiment_db,
            output_path=args.output,
        )
    except Exception as exc:  # noqa: BLE001 - scheduled boundary fails closed
        print(f"RPI Alpha research failed: {type(exc).__name__}: {exc}")
        return SCHEDULE_EXIT_FAILURE
    print(
        "RPI Alpha research "
        f"status={report['status']} evaluated={report['funnel']['evaluated']} "
        f"statistical_passes={report['funnel']['statistical_passes']} "
        f"promotion_eligible={report['funnel']['promotion_eligible']}"
    )
    return (
        SCHEDULE_EXIT_SUCCESS
        if report["data_admission"]["exploration_allowed"]
        else SCHEDULE_EXIT_SAFE_BLOCKED
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_OUTPUT",
    "RpiAlphaResearchError",
    "load_rpi_dataset",
    "main",
    "run_cycle",
]
