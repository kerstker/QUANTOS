"""Autonomous realtime Alpha discovery scheduled worker.

The worker consumes only the retained Binance public-feature store.  It
pre-registers the fixed 24-trial microstructure/relative-value budget, builds
PIT 30-second decision frames, evaluates post-latency labels, records every
attempt in the global experiment registry, and publishes a compact operator
snapshot.  It has no credentials, broker adapter, order-submission seam, or
promotion authority.

Three days of common history unlock diagnostic exploration, 30 days unlock
the unchanged statistical gate, and 90 days unlock *data eligibility* for a
separate forward-shadow adapter.  No maturity state is itself an Alpha pass.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import time
from typing import Any, Mapping, Sequence

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
from services.features.binance_realtime_projection import (
    CAPACITY_FEATURE_ID,
    CORE_FEATURE_IDS,
    FEATURE_IDS,
    FEATURE_VERSION,
)
from services.research.alpha_family_registry import AlphaFamilyRegistry
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.realtime_alpha_discovery import (
    DAY_MS,
    DataAdmissionReport,
    FeatureCoverage,
    RealtimeAlphaPolicy,
    RealtimeFrame,
    RealtimeHypothesis,
    RealtimeHypothesisEvaluation,
    assess_data_admission,
    assess_executable_depth_capacity,
    default_realtime_hypotheses,
    evaluate_hypothesis,
)
from services.research.trial_provenance import trial_provenance_for_definition
from services.validation.deflated_sharpe import deflated_sharpe_ratio, pbo_cscv
from tools.cache_io import atomic_write_json
from tools.search_trial_registry import make_search_id


ROOT = runtime_state_root()
DEFAULT_SOURCE_STORE = ROOT / "data" / "cache" / "binance_realtime_features.db"
DEFAULT_FEATURE_STATUS = ROOT / "data" / "cache" / "binance_realtime_feature_status.json"
DEFAULT_EXPERIMENT_DB = ROOT / "data" / "cache" / "experiment_registry.db"
DEFAULT_REGISTRY_DB = ROOT / "data" / "cache" / "realtime_alpha_registry.db"
DEFAULT_OUTPUT = ROOT / "data" / "cache" / "realtime_alpha_discovery.json"
MAX_RESEARCH_LOOKBACK_MS = 90 * DAY_MS
CAPACITY_LOOKBACK_MS = 30 * DAY_MS
STRICT_DSR_MIN = 0.95
STRICT_PBO_MAX = 0.25
STRICT_HOLDOUT_SHARPE_MIN = 0.80
STRICT_WF_POSITIVE_FRAC = 0.75
STRICT_REGIME_POSITIVE_FRAC = 0.75
STRICT_MAX_DRAWDOWN = 0.15


class RealtimeAlphaWorkerError(RuntimeError):
    """The retained realtime research input or evidence store is invalid."""


def _read_quality_status(path: str | Path) -> tuple[str, dict[str, Any]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "UNAVAILABLE", {}
    if not isinstance(payload, dict):
        return "UNAVAILABLE", {}
    return str(payload.get("status", "UNAVAILABLE")).upper(), payload


def coverage_from_store(path: str | Path) -> dict[str, FeatureCoverage]:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise RealtimeAlphaWorkerError("realtime feature store is unavailable")
    try:
        connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feature_values'"
        ).fetchone()
        if table is None:
            raise RealtimeAlphaWorkerError("realtime feature store is uninitialized")
        result: dict[str, FeatureCoverage] = {}
        for feature_id in FEATURE_IDS:
            summary = connection.execute(
                "SELECT COUNT(*) AS n,MIN(event_time) AS first_event,"
                "MAX(event_time) AS last_event FROM feature_values "
                "WHERE feature_id=? AND feature_version=?",
                (feature_id, FEATURE_VERSION),
            ).fetchone()
            if summary is None or int(summary["n"] or 0) == 0:
                continue
            instruments = frozenset(
                str(row["instrument_id"])
                for row in connection.execute(
                    "SELECT DISTINCT instrument_id FROM feature_values "
                    "WHERE feature_id=? AND feature_version=? ORDER BY instrument_id",
                    (feature_id, FEATURE_VERSION),
                )
            )
            result[feature_id] = FeatureCoverage(
                feature_id=feature_id,
                row_count=int(summary["n"]),
                instruments=instruments,
                first_event_ms=int(summary["first_event"]),
                last_event_ms=int(summary["last_event"]),
            )
        return result
    except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise RealtimeAlphaWorkerError(
            f"realtime feature coverage is unreadable: {type(exc).__name__}"
        ) from exc
    finally:
        if "connection" in locals():
            connection.close()


def _complete_frame(
    instrument: str,
    *,
    event_time_ms: int,
    knowledge_time_ms: int,
    decision_time_ms: int,
    values: Mapping[str, float],
) -> RealtimeFrame:
    return RealtimeFrame(
        instrument_id=instrument,
        event_time_ms=event_time_ms,
        decision_time_ms=decision_time_ms,
        mid_price=float(values["rt_mid_price"]),
        spread_bps=float(values["rt_spread_bps"]),
        depth_imbalance_5=float(values["rt_depth_imbalance_5"]),
        mark_basis_bps=float(values["rt_mark_basis_bps"]),
        funding_rate=float(values["rt_funding_rate"]),
        taker_imbalance_5s=float(values["rt_taker_imbalance_5s"]),
        liquidation_imbalance_5s=float(values["rt_liquidation_imbalance_5s"]),
    )


def load_bucketed_frames(
    path: str | Path,
    *,
    instruments: Sequence[str],
    start_ms: int,
    end_ms: int,
    bucket_ms: int = 30_000,
) -> dict[str, tuple[RealtimeFrame, ...]]:
    """Load the last complete known snapshot in every closed decision bucket."""
    if type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms <= end_ms:
        raise ValueError("frame range is invalid")
    if type(bucket_ms) is not int or bucket_ms <= 0:
        raise ValueError("bucket_ms must be positive")
    resolved = Path(path).resolve()
    try:
        connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        result: dict[str, tuple[RealtimeFrame, ...]] = {}
        for instrument in sorted(set(instruments)):
            snapshots: dict[tuple[int, int], dict[str, float]] = {}
            for feature_id in CORE_FEATURE_IDS:
                rows = connection.execute(
                    "SELECT event_time,knowledge_time,value FROM feature_values "
                    "WHERE feature_id=? AND feature_version=? AND instrument_id=? "
                    "AND knowledge_time>=? AND knowledge_time<=? "
                    "ORDER BY knowledge_time,event_time",
                    (feature_id, FEATURE_VERSION, instrument, start_ms, end_ms),
                )
                for row in rows:
                    value = row["value"]
                    if value is None or not math.isfinite(float(value)):
                        continue
                    key = (int(row["event_time"]), int(row["knowledge_time"]))
                    snapshots.setdefault(key, {})[feature_id] = float(value)
            latest_by_bucket: dict[int, tuple[int, int, Mapping[str, float]]] = {}
            for (event_time, knowledge_time), values in snapshots.items():
                if set(values) != set(CORE_FEATURE_IDS) or knowledge_time < event_time:
                    continue
                bucket_end = (knowledge_time // bucket_ms + 1) * bucket_ms
                if bucket_end > end_ms + bucket_ms:
                    continue
                existing = latest_by_bucket.get(bucket_end)
                if existing is None or (knowledge_time, event_time) > (existing[1], existing[0]):
                    latest_by_bucket[bucket_end] = (event_time, knowledge_time, values)
            frames = tuple(
                _complete_frame(
                    instrument,
                    event_time_ms=event_time,
                    knowledge_time_ms=knowledge_time,
                    decision_time_ms=bucket_end,
                    values=values,
                )
                for bucket_end, (event_time, knowledge_time, values) in sorted(
                    latest_by_bucket.items()
                )
            )
            if frames:
                result[instrument] = frames
        return result
    except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise RealtimeAlphaWorkerError(
            f"realtime frames are unreadable: {type(exc).__name__}"
        ) from exc
    finally:
        if "connection" in locals():
            connection.close()


def load_hourly_executable_depth(
    path: str | Path,
    *,
    instruments: Sequence[str],
    start_ms: int,
    end_ms: int,
) -> dict[str, tuple[float, ...]]:
    """Load bounded hourly depth means without materializing raw tick history."""
    if type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms <= end_ms:
        raise ValueError("capacity range is invalid")
    selected = tuple(sorted(set(str(item) for item in instruments if item)))
    if not selected:
        return {}
    placeholders = ",".join("?" for _ in selected)
    query = (
        "SELECT instrument_id,CAST(event_time / 3600000 AS INTEGER) AS hour_bucket,"
        "AVG(value) AS mean_depth FROM feature_values "
        "WHERE feature_id=? AND feature_version=? AND event_time>=? AND event_time<=? "
        f"AND instrument_id IN ({placeholders}) "
        "GROUP BY instrument_id,hour_bucket ORDER BY instrument_id,hour_bucket"
    )
    resolved = Path(path).resolve()
    try:
        connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        result: dict[str, list[float]] = {}
        for row in connection.execute(
            query,
            (CAPACITY_FEATURE_ID, FEATURE_VERSION, start_ms, end_ms, *selected),
        ):
            value = row["mean_depth"]
            if value is None or not math.isfinite(float(value)) or float(value) <= 0:
                continue
            result.setdefault(str(row["instrument_id"]), []).append(float(value))
        return {key: tuple(values) for key, values in result.items()}
    except sqlite3.DatabaseError as exc:
        raise RealtimeAlphaWorkerError(
            f"executable depth evidence is unreadable: {type(exc).__name__}"
        ) from exc
    finally:
        if "connection" in locals():
            connection.close()


def _capacity_report(
    source_store: str | Path,
    *,
    coverage: Mapping[str, FeatureCoverage],
    admission: DataAdmissionReport,
    now_ms: int,
    policy: RealtimeAlphaPolicy,
):
    capacity_coverage = coverage.get(CAPACITY_FEATURE_ID)
    if capacity_coverage is None:
        return assess_executable_depth_capacity(
            {},
            history_span_ms=0,
            feature_age_ms=None,
            expected_instruments=admission.common_instruments,
            max_feature_age_ms=policy.max_feature_age_ms,
        )
    start_ms = max(
        capacity_coverage.first_event_ms,
        capacity_coverage.last_event_ms - CAPACITY_LOOKBACK_MS,
    )
    hourly = load_hourly_executable_depth(
        source_store,
        instruments=admission.common_instruments,
        start_ms=start_ms,
        end_ms=capacity_coverage.last_event_ms,
    )
    return assess_executable_depth_capacity(
        hourly,
        history_span_ms=(
            capacity_coverage.last_event_ms - capacity_coverage.first_event_ms
        ),
        feature_age_ms=max(0, now_ms - capacity_coverage.last_event_ms),
        expected_instruments=admission.common_instruments,
        max_feature_age_ms=policy.max_feature_age_ms,
    )


def _definition(spec: RealtimeHypothesis) -> AlphaDefinition:
    return AlphaDefinition(
        alpha_id=spec.candidate_id,
        name=f"Realtime {spec.mechanism} {_horizon_text(spec.horizon_ms)}",
        factor_ids=tuple(sorted(spec.required_features)),
        universe="binance_usdm_14_realtime_public",
        params={
            "family_id": spec.family_id,
            "mechanism": spec.mechanism,
            "horizon_ms": str(spec.horizon_ms),
            "signal_version": spec.signal_version,
            "label_policy": "post_latency_first_known_mid",
        },
    )


def _horizon_text(value: int) -> str:
    if value % 3_600_000 == 0:
        return f"{value // 3_600_000}h"
    if value % 60_000 == 0:
        return f"{value // 60_000}m"
    return f"{value // 1_000}s"


def _candidate_payload(spec: RealtimeHypothesis, definition: AlphaDefinition) -> dict[str, Any]:
    identity = spec.as_dict()
    # JSON persistence normalizes tuples to lists.  Normalize before the first
    # append as well so a later process sees byte-for-byte immutable identity
    # instead of a Python tuple-vs-JSON-list false conflict.
    identity["required_features"] = list(spec.required_features)
    return {
        **identity,
        "config_hash": definition.definition_hash,
        "intended_frequency": "30s_to_8h",
        "evaluation_frequency": "realtime_pit_30s_buckets",
        "paper_eligible": False,
        "notes": "PIT public-data research; 30d validation and 90d shadow maturity remain mandatory",
    }


def _raw_observed_sr(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    deviation = statistics.pstdev(values)
    return statistics.fmean(values) / deviation if deviation > 0 else 0.0


def _pbo_by_horizon(
    evaluations: Mapping[str, RealtimeHypothesisEvaluation],
) -> dict[str, float | None]:
    grouped: dict[int, list[RealtimeHypothesisEvaluation]] = {}
    for item in evaluations.values():
        if item.status == "EVALUATED":
            grouped.setdefault(item.horizon_ms, []).append(item)
    result: dict[str, float | None] = {}
    for items in grouped.values():
        if len(items) < 2:
            result.update({item.candidate_id: None for item in items})
            continue
        streams = {
            item.candidate_id: dict(zip(item.event_times, item.net_returns, strict=True))
            for item in items
        }
        common = set(next(iter(streams.values())))
        for stream in streams.values():
            common.intersection_update(stream)
        times = sorted(common)
        value: float | None = None
        if len(times) >= 16:
            block_count = min(8, len(times) // 2)
            matrix = []
            for block in range(block_count):
                lower = len(times) * block // block_count
                upper = len(times) * (block + 1) // block_count
                matrix.append(
                    [
                        statistics.fmean(streams[item.candidate_id][event] for event in times[lower:upper])
                        for item in items
                    ]
                )
            try:
                value = pbo_cscv(matrix)
            except (ValueError, ZeroDivisionError, ArithmeticError):
                value = None
        result.update({item.candidate_id: value for item in items})
    return result


def _gate_result(
    item: RealtimeHypothesisEvaluation,
    *,
    admission: DataAdmissionReport,
    dsr: float | None,
    pbo: float | None,
    capacity,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not admission.formal_validation_allowed:
        reasons.append("REALTIME_HISTORY_BELOW_30D")
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
    # Evidence maturity is load-bearing.  The $1m target remains a separate
    # deployment-size verdict: a real but low-capacity edge may still enter a
    # capacity-sized, non-promotable forward shadow account.
    reasons.extend(capacity.formal_gate_reasons)
    return not reasons, reasons


def _dataset_version(
    coverage: Mapping[str, FeatureCoverage], hypotheses: Sequence[RealtimeHypothesis]
) -> str:
    return stable_hash(
        {
            "feature_version": FEATURE_VERSION,
            "coverage": {
                feature_id: {
                    "rows": item.row_count,
                    "instruments": sorted(item.instruments),
                    "first": item.first_event_ms,
                    "last": item.last_event_ms,
                }
                for feature_id, item in sorted(coverage.items())
            },
            "hypotheses": [item.as_dict() for item in hypotheses],
        }
    )


def _display_path(path: str | Path) -> str:
    try:
        return os.path.relpath(str(path), ROOT)
    except ValueError:
        return str(Path(path))


def run_cycle(
    *,
    source_store: str | Path = DEFAULT_SOURCE_STORE,
    feature_status: str | Path = DEFAULT_FEATURE_STATUS,
    experiment_db: str | Path = DEFAULT_EXPERIMENT_DB,
    registry_db: str | Path = DEFAULT_REGISTRY_DB,
    output_path: str | Path = DEFAULT_OUTPUT,
    now_ms: int | None = None,
    policy: RealtimeAlphaPolicy | None = None,
    persist_trials: bool = True,
) -> dict[str, Any]:
    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    policy = policy or RealtimeAlphaPolicy()
    hypotheses = default_realtime_hypotheses()
    definitions = {item.candidate_id: _definition(item) for item in hypotheses}
    quality_status, quality_payload = _read_quality_status(feature_status)
    coverage = coverage_from_store(source_store)
    admission = assess_data_admission(
        coverage, now_ms=now, quality_status=quality_status, policy=policy
    )
    capacity = _capacity_report(
        source_store,
        coverage=coverage,
        admission=admission,
        now_ms=now,
        policy=policy,
    )
    dataset_version = _dataset_version(coverage, hypotheses)

    registry = AlphaFamilyRegistry(str(registry_db), create=True)
    exp_store: SqliteExperimentStore | None = None
    try:
        for item in hypotheses:
            registry.register_candidate(
                _candidate_payload(item, definitions[item.candidate_id]),
                recorded_at=now,
            )

        evaluations: dict[str, RealtimeHypothesisEvaluation] = {}
        if admission.exploration_allowed:
            assert admission.common_start_ms is not None and admission.common_end_ms is not None
            start_ms = max(
                admission.common_start_ms,
                admission.common_end_ms - MAX_RESEARCH_LOOKBACK_MS,
            )
            frames = load_bucketed_frames(
                source_store,
                instruments=admission.common_instruments,
                start_ms=start_ms,
                end_ms=admission.common_end_ms,
                bucket_ms=policy.decision_interval_ms,
            )
            evaluations = {
                item.candidate_id: evaluate_hypothesis(item, frames, policy=policy)
                for item in hypotheses
            }

        exp_registry: ExperimentRegistry | None = None
        search_id: str | None = None
        persistence_recorded_at = now
        catalog = AlphaCatalog()
        if evaluations and persist_trials:
            exp_store = SqliteExperimentStore(str(experiment_db), create=False)
            exp_registry = ExperimentRegistry(exp_store)
            exp_registry.load_for_evaluation(catalog)
            # `now` is the market-data decision cutoff, captured before frame
            # loading and evaluation.  Durable ledger events are written only
            # afterwards and must use the persistence wall clock; reusing the
            # earlier as-of cutoff causes a legitimate global-clock rollback
            # whenever another research worker appends during computation.
            persistence_recorded_at = max(now, int(time.time() * 1_000))
            search_id = make_search_id(
                source="realtime_alpha_discovery",
                search_kind="realtime_microstructure_budget_v1",
                dataset_version=dataset_version,
                recorded_at=persistence_recorded_at,
                params={"candidate_ids": [item.candidate_id for item in hypotheses]},
            )
            exp_registry.begin_search_attempt(
                search_id=search_id,
                search_kind="realtime_microstructure_budget_v1",
                source="tools.realtime_alpha_discovery",
                dataset_version=dataset_version,
                recorded_at=persistence_recorded_at,
            )
            exp_registry.register_search_candidates(
                search_id,
                [definitions[item.candidate_id].definition_hash for item in hypotheses],
                registered_at=persistence_recorded_at,
            )
            for item in hypotheses:
                definition = definitions[item.candidate_id]
                exp_registry.record_trial_provenance(
                    trial_provenance_for_definition(
                        definition,
                        research_session_id=search_id,
                        actor_id="tools.realtime_alpha_discovery",
                        dataset_version=dataset_version,
                        recorded_at=persistence_recorded_at,
                        model_name="deterministic-realtime-family-budget-v1",
                    )
                )
                catalog.register(definition)
        elif evaluations:
            for item in hypotheses:
                catalog.register(definitions[item.candidate_id])

        for candidate_id, item in evaluations.items():
            catalog.set_returns(candidate_id, item.net_returns)
        trial_count = catalog.global_trial_count() if evaluations else 0
        catalog_var_sr = catalog.catalog_var_sr() if evaluations else None
        pbo_values = _pbo_by_horizon(evaluations)
        result_rows: list[dict[str, Any]] = []
        for spec in hypotheses:
            item = evaluations.get(spec.candidate_id)
            if item is None:
                continue
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
            pbo = pbo_values.get(spec.candidate_id)
            hard_pass, gate_reasons = _gate_result(
                item,
                admission=admission,
                dsr=dsr,
                pbo=pbo,
                capacity=capacity,
            )
            if hard_pass and admission.shadow_admission_allowed:
                status = "SHADOW_CANDIDATE_READY"
            elif hard_pass:
                status = "VALIDATED_AWAITING_90D_SHADOW_MATURITY"
            elif admission.formal_validation_allowed:
                status = "REJECTED_BY_FORMAL_GATE"
            else:
                status = "EXPLORATORY_ONLY"
            row = {
                **item.as_dict(),
                "status": status,
                "verdict": "PASS" if hard_pass else "REQUIRES_HUMAN_REVIEW",
                "gate_pass": hard_pass,
                "gate_reasons": gate_reasons,
                "deflated_sharpe": dsr,
                "pbo": pbo,
                "trial_count": trial_count,
                "dataset_version": dataset_version,
                "economic_hypothesis": spec.economic_hypothesis,
                "falsification_standard": spec.falsification_standard,
                "promotion_eligible": False,
                "live_allowed": False,
            }
            result_rows.append(row)
            registry.record_evaluation(
                spec.candidate_id,
                row,
                dataset_version=dataset_version,
                recorded_at=now,
            )
            if exp_registry is not None and search_id is not None:
                persistence_recorded_at = max(
                    persistence_recorded_at,
                    int(time.time() * 1_000),
                )
                evaluation = AlphaEvaluation(
                    alpha_id=spec.candidate_id,
                    ic_mean=item.mean_ic,
                    icir=None,
                    ic_tstat=item.ic_t_stat,
                    ic_pvalue=None,
                    # AlphaEvaluation uses numeric zero together with
                    # coverage_ok=False for an honestly unassessed metric;
                    # exploratory candidates with insufficient horizon data
                    # must still be retained without fabricating evidence or
                    # crashing the whole autonomous cycle.
                    mean_breadth=float(item.mean_breadth or 0.0),
                    n_periods=item.n_periods,
                    coverage_ok=item.status == "EVALUATED",
                    net_sharpe=float(item.net_sharpe or 0.0),
                    capacity_adj_sharpe=float(item.net_sharpe or 0.0),
                    observed_sr=_raw_observed_sr(item.net_returns),
                    turnover=float(item.mean_turnover or 0.0),
                    deflated_sharpe=max(0.0, min(1.0, float(dsr or 0.0))),
                    pbo=max(0.0, min(1.0, float(pbo or 0.0))),
                    catalog_trial_count=max(1, trial_count),
                    verdict=(
                        ValidationVerdict.PASS
                        if hard_pass
                        else ValidationVerdict.REQUIRES_HUMAN_REVIEW
                    ),
                    reasons=tuple(gate_reasons),
                    as_of=now,
                    reproducibility_hash=stable_hash(
                        {"candidate": row, "dataset_version": dataset_version}
                    ),
                )
                exp_registry.record_evaluation(
                    evaluation,
                    dataset_version=dataset_version,
                    recorded_ts=persistence_recorded_at,
                    search_id=search_id,
                    config_hash=definitions[spec.candidate_id].definition_hash,
                )
        if exp_registry is not None and search_id is not None:
            persistence_recorded_at = max(
                persistence_recorded_at,
                int(time.time() * 1_000),
            )
            exp_registry.finish_search_attempt(
                search_id,
                status="COMPLETED",
                recorded_at=persistence_recorded_at,
            )

        sorted_rows = sorted(
            result_rows,
            key=lambda row: (
                not bool(row.get("diagnostic_lead")),
                -float(row.get("holdout_net_sharpe") or 0.0),
                str(row.get("candidate_id")),
            ),
        )
        remaining_gates: list[str] = []
        if not admission.formal_validation_allowed:
            remaining_gates.append("30D_COMMON_HISTORY_FOR_FORMAL_VALIDATION")
        if not admission.shadow_admission_allowed:
            remaining_gates.append("90D_COMMON_HISTORY_FOR_FORWARD_SHADOW")
        if not capacity.evidence_ready:
            remaining_gates.append("EXECUTABLE_DEPTH_CAPACITY_CALIBRATION")
        report = {
            "schema_version": 1,
            "generated_at_ms": now,
            "status": (
                "RUNNING_EXPLORATORY"
                if admission.status == "EXPLORATION_READY"
                else "RUNNING_FORMAL_VALIDATION"
                if admission.formal_validation_allowed
                else "COLLECTING_OR_BLOCKED"
            ),
            "mode": "L2_AUTONOMOUS_REALTIME_ALPHA_RESEARCH",
            "dataset_version": dataset_version,
            "search_id": search_id,
            "manifest_hash": stable_hash([item.as_dict() for item in hypotheses]),
            "autonomy": {
                "worker": "tools.realtime_alpha_discovery",
                "scheduler_task": "QUANTOS_realtime_alpha_discovery",
                "autonomous_search_running": True,
                "requires_developer_ai": False,
                "next_cycle_expected_ms": now + 60 * 60_000,
            },
            "data_admission": admission.as_dict(),
            "executable_depth_capacity": capacity.as_dict(),
            "feature_quality": {
                "status": quality_status,
                "generated_at_ms": quality_payload.get("generated_at_ms"),
                "drift": quality_payload.get("drift"),
            },
            "funnel": {
                "pre_registered": len(hypotheses),
                "evaluated": len(result_rows),
                "diagnostic_leads": sum(bool(row.get("diagnostic_lead")) for row in result_rows),
                "formal_passes": sum(bool(row.get("gate_pass")) for row in result_rows),
                "shadow_candidates": sum(row.get("status") == "SHADOW_CANDIDATE_READY" for row in result_rows),
            },
            "families": [
                {
                    "family_id": family,
                    "priority": "P1",
                    "hypothesis_count": sum(item.family_id == family for item in hypotheses),
                }
                for family in ("order_flow", "order_book", "liquidation", "funding_basis")
            ],
            "candidates": sorted_rows,
            "top_candidates": sorted_rows[:8],
            "global_trial_count": trial_count,
            "catalog_var_sr": catalog_var_sr,
            "registry": {
                "path": _display_path(registry_db),
                "chain_valid": registry.verify_chain(),
                "event_count": registry.event_count(),
            },
            "remaining_gates": remaining_gates,
            "safety": {
                "paper_only": True,
                "live_trading_enabled": False,
                "real_broker_enabled": False,
                "allow_order_submission": False,
                "allow_ai_direct_trading": False,
                "promotion_eligible": False,
                "credentials_accessed": False,
            },
        }
        atomic_write_json(str(output_path), report, indent=2)
        return report
    finally:
        if exp_store is not None:
            exp_store.close()
        registry.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run autonomous realtime Alpha discovery")
    parser.add_argument("--source-store", default=str(DEFAULT_SOURCE_STORE))
    parser.add_argument("--feature-status", default=str(DEFAULT_FEATURE_STATUS))
    parser.add_argument("--experiment-db", default=str(DEFAULT_EXPERIMENT_DB))
    parser.add_argument("--registry-db", default=str(DEFAULT_REGISTRY_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        report = run_cycle(
            source_store=args.source_store,
            feature_status=args.feature_status,
            experiment_db=args.experiment_db,
            registry_db=args.registry_db,
            output_path=args.output,
        )
    except Exception as exc:
        print(f"realtime alpha discovery failed: {type(exc).__name__}: {exc}")
        return SCHEDULE_EXIT_FAILURE
    print(
        "realtime alpha discovery "
        f"status={report['status']} evaluated={report['funnel']['evaluated']} "
        f"leads={report['funnel']['diagnostic_leads']} passes={report['funnel']['formal_passes']}"
    )
    if not report["data_admission"]["exploration_allowed"]:
        return SCHEDULE_EXIT_SAFE_BLOCKED
    return SCHEDULE_EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_OUTPUT",
    "RealtimeAlphaWorkerError",
    "coverage_from_store",
    "load_hourly_executable_depth",
    "load_bucketed_frames",
    "main",
    "run_cycle",
]
