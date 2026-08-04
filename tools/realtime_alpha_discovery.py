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
from services.research.realtime_alpha_feedback import (
    FeedbackProposal,
    build_feedback_plan,
)
from services.research.realtime_alpha_handoff import (
    canonical_realtime_research_policy,
    realtime_research_policy_hash,
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
MAX_ACTIVE_ADAPTIVE_HYPOTHESES = 8


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


def _query_bucketed_frames(
    path: str | Path,
    *,
    instruments: Sequence[str],
    start_ms: int,
    end_ms: int,
    bucket_ms: int = 30_000,
) -> dict[str, tuple[RealtimeFrame, ...]]:
    """Load the last complete known snapshot in every closed decision bucket.

    The feature store is intentionally append-only and can contain many raw
    observations inside one 30-second decision bucket.  The old implementation
    materialized every one of those rows in Python and only then selected the
    latest bucket observation.  That made a normal discovery cycle reread
    millions of values and left the real-time freshness gate racing the worker
    runtime.

    Bucket selection is now performed by SQLite: first identify complete
    seven-feature snapshots, then rank one snapshot per instrument/bucket by
    ``knowledge_time`` and ``event_time``.  Only the selected rows cross the
    Python boundary.  The PIT rules are unchanged: values are selected by
    knowledge time, the snapshot must be complete, and the resulting decision
    frame is still constructed only from observations known by that decision.
    """
    if type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms <= end_ms:
        raise ValueError("frame range is invalid")
    if type(bucket_ms) is not int or bucket_ms <= 0:
        raise ValueError("bucket_ms must be positive")
    selected_instruments = tuple(sorted(set(str(item) for item in instruments)))
    if not selected_instruments:
        return {}
    selected_features = tuple(str(item) for item in CORE_FEATURE_IDS)
    feature_placeholders = ",".join("?" for _ in selected_features)
    instrument_placeholders = ",".join("?" for _ in selected_instruments)
    resolved = Path(path).resolve()
    try:
        connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        result: dict[str, tuple[RealtimeFrame, ...]] = {}
        query = f"""
            WITH complete_snapshots AS (
                SELECT
                    instrument_id,
                    event_time,
                    knowledge_time,
                    (CAST(knowledge_time / ? AS INTEGER) + 1) * ? AS decision_time
                FROM feature_values
                WHERE feature_version=?
                  AND feature_id IN ({feature_placeholders})
                  AND instrument_id IN ({instrument_placeholders})
                  AND knowledge_time>=?
                  AND knowledge_time<=?
                  AND event_time<=knowledge_time
                  AND value IS NOT NULL
                GROUP BY instrument_id,event_time,knowledge_time
                HAVING COUNT(DISTINCT feature_id)=?
            ), ranked_snapshots AS (
                SELECT
                    instrument_id,
                    event_time,
                    knowledge_time,
                    decision_time,
                    ROW_NUMBER() OVER (
                        PARTITION BY instrument_id,decision_time
                        ORDER BY knowledge_time DESC,event_time DESC
                    ) AS row_number
                FROM complete_snapshots
                WHERE decision_time<=?
            )
            SELECT
                ranked.instrument_id,
                ranked.event_time,
                ranked.knowledge_time,
                ranked.decision_time,
                fv.feature_id,
                fv.value
            FROM ranked_snapshots AS ranked
            JOIN feature_values AS fv
              ON fv.instrument_id=ranked.instrument_id
             AND fv.event_time=ranked.event_time
             AND fv.knowledge_time=ranked.knowledge_time
             AND fv.feature_version=?
             AND fv.feature_id IN ({feature_placeholders})
            WHERE ranked.row_number=1
            ORDER BY ranked.instrument_id,ranked.decision_time,
                     ranked.knowledge_time,ranked.event_time,fv.feature_id
        """
        parameters = (
            bucket_ms,
            bucket_ms,
            FEATURE_VERSION,
            *selected_features,
            *selected_instruments,
            start_ms,
            end_ms,
            len(selected_features),
            end_ms + bucket_ms,
            FEATURE_VERSION,
            *selected_features,
        )
        current_key: tuple[str, int, int, int] | None = None
        current_values: dict[str, float] = {}
        frames_by_instrument: dict[str, list[RealtimeFrame]] = {}

        def flush_current() -> None:
            if current_key is None or set(current_values) != set(selected_features):
                return
            instrument, event_time, knowledge_time, decision_time = current_key
            if knowledge_time < event_time:
                return
            try:
                frame = _complete_frame(
                    instrument,
                    event_time_ms=event_time,
                    knowledge_time_ms=knowledge_time,
                    decision_time_ms=decision_time,
                    values=current_values,
                )
            except (KeyError, TypeError, ValueError):
                return
            frames_by_instrument.setdefault(instrument, []).append(frame)

        for row in connection.execute(query, parameters):
            key = (
                str(row["instrument_id"]),
                int(row["event_time"]),
                int(row["knowledge_time"]),
                int(row["decision_time"]),
            )
            if current_key != key:
                flush_current()
                current_key = key
                current_values = {}
            value = row["value"]
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric):
                current_values[str(row["feature_id"])] = numeric
        flush_current()
        result = {
            instrument: tuple(rows)
            for instrument, rows in frames_by_instrument.items()
            if rows
        }
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


def _manifest_row(
    spec: RealtimeHypothesis,
    lineage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row = spec.as_dict()
    row["required_features"] = list(spec.required_features)
    if lineage is not None:
        row.update(dict(lineage))
    return row


def _adaptive_hypothesis(
    row: Mapping[str, Any],
) -> tuple[RealtimeHypothesis, dict[str, Any]] | None:
    if row.get("adaptive_feedback") is not True:
        return None
    required = row.get("required_features")
    cutoff = row.get("feedback_cutoff_ms")
    if (
        not isinstance(required, list)
        or not required
        or not all(isinstance(item, str) and item for item in required)
        or type(cutoff) is not int
        or cutoff < 0
        or row.get("fresh_oos_required") is not True
        or row.get("adaptive_generation") != 1
        or not isinstance(row.get("parent_candidate_id"), str)
        or not isinstance(row.get("mutation"), str)
        or row.get("signal_version") != "realtime-microstructure.feedback.v1"
    ):
        raise RealtimeAlphaWorkerError("adaptive candidate registration is invalid")
    try:
        spec = RealtimeHypothesis(
            candidate_id=str(row["candidate_id"]),
            family_id=str(row["family_id"]),
            mechanism=str(row["mechanism"]),
            horizon_ms=int(row["horizon_ms"]),
            required_features=tuple(required),
            economic_hypothesis=str(row["economic_hypothesis"]),
            falsification_standard=str(row["falsification_standard"]),
            signal_version=str(row["signal_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RealtimeAlphaWorkerError("adaptive candidate definition is invalid") from exc
    lineage = {
        "adaptive_feedback": True,
        "parent_candidate_id": str(row["parent_candidate_id"]),
        "feedback_cutoff_ms": cutoff,
        "mutation": str(row["mutation"]),
        "fresh_oos_required": True,
        "adaptive_generation": 1,
    }
    return spec, lineage


def _proposal_lineage(proposal: FeedbackProposal) -> dict[str, Any]:
    return {
        "adaptive_feedback": True,
        "parent_candidate_id": proposal.parent_candidate_id,
        "feedback_cutoff_ms": proposal.feedback_cutoff_ms,
        "mutation": proposal.mutation,
        "fresh_oos_required": proposal.fresh_oos_required,
        "adaptive_generation": proposal.adaptive_generation,
    }


def _definition(
    spec: RealtimeHypothesis,
    lineage: Mapping[str, Any] | None = None,
) -> AlphaDefinition:
    params = {
        "family_id": spec.family_id,
        "mechanism": spec.mechanism,
        "horizon_ms": str(spec.horizon_ms),
        "signal_version": spec.signal_version,
        "label_policy": "post_latency_first_known_mid",
    }
    if lineage is not None:
        params.update(
            {
                "adaptive_feedback": "true",
                "parent_candidate_id": str(lineage["parent_candidate_id"]),
                "feedback_cutoff_ms": str(lineage["feedback_cutoff_ms"]),
                "mutation": str(lineage["mutation"]),
                "fresh_oos_required": "true",
            }
        )
    return AlphaDefinition(
        alpha_id=spec.candidate_id,
        name=f"Realtime {spec.mechanism} {_horizon_text(spec.horizon_ms)}",
        factor_ids=tuple(sorted(spec.required_features)),
        universe="binance_usdm_14_realtime_public",
        params=params,
    )


def _horizon_text(value: int) -> str:
    if value % 3_600_000 == 0:
        return f"{value // 3_600_000}h"
    if value % 60_000 == 0:
        return f"{value // 60_000}m"
    return f"{value // 1_000}s"


def _candidate_payload(
    spec: RealtimeHypothesis,
    definition: AlphaDefinition,
    lineage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    identity = _manifest_row(spec, lineage)
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


def _frame_cache_connect(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(resolved), timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_frames (
            instrument_id TEXT NOT NULL,
            decision_time INTEGER NOT NULL,
            event_time INTEGER NOT NULL,
            mid_price REAL NOT NULL,
            spread_bps REAL NOT NULL,
            depth_imbalance_5 REAL NOT NULL,
            mark_basis_bps REAL NOT NULL,
            funding_rate REAL NOT NULL,
            taker_imbalance_5s REAL NOT NULL,
            liquidation_imbalance_5s REAL NOT NULL,
            PRIMARY KEY (instrument_id, decision_time)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_decision_frames_time "
        "ON decision_frames (decision_time, instrument_id)"
    )
    connection.commit()
    return connection


def _frame_cache_bounds(
    connection: sqlite3.Connection,
    *,
    instruments: Sequence[str],
) -> tuple[int | None, int | None]:
    selected = tuple(sorted(set(str(item) for item in instruments)))
    if not selected:
        return None, None
    placeholders = ",".join("?" for _ in selected)
    row = connection.execute(
        f"SELECT MIN(decision_time),MAX(decision_time) FROM decision_frames "
        f"WHERE instrument_id IN ({placeholders})",
        selected,
    ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None, None
    return int(row[0]), int(row[1])


def _read_frame_cache(
    connection: sqlite3.Connection,
    *,
    instruments: Sequence[str],
    start_ms: int,
    end_ms: int,
) -> dict[str, tuple[RealtimeFrame, ...]]:
    selected = tuple(sorted(set(str(item) for item in instruments)))
    if not selected:
        return {}
    placeholders = ",".join("?" for _ in selected)
    rows = connection.execute(
        f"""
        SELECT instrument_id,decision_time,event_time,mid_price,spread_bps,
               depth_imbalance_5,mark_basis_bps,funding_rate,
               taker_imbalance_5s,liquidation_imbalance_5s
        FROM decision_frames
        WHERE instrument_id IN ({placeholders})
          AND decision_time>=? AND decision_time<=?
        ORDER BY instrument_id,decision_time
        """,
        (*selected, start_ms, end_ms),
    )
    result: dict[str, list[RealtimeFrame]] = {}
    for row in rows:
        frame = RealtimeFrame(
            instrument_id=str(row["instrument_id"]),
            event_time_ms=int(row["event_time"]),
            decision_time_ms=int(row["decision_time"]),
            mid_price=float(row["mid_price"]),
            spread_bps=float(row["spread_bps"]),
            depth_imbalance_5=float(row["depth_imbalance_5"]),
            mark_basis_bps=float(row["mark_basis_bps"]),
            funding_rate=float(row["funding_rate"]),
            taker_imbalance_5s=float(row["taker_imbalance_5s"]),
            liquidation_imbalance_5s=float(row["liquidation_imbalance_5s"]),
        )
        result.setdefault(frame.instrument_id, []).append(frame)
    return {key: tuple(value) for key, value in result.items()}


def _write_frame_cache(
    connection: sqlite3.Connection,
    frames: Mapping[str, Sequence[RealtimeFrame]],
) -> None:
    rows = [
        (
            frame.instrument_id,
            frame.decision_time_ms,
            frame.event_time_ms,
            frame.mid_price,
            frame.spread_bps,
            frame.depth_imbalance_5,
            frame.mark_basis_bps,
            frame.funding_rate,
            frame.taker_imbalance_5s,
            frame.liquidation_imbalance_5s,
        )
        for instrument_frames in frames.values()
        for frame in instrument_frames
    ]
    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO decision_frames (
            instrument_id,decision_time,event_time,mid_price,spread_bps,
            depth_imbalance_5,mark_basis_bps,funding_rate,
            taker_imbalance_5s,liquidation_imbalance_5s
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(instrument_id,decision_time) DO UPDATE SET
            event_time=excluded.event_time,
            mid_price=excluded.mid_price,
            spread_bps=excluded.spread_bps,
            depth_imbalance_5=excluded.depth_imbalance_5,
            mark_basis_bps=excluded.mark_basis_bps,
            funding_rate=excluded.funding_rate,
            taker_imbalance_5s=excluded.taker_imbalance_5s,
            liquidation_imbalance_5s=excluded.liquidation_imbalance_5s
        """,
        rows,
    )
    connection.commit()


def load_bucketed_frames(
    path: str | Path,
    *,
    instruments: Sequence[str],
    start_ms: int,
    end_ms: int,
    bucket_ms: int = 30_000,
    frame_cache_path: str | Path | None = None,
    max_cache_lag_ms: int = 15 * 60_000,
) -> dict[str, tuple[RealtimeFrame, ...]]:
    """Load PIT frames, optionally using a replaceable derived bucket cache.

    The cache is not evidence and never drives promotion or order authority.
    It only prevents each discovery cycle from repeating the historical SQL
    reduction.  A cold cache performs the verified reduction once; warm cycles
    refresh only the recent tail and read the historical buckets directly.
    """
    if frame_cache_path is None:
        return _query_bucketed_frames(
            path,
            instruments=instruments,
            start_ms=start_ms,
            end_ms=end_ms,
            bucket_ms=bucket_ms,
        )
    if type(max_cache_lag_ms) is not int or max_cache_lag_ms < 0:
        raise ValueError("max_cache_lag_ms must be a non-negative integer")
    connection = _frame_cache_connect(frame_cache_path)
    try:
        cache_min, cache_max = _frame_cache_bounds(
            connection,
            instruments=instruments,
        )
        first_decision_time = (start_ms // bucket_ms + 1) * bucket_ms
        needs_history = cache_min is None or cache_min > first_decision_time
        cache_is_recent = (
            cache_max is not None
            and cache_max >= end_ms - max_cache_lag_ms
        )
        if not needs_history and cache_is_recent:
            # A recent cache is already within the same freshness contract as
            # the feature admission gate.  Avoid a full feature-table scan on
            # every discovery cycle; the next stale boundary will trigger a
            # bounded refresh and rebuild only when required.
            return _read_frame_cache(
                connection,
                instruments=instruments,
                start_ms=start_ms,
                end_ms=end_ms + bucket_ms,
            )
        if needs_history:
            refresh_start = start_ms
        elif cache_max is None or cache_max < end_ms:
            refresh_start = max(start_ms, cache_max - (2 * bucket_ms))
        else:
            # Refresh the tail even when the requested range is already
            # covered, because a newer PIT observation may replace a bucket.
            refresh_start = max(start_ms, end_ms - (2 * bucket_ms))
        refreshed = _query_bucketed_frames(
            path,
            instruments=instruments,
            start_ms=refresh_start,
            end_ms=end_ms,
            bucket_ms=bucket_ms,
        )
        _write_frame_cache(connection, refreshed)
        return _read_frame_cache(
            connection,
            instruments=instruments,
            start_ms=start_ms,
            end_ms=end_ms + bucket_ms,
        )
    finally:
        connection.close()


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
    coverage: Mapping[str, FeatureCoverage],
    manifest_rows: Sequence[Mapping[str, Any]],
    *,
    research_policy_hash: str,
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
            "hypotheses": [dict(item) for item in manifest_rows],
            "research_policy_hash": research_policy_hash,
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
    frame_cache_path: str | Path | None = None,
    now_ms: int | None = None,
    policy: RealtimeAlphaPolicy | None = None,
    persist_trials: bool = True,
) -> dict[str, Any]:
    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    policy = policy or RealtimeAlphaPolicy()
    effective_frame_cache_path = (
        Path(frame_cache_path)
        if frame_cache_path is not None
        else Path(source_store).with_name("realtime_decision_frames.db")
    )
    research_policy = canonical_realtime_research_policy(
        policy,
        feature_version=FEATURE_VERSION,
    )
    research_policy_hash = realtime_research_policy_hash(research_policy)
    base_hypotheses = default_realtime_hypotheses()
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
    registry = AlphaFamilyRegistry(str(registry_db), create=True)
    exp_store: SqliteExperimentStore | None = None
    try:
        if not registry.verify_chain():
            raise RealtimeAlphaWorkerError("Alpha family registry chain is invalid")
        registrations = registry.candidate_definitions()
        existing_ids = {
            str(row.get("candidate_id"))
            for row in registrations
            if isinstance(row.get("candidate_id"), str)
        }
        adaptive: list[RealtimeHypothesis] = []
        lineage_by_id: dict[str, dict[str, Any]] = {}
        base_by_id = {item.candidate_id: item for item in base_hypotheses}
        for row in registrations:
            parsed = _adaptive_hypothesis(row)
            if parsed is None:
                continue
            spec, lineage = parsed
            parent = base_by_id.get(str(lineage["parent_candidate_id"]))
            if (
                parent is None
                or spec.family_id != parent.family_id
                or spec.mechanism != parent.mechanism
                or spec.required_features != parent.required_features
            ):
                raise RealtimeAlphaWorkerError("adaptive candidate lineage is invalid")
            adaptive.append(spec)
            lineage_by_id[spec.candidate_id] = lineage
        if len(adaptive) > MAX_ACTIVE_ADAPTIVE_HYPOTHESES:
            raise RealtimeAlphaWorkerError("adaptive candidate budget is exceeded")

        paper_runs = {
            item.candidate_id: registry.paper_runs(item.candidate_id)
            for item in base_hypotheses
            if item.candidate_id in existing_ids
        }
        feedback = build_feedback_plan(
            base_hypotheses,
            paper_runs,
            existing_candidate_ids=tuple(existing_ids),
            max_new=max(0, MAX_ACTIVE_ADAPTIVE_HYPOTHESES - len(adaptive)),
            min_realizations=8,
            decision_interval_ms=policy.decision_interval_ms,
        )
        for proposal in feedback.proposals:
            adaptive.append(proposal.spec)
            lineage_by_id[proposal.spec.candidate_id] = _proposal_lineage(proposal)
        hypotheses = tuple(base_hypotheses) + tuple(
            sorted(adaptive, key=lambda item: item.candidate_id)
        )
        if len({item.candidate_id for item in hypotheses}) != len(hypotheses):
            raise RealtimeAlphaWorkerError("active hypothesis identities are duplicated")
        manifest_rows = [
            _manifest_row(item, lineage_by_id.get(item.candidate_id))
            for item in hypotheses
        ]
        definitions = {
            item.candidate_id: _definition(
                item, lineage_by_id.get(item.candidate_id)
            )
            for item in hypotheses
        }
        dataset_version = _dataset_version(
            coverage,
            manifest_rows,
            research_policy_hash=research_policy_hash,
        )

        for item in hypotheses:
            registry.register_candidate(
                _candidate_payload(
                    item,
                    definitions[item.candidate_id],
                    lineage_by_id.get(item.candidate_id),
                ),
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
                frame_cache_path=effective_frame_cache_path,
                max_cache_lag_ms=policy.max_feature_age_ms,
            )
            evaluations = {}
            for item in hypotheses:
                lineage = lineage_by_id.get(item.candidate_id)
                candidate_frames = frames
                if lineage is not None:
                    cutoff = int(lineage["feedback_cutoff_ms"])
                    candidate_frames = {
                        instrument: tuple(
                            frame for frame in rows if frame.decision_time_ms > cutoff
                        )
                        for instrument, rows in frames.items()
                    }
                evaluations[item.candidate_id] = evaluate_hypothesis(
                    item, candidate_frames, policy=policy
                )

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
                search_kind="realtime_microstructure_adaptive_budget_v2",
                dataset_version=dataset_version,
                recorded_at=persistence_recorded_at,
                params={
                    "candidate_ids": [item.candidate_id for item in hypotheses],
                    "feedback_hash": stable_hash(feedback.as_dict()),
                },
            )
            exp_registry.begin_search_attempt(
                search_id=search_id,
                search_kind="realtime_microstructure_adaptive_budget_v2",
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
                        model_name="deterministic-realtime-causal-feedback-v2",
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
            lineage = lineage_by_id.get(spec.candidate_id)
            fresh_oos_span_ms: int | None = None
            if lineage is not None:
                cutoff = int(lineage["feedback_cutoff_ms"])
                fresh_oos_span_ms = max(
                    0, int(admission.common_end_ms or now) - cutoff
                )
                if fresh_oos_span_ms < policy.validation_min_span_ms:
                    gate_reasons.append("ADAPTIVE_FEEDBACK_FRESH_OOS_BELOW_30D")
                    hard_pass = False
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
                # Preserve the evaluator result separately from the workflow
                # state below.  The old shape overwrote ``item.status`` and
                # made it impossible for an isolated engineering-paper worker
                # to distinguish an evaluated candidate from one with no
                # usable observations.
                "evaluation_status": item.status,
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
                "adaptive_feedback": lineage is not None,
                "parent_candidate_id": (
                    lineage.get("parent_candidate_id") if lineage else None
                ),
                "feedback_cutoff_ms": (
                    lineage.get("feedback_cutoff_ms") if lineage else None
                ),
                "mutation": lineage.get("mutation") if lineage else None,
                "fresh_oos_required": bool(lineage),
                "adaptive_generation": (
                    lineage.get("adaptive_generation") if lineage else None
                ),
                "fresh_oos_span_ms": fresh_oos_span_ms,
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
            "research_policy": research_policy,
            "research_policy_hash": research_policy_hash,
            "search_id": search_id,
            "manifest_hash": stable_hash(manifest_rows),
            "hypotheses": manifest_rows,
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
                "base_pre_registered": len(base_hypotheses),
                "adaptive_pre_registered": len(adaptive),
                "adaptive_evaluated": sum(
                    row.get("adaptive_feedback") is True for row in result_rows
                ),
                "diagnostic_leads": sum(bool(row.get("diagnostic_lead")) for row in result_rows),
                "formal_passes": sum(bool(row.get("gate_pass")) for row in result_rows),
                "shadow_candidates": sum(row.get("status") == "SHADOW_CANDIDATE_READY" for row in result_rows),
            },
            "feedback": {
                **feedback.as_dict(),
                "active_adaptive_candidates": len(adaptive),
                "new_candidate_ids": [
                    item.spec.candidate_id for item in feedback.proposals
                ],
                "feedback_hash": stable_hash(feedback.as_dict()),
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
