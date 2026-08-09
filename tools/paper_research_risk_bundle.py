"""Publish a truthful paper-risk preflight artifact.

This tool does not synthesize nine-engine inputs.  It binds the currently
attested forward decisions to the current dataset/config identity and writes a
bundle whose missing fields remain explicit.  The paper order path therefore
shows the exact evidence still required instead of treating a missing file as
an opaque outage.  A future producer may extend the rows with independently
attested inputs, but this command never fills those fields with defaults.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping, Sequence

# Keep the documented ``python tools/<entrypoint>.py`` invocation equivalent
# to ``python -m tools.<entrypoint>``.  The repository intentionally does not
# install itself as a site package, so direct script execution must bootstrap
# the repository root before importing the service trees.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.research.paper_trading.attested_store import AttestedForwardLedger
from quantos.contracts.hashing import stable_hash
from services.risk.research_risk.bundle import (
    BUNDLE_PROVENANCE,
    BUNDLE_SCHEMA_VERSION,
    REQUIRED_INPUT_FIELDS,
    forward_decision_payload_sha256,
)
from services.risk.research_risk.domain.models import RiskConfig


ALPHA_IDS = ("carry_oi_majors", "crowd_contra", "top_contra")
DEFAULT_FORWARD_DB = Path("data/cache/paper_track.db")
DEFAULT_DATASET_EVIDENCE = Path("evidence/crypto_robust_optimization.json")
DEFAULT_EVALUATIONS = Path("evidence/paper_alpha_evaluations.json")
DEFAULT_FORWARD_EVALUATIONS = Path("data/cache/forward_alpha_evaluations.json")
DEFAULT_FORWARD_STATUS = Path("data/cache/forward_track_status.json")
DEFAULT_FX_DB = Path("data/cache/fx.db")
DEFAULT_MAINTENANCE = Path("data/cache/alpha_maintenance_status.json")
DEFAULT_OUTPUT = Path("data/cache/paper_research_risk_inputs.json")
DEFAULT_PAPER_AUM_USD = 1_000_000.0
MAX_FX_AGE_DAYS = 8
REQUIRED_FORWARD_PERIODS = 8
_LAST_RELEASE_RUN_ATTESTATION: dict[str, Any] | None = None


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _path_content_identity(path: str | Path) -> dict[str, Any]:
    """Bind authoritative bytes, including uncheckpointed SQLite WAL data."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        return {
            "path": os.path.normpath(str(resolved)),
            "exists": False,
        }
    result: dict[str, Any] = {
        "path": os.path.normpath(str(resolved)),
        "exists": True,
        "size": resolved.stat().st_size,
        "sha256": _sha256_file(resolved),
    }
    wal = Path(str(resolved) + "-wal")
    if wal.is_file():
        result["wal"] = {
            "size": wal.stat().st_size,
            "sha256": _sha256_file(wal),
        }
    return result


def _source_authority_view(paths: Mapping[str, str | Path]) -> dict[str, Any]:
    return {
        str(name): _path_content_identity(path)
        for name, path in sorted(paths.items())
    }


def _source_authority_paths(
    *,
    forward_db: str | Path,
    dataset_evidence: str | Path,
    evaluations: str | Path,
    forward_evaluations: str | Path,
    forward_status: str | Path,
    fx_db: str | Path,
    maintenance: str | Path,
) -> dict[str, str | Path]:
    """Enumerate every mutable authority consumed by the production builder."""
    return {
        "crypto_funding_db": Path("data/cache/crypto_funding.db"),
        "crypto_funding_rates": Path("data/cache/crypto_funding_rates.json"),
        "crypto_funding_snapshot": Path("data/cache/crypto_funding_snapshot.json"),
        "crypto_oi_metrics": Path("data/cache/crypto_oi_metrics.json"),
        "dataset_evidence": dataset_evidence,
        "experiment_registry": Path("data/cache/experiment_registry.db"),
        "forward_evaluations": forward_evaluations,
        "forward_ledger": forward_db,
        "forward_status": forward_status,
        "fx_authority": fx_db,
        "macro_fred_db": Path("data/cache/macro_fred.db"),
        "maintenance": maintenance,
        "paper_evaluations": evaluations,
    }


def _source_view_complete(view: Mapping[str, Any]) -> bool:
    if not view:
        return False
    for item in view.values():
        if (
            not isinstance(item, Mapping)
            or item.get("exists") is not True
            or not _is_sha256(item.get("sha256"))
        ):
            return False
        wal = item.get("wal")
        if wal is not None and (
            not isinstance(wal, Mapping) or not _is_sha256(wal.get("sha256"))
        ):
            return False
    return True


def _risk_input_bundle_view(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: document.get(name)
        for name in (
            "schema_version",
            "provenance",
            "status",
            "producer",
            "producer_contract",
            "dataset_version",
            "config_hash",
            "candidate_universe",
            "alphas",
            "missing_inputs",
            "synthetic_defaults",
            "execution_authority_granted",
            "order_submission_allowed",
        )
    }


def _build_release_run_attestation(
    document: Mapping[str, Any],
    *,
    candidate_ids: Sequence[str],
    contract: Mapping[str, Any],
    source_before: Mapping[str, Any],
    source_after: Mapping[str, Any],
    output_path: str | Path,
) -> dict[str, Any]:
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT")
    if isinstance(release_commit, str):
        release_commit = release_commit.strip().lower()
    input_hashes = {
        "candidate_universe": stable_hash(list(candidate_ids)),
        "paper_research_risk_contract": stable_hash(dict(contract)),
        "risk_input_bundle_view": stable_hash(_risk_input_bundle_view(document)),
        "source_authorities_before": stable_hash(dict(source_before)),
    }
    output_hashes = {
        "paper_research_risk_bundle_file": _sha256_file(output_path),
        "source_authorities_after": stable_hash(dict(source_after)),
    }
    raw_safety = document.get("safety")
    raw_safety = raw_safety if isinstance(raw_safety, Mapping) else {}
    safety = {
        "paper_only": raw_safety.get("paper_only"),
        "live_allowed": raw_safety.get("live_allowed"),
        "order_submission_allowed": raw_safety.get("order_submission_allowed"),
        "credentials_accessed": raw_safety.get("credentials_accessed"),
        "broker_connectivity": raw_safety.get("broker_connectivity"),
    }
    expected_safety = {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }
    rows = document.get("alphas")
    complete = bool(
        isinstance(release_commit, str)
        and len(release_commit) == 40
        and all(character in "0123456789abcdef" for character in release_commit)
        and document.get("status") in {"READY", "PENDING"}
        and document.get("producer") == "tools.paper_research_risk_bundle"
        and document.get("synthetic_defaults") is False
        and document.get("execution_authority_granted") is False
        and document.get("order_submission_allowed") is False
        and isinstance(rows, Mapping)
        and set(rows) == set(candidate_ids)
        and source_before == source_after
        and _source_view_complete(source_before)
        and all(_is_sha256(value) for value in input_hashes.values())
        and all(_is_sha256(value) for value in output_hashes.values())
        and safety == expected_safety
    )
    return {
        "schema_version": 1,
        "module": "paper_research_risk_bundle",
        "release_commit": release_commit,
        "complete": complete,
        "input_hashes": dict(sorted(input_hashes.items())),
        "output_hashes": dict(sorted(output_hashes.items())),
        "safety": safety,
    }


def release_run_attestation() -> dict[str, Any] | None:
    """Return a detached copy of the latest paper-risk release proof."""
    if _LAST_RELEASE_RUN_ATTESTATION is None:
        return None
    return json.loads(
        json.dumps(
            _LAST_RELEASE_RUN_ATTESTATION,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _config_hash() -> str:
    """Full digest for the actual nine-engine config (not the legacy 16-char id)."""
    payload = dict(RiskConfig().__dict__)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _load_dataset_version(path: Path) -> str:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"dataset evidence is unreadable: {exc}") from exc
    version = document.get("dataset", {}).get("version") if isinstance(document, Mapping) else None
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("dataset evidence has no version")
    return version


def _pending_rows(
    *,
    forward_db: Path,
    alpha_ids: Sequence[str],
    dataset_version: str,
    config_hash: str,
) -> dict[str, dict[str, Any]]:
    ledger = AttestedForwardLedger(str(forward_db), create=False)
    rows: dict[str, dict[str, Any]] = {}
    try:
        for alpha_id in alpha_ids:
            pending = ledger.attested_pending(alpha_id)
            if len(pending) != 1:
                raise RuntimeError(
                    f"{alpha_id} requires exactly one attested pending decision; found {len(pending)}"
                )
            row = dict(pending[0])
            row["alpha_id"] = alpha_id
            rows[alpha_id] = {
                "binding": {
                    "alpha_id": alpha_id,
                    "decision_ts": int(row["decided_ts"]),
                    "decision_payload_sha256": forward_decision_payload_sha256(row),
                    "dataset_version": dataset_version,
                    "config_hash": config_hash,
                },
                # Deliberately no inputs: these are the exact fields the
                # complete producer must supply from the same PIT snapshot.
                "missing_inputs": list(REQUIRED_INPUT_FIELDS),
                "status": "PENDING",
            }
    finally:
        ledger.close()
    return rows


def build_pending_bundle(
    *,
    forward_db: str | Path = DEFAULT_FORWARD_DB,
    dataset_evidence: str | Path = DEFAULT_DATASET_EVIDENCE,
    alpha_ids: Sequence[str] = ALPHA_IDS,
) -> dict[str, Any]:
    selected = tuple(alpha_ids)
    if selected != tuple(dict.fromkeys(selected)) or not selected or any(alpha not in ALPHA_IDS for alpha in selected):
        raise ValueError("alpha_ids must be a non-empty subset of the registered Alphas")
    dataset_version = _load_dataset_version(Path(dataset_evidence))
    config_hash = _config_hash()
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "provenance": BUNDLE_PROVENANCE,
        "status": "PENDING",
        "producer": "tools.paper_research_risk_bundle",
        "producer_contract": "PREVIEW_BINDING_ONLY_NO_SYNTHETIC_RISK_INPUTS",
        "dataset_version": dataset_version,
        "config_hash": config_hash,
        "alphas": _pending_rows(
            forward_db=Path(forward_db),
            alpha_ids=selected,
            dataset_version=dataset_version,
            config_hash=config_hash,
        ),
        "missing_inputs": list(REQUIRED_INPUT_FIELDS),
        "synthetic_defaults": False,
        "execution_authority_granted": False,
        "order_submission_allowed": False,
    }


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return value


def _cny_per_usd_as_of(path: Path, decision_ts: int) -> tuple[float, dict[str, Any]]:
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        row = connection.execute(
            "SELECT event_time,knowledge_time,payload_json FROM bars "
            "WHERE key='CNY' AND event_time<=? AND knowledge_time<=? "
            "ORDER BY event_time DESC,knowledge_time DESC LIMIT 1",
            (int(decision_ts), int(decision_ts)),
        ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError(f"CNY FX authority is unavailable: {exc}") from exc
    finally:
        if "connection" in locals():
            connection.close()
    if row is None:
        raise RuntimeError("CNY FX authority has no PIT observation")
    try:
        payload = json.loads(str(row[2]))
        usd_per_cny = float(payload["close"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("CNY FX observation is malformed") from exc
    age_days = (int(decision_ts) - int(row[0])) / 86_400_000
    if (
        not math.isfinite(usd_per_cny)
        or usd_per_cny <= 0.0
        or age_days < 0.0
        or age_days > MAX_FX_AGE_DAYS
    ):
        raise RuntimeError("CNY FX observation is stale or invalid")
    return 1.0 / usd_per_cny, {
        "source": "FRANKFURTER_ECB_USD_BASE_CNY",
        "event_time": int(row[0]),
        "knowledge_time": int(row[1]),
        "age_days": age_days,
        "usd_per_cny": usd_per_cny,
    }


def _latest_period_at_or_before(periods: Sequence[int], decision_ts: int) -> int:
    eligible = [int(value) for value in periods if int(value) <= int(decision_ts)]
    if not eligible:
        raise RuntimeError("alpha schedule has no PIT period at or before decision")
    return max(eligible)


def _decision_adv_inputs(
    *,
    schedule,
    decision_ts: int,
    weights: Mapping[str, float],
    cny_per_usd: float,
    paper_aum_usd: float,
) -> dict[str, Any]:
    period = _latest_period_at_or_before(schedule.periods, decision_ts)
    adv = schedule.adv_by_period.get(period)
    if not isinstance(adv, Mapping):
        raise RuntimeError("alpha schedule has no ADV row for the decision")
    missing = sorted(str(name) for name in weights if name not in adv)
    if missing:
        raise RuntimeError(f"alpha schedule is missing decision ADV: {missing}")
    capacity = {
        str(name): float(adv[name]) * 0.10 * float(cny_per_usd)
        for name in sorted(weights)
    }
    if any(not math.isfinite(value) or value <= 0.0 for value in capacity.values()):
        raise RuntimeError("alpha schedule contains invalid decision ADV")
    aggregate_adv = sum(float(adv[name]) for name in weights) * float(cny_per_usd)
    return {
        "capacity_cny": capacity,
        "daily_volume_cny": aggregate_adv,
        "total_aum": float(paper_aum_usd) * float(cny_per_usd),
        "liquidity_provenance": {
            "schedule_period": period,
            "capacity_rule": "10_PERCENT_OF_PIT_DAILY_QUOTE_ADV",
            "currency": "CNY",
        },
    }


def _verified_evaluation_rows(path: Path) -> dict[str, dict[str, Any]]:
    document = _read_object(path)
    if document.get("schema_version") != "paper_alpha_evaluations.v1":
        raise RuntimeError("paper alpha evaluation schema is invalid")
    rows = document.get("alphas")
    if not isinstance(rows, Mapping):
        raise RuntimeError("paper alpha evaluation rows are missing")

    from tools.search_trial_registry import production_experiment_registry

    result: dict[str, dict[str, Any]] = {}
    with production_experiment_registry() as registry:
        for deployed in ALPHA_IDS:
            summary = rows.get(deployed)
            evaluation_ref = (
                summary.get("evaluation") if isinstance(summary, Mapping) else None
            )
            run_id = (
                evaluation_ref.get("run_id")
                if isinstance(evaluation_ref, Mapping)
                else None
            )
            if not isinstance(run_id, str) or not registry.verify_evaluation(run_id):
                continue
            stored = registry.get_evaluation(run_id)
            if not isinstance(stored, Mapping):
                continue
            try:
                evaluation = json.loads(str(stored["evaluation_json"]))
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            if (
                isinstance(summary, Mapping)
                and evaluation.get("alpha_id") == summary.get("research_alpha_id")
                and stored.get("dataset_version") == document.get("dataset_version")
            ):
                result[deployed] = {
                    "evaluation": evaluation,
                    "run_id": run_id,
                    "research_alpha_id": summary.get("research_alpha_id"),
                    "definition_hash": summary.get("definition_hash"),
                    "dataset_version": stored.get("dataset_version"),
                    "content_hash": stored.get("content_hash"),
                }
    return result


def _verified_forward_evaluation_rows(
    path: Path,
    alpha_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Verify independent challenger evaluations against the registry."""
    document = _read_object(path)
    if document.get("schema_version") != "forward_alpha_evaluations.v1":
        return {}
    rows = document.get("alphas")
    if not isinstance(rows, Mapping):
        return {}
    from tools.search_trial_registry import production_experiment_registry

    result: dict[str, dict[str, Any]] = {}
    with production_experiment_registry() as registry:
        for alpha_id in alpha_ids:
            summary = rows.get(alpha_id)
            evaluation_ref = (
                summary.get("evaluation") if isinstance(summary, Mapping) else None
            )
            run_id = (
                evaluation_ref.get("run_id")
                if isinstance(evaluation_ref, Mapping)
                else None
            )
            if not isinstance(run_id, str) or not registry.verify_evaluation(run_id):
                continue
            stored = registry.get_evaluation(run_id)
            if not isinstance(stored, Mapping):
                continue
            try:
                evaluation = json.loads(str(stored["evaluation_json"]))
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            if (
                isinstance(summary, Mapping)
                and evaluation.get("alpha_id") == summary.get("research_alpha_id")
                and stored.get("dataset_version") == document.get("dataset_version")
            ):
                result[str(alpha_id)] = {
                    "evaluation": evaluation,
                    "run_id": run_id,
                    "research_alpha_id": summary.get("research_alpha_id"),
                    "definition_hash": summary.get("definition_hash"),
                    "dataset_version": stored.get("dataset_version"),
                    "content_hash": stored.get("content_hash"),
                    "quantile": summary.get("quantile"),
                    "cadence_days": summary.get("cadence_days"),
                }
    return result


def _forward_periods(path: Path, alpha_id: str, ledger: AttestedForwardLedger) -> int:
    document = _read_object(path)
    rows = document.get("alphas")
    row = rows.get(alpha_id) if isinstance(rows, Mapping) else None
    value = row.get("n_forward_periods") if isinstance(row, Mapping) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return int(value)
    return len(ledger.attested_realizations(alpha_id))


def _dynamic_admission_blockers(
    *,
    forward_periods: int,
    evaluation: Mapping[str, Any] | None,
) -> list[str]:
    """Keep evidence production independent from formal Paper admission."""
    blockers: list[str] = []
    if evaluation is None:
        blockers.append("INDEPENDENT_EVALUATION_MISSING")
    elif evaluation.get("verdict") != "PASS":
        blockers.append("INDEPENDENT_EVALUATION_NOT_PASS")
    if int(forward_periods) < REQUIRED_FORWARD_PERIODS:
        blockers.append("FORWARD_EVIDENCE_MATURITY_PENDING")
    return blockers


def _dynamic_evidence_decision(
    ledger: AttestedForwardLedger,
    alpha_id: str,
) -> tuple[dict[str, Any] | None, str, list[str], str | None]:
    """Select an attested PIT decision for evidence without inventing one.

    A current pending decision is the formal admission binding.  When a slower
    cadence has already realized its latest decision, the same immutable row is
    still valid for non-time monitoring inputs (ADV, capacity, return history,
    and model diagnostics), but it is never treated as a current admission
    decision.  More than one pending row is ambiguous and remains fail-closed.
    """
    pending = [dict(row) for row in ledger.attested_pending(alpha_id)]
    if len(pending) == 1:
        return pending[0], "CURRENT_ATTESTED_PENDING_DECISION", [], None
    if len(pending) > 1:
        return (
            None,
            "AMBIGUOUS_ATTESTED_PENDING_DECISIONS",
            ["ATTESTED_PENDING_DECISION_BACKLOG"],
            f"attested_pending_decision_backlog:{len(pending)}",
        )
    decisions = [dict(row) for row in ledger.attested_decisions(alpha_id)]
    if not decisions:
        return (
            None,
            "ATTESTED_DECISION_MISSING",
            ["ATTESTED_FORWARD_DECISION_MISSING"],
            "attested_forward_decision_missing",
        )
    latest = max(
        decisions,
        key=lambda row: (int(row["decided_ts"]), int(row["recorded_at"])),
    )
    return (
        latest,
        "LATEST_REALIZED_ATTESTED_DECISION_MONITORING_ONLY",
        ["CURRENT_ATTESTED_PENDING_DECISION_MISSING"],
        None,
    )


def _dynamic_candidate_schedule(panel, alpha_id: str):
    """Build a real PIT schedule for a forward challenger when needed."""
    from tools.crypto_robust_optimization import _prepare_oi_alpha
    from tools.forward_track_advance import _alpha_spec, _registered_alphas

    registered, _dynamic_ids = _registered_alphas()
    entry = registered.get(alpha_id)
    if entry is None:
        raise RuntimeError(f"forward candidate specification is missing: {alpha_id}")
    spec = _alpha_spec(entry)
    return _prepare_oi_alpha(
        panel,
        alpha_id,
        spec["expr"],
        quantile=float(spec["quantile"]),
    )


def _candidate_risk_history(
    candidate_schedule,
    reference_schedule,
    base_research: Mapping[str, Any],
) -> tuple[dict[str, tuple[float, ...]], list[str], list[int]]:
    """Align candidate and a real peer on common PIT ISO weeks."""
    from tools.crypto_robust_optimization import _schedule_return_map, _week_key

    candidate_by_week = {
        _week_key(timestamp): int(timestamp)
        for timestamp in candidate_schedule.periods
    }
    reference_by_week = {
        _week_key(timestamp): int(timestamp)
        for timestamp in reference_schedule.periods
    }
    common = sorted(set(candidate_by_week) & set(reference_by_week))
    candidate_returns, _ = _schedule_return_map(candidate_schedule, [candidate_by_week[week] for week in common])
    reference_returns, _ = _schedule_return_map(reference_schedule, [reference_by_week[week] for week in common])
    valid = sorted(set(candidate_returns) & set(reference_returns))
    if len(valid) < 16:
        raise RuntimeError(f"fewer than 16 common PIT return weeks: {len(valid)}")
    candidate_id = candidate_schedule.alpha_id
    reference_id = reference_schedule.alpha_id
    periods = [max(candidate_by_week[week], reference_by_week[week]) for week in valid]
    label_map = {
        _week_key(timestamp): str(label)
        for timestamp, label in zip(
            base_research.get("periods", ()),
            base_research.get("state_labels", ()),
        )
    }
    labels = [label_map.get(week, "UNKNOWN") for week in valid]
    return {
        candidate_id: tuple(float(candidate_returns[week]) for week in valid),
        reference_id: tuple(float(reference_returns[week]) for week in valid),
    }, labels, periods


def _metadata_from_evaluation(
    *,
    deployed_alpha_id: str,
    evaluation_row: Mapping[str, Any],
    decision_recorded_at: int,
    forward_periods: int,
    maintenance: Mapping[str, Any],
    allow_post_decision_monitoring: bool = False,
) -> dict[str, Any]:
    evaluation = evaluation_row.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise RuntimeError("verified evaluation payload is missing")
    as_of = evaluation.get("as_of")
    if not isinstance(as_of, int):
        raise RuntimeError("verified evaluation as_of is invalid")
    post_decision = as_of > int(decision_recorded_at)
    if post_decision and not allow_post_decision_monitoring:
        raise RuntimeError("verified evaluation does not predate the decision")
    reasons = [str(value).upper() for value in evaluation.get("reasons") or ()]
    dsr = evaluation.get("deflated_sharpe")
    pbo = evaluation.get("pbo")
    if not isinstance(dsr, (int, float)) or not isinstance(pbo, (int, float)):
        raise RuntimeError("verified evaluation confidence fields are missing")
    strategies = maintenance.get("strategies")
    maintenance_row = (
        strategies.get(deployed_alpha_id) if isinstance(strategies, Mapping) else None
    )
    maintenance_state = (
        str(maintenance_row.get("state", "")).upper()
        if isinstance(maintenance_row, Mapping)
        else "HEALTHY_NO_ACTION"
    )
    return {
        "age_days": max(0.0, (int(decision_recorded_at) - as_of) / 86_400_000),
        "validation_confidence": max(0.0, min(1.0, float(dsr))),
        "paper_confidence": max(
            0.0, min(1.0, int(forward_periods) / REQUIRED_FORWARD_PERIODS)
        ),
        "decay_flag": maintenance_state in {"DEGRADED", "PAUSED", "RETIRED"},
        # A high catalog trial count is a multiple-testing/review condition, not
        # itself an observed overfit event.  Only PBO or an explicit machine
        # overfit finding may set this flag.
        "overfit_flag": float(pbo) > 0.50
        or any(
            "OVERFIT_DETECTED" in reason or "OVERFIT_CONFIRMED" in reason
            for reason in reasons
        ),
        "leakage_flag": any("LEAKAGE" in reason for reason in reasons),
        "deployed_alpha_id": deployed_alpha_id,
        "research_alpha_id": evaluation_row.get("research_alpha_id"),
        "evaluation_run_id": evaluation_row.get("run_id"),
        "evaluation_verdict": evaluation.get("verdict"),
        "maintenance_state": maintenance_state,
        "evidence_timing": (
            "POST_DECISION_MONITORING_ONLY" if post_decision else "PREDECISION"
        ),
        "evaluation_as_of": as_of,
        "decision_recorded_at": int(decision_recorded_at),
    }


def build_research_risk_bundle(
    *,
    forward_db: str | Path = DEFAULT_FORWARD_DB,
    evaluations: str | Path = DEFAULT_EVALUATIONS,
    fx_db: str | Path = DEFAULT_FX_DB,
    maintenance: str | Path = DEFAULT_MAINTENANCE,
    alpha_ids: Sequence[str] = ALPHA_IDS,
    paper_aum_usd: float = DEFAULT_PAPER_AUM_USD,
) -> dict[str, Any]:
    selected = tuple(alpha_ids)
    if selected != tuple(dict.fromkeys(selected)) or not selected or any(
        alpha not in ALPHA_IDS for alpha in selected
    ):
        raise ValueError("alpha_ids must be a non-empty subset of the registered Alphas")
    if not math.isfinite(float(paper_aum_usd)) or float(paper_aum_usd) <= 0:
        raise ValueError("paper_aum_usd must be positive and finite")

    from tools.crypto_robust_optimization import (
        _build_bundle,
        _dataset_version,
        load_alpha_schedules,
    )

    schedules, schedule_identity = load_alpha_schedules()
    research = _build_bundle(schedules)
    robust_version, robust_hashes = _dataset_version()
    labels = research.get("state_labels")
    returns = research.get("alpha_returns")
    timestamps = research.get("periods")
    if (
        not isinstance(labels, Sequence)
        or any(not isinstance(value, str) or not value for value in labels)
        or not isinstance(returns, Mapping)
        or not isinstance(timestamps, Sequence)
        or len(timestamps) != len(labels)
        or any(not isinstance(value, int) or value < 0 for value in timestamps)
    ):
        raise RuntimeError("aligned PIT return/regime evidence is incomplete")
    evaluation_rows = _verified_evaluation_rows(Path(evaluations))
    maintenance_document = _read_object(Path(maintenance))
    config_hash = _config_hash()
    ledger = AttestedForwardLedger(str(forward_db), create=False)
    rows: dict[str, dict[str, Any]] = {}
    all_missing: list[str] = []
    dataset_components: list[dict[str, Any]] = []
    try:
        for alpha_id in selected:
            pending_rows = ledger.attested_pending(alpha_id)
            if len(pending_rows) != 1:
                raise RuntimeError(
                    f"{alpha_id} requires exactly one attested pending decision; "
                    f"found {len(pending_rows)}"
                )
            pending = dict(pending_rows[0])
            pending["alpha_id"] = alpha_id
            try:
                weights = json.loads(str(pending["weights_json"]))
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"{alpha_id} decision weights are malformed") from exc
            if not isinstance(weights, Mapping) or not weights:
                raise RuntimeError(f"{alpha_id} decision weights are empty")
            cny_per_usd, fx_identity = _cny_per_usd_as_of(
                Path(fx_db), int(pending["decided_ts"])
            )
            liquidity = _decision_adv_inputs(
                schedule=schedules[alpha_id],
                decision_ts=int(pending["decided_ts"]),
                weights=weights,
                cny_per_usd=cny_per_usd,
                paper_aum_usd=float(paper_aum_usd),
            )
            dataset_components.append(
                {
                    "alpha_id": alpha_id,
                    "decision_ts": int(pending["decided_ts"]),
                    "fx": fx_identity,
                    "liquidity": liquidity["liquidity_provenance"],
                }
            )
            inputs = {
                "capacity_cny": liquidity["capacity_cny"],
                "daily_volume_cny": liquidity["daily_volume_cny"],
                "total_aum": liquidity["total_aum"],
                "alpha_returns": {
                    str(key): list(value) for key, value in returns.items()
                },
                "regime_labels": list(labels),
                "timestamps": list(timestamps),
            }
            missing: list[str] = []
            missing_reasons: dict[str, str] = {}
            evaluation_row = evaluation_rows.get(alpha_id)
            if evaluation_row is None:
                missing.append("alpha_metadata")
                missing_reasons["alpha_metadata"] = "verified_evaluation_missing"
            else:
                forward_periods = len(ledger.attested_realizations(alpha_id))
                try:
                    metadata = _metadata_from_evaluation(
                        deployed_alpha_id=alpha_id,
                        evaluation_row=evaluation_row,
                        decision_recorded_at=int(pending["recorded_at"]),
                        forward_periods=forward_periods,
                        maintenance=maintenance_document,
                    )
                except RuntimeError as exc:
                    missing.append("alpha_metadata")
                    missing_reasons["alpha_metadata"] = str(exc)
                else:
                    inputs["alpha_metadata"] = [metadata]
            all_missing.extend(missing)
            rows[alpha_id] = {
                "binding": {
                    "alpha_id": alpha_id,
                    "decision_ts": int(pending["decided_ts"]),
                    "decision_payload_sha256": forward_decision_payload_sha256(pending),
                    "dataset_version": "PENDING",
                    "config_hash": config_hash,
                },
                "inputs": inputs,
                "missing_inputs": missing,
                "missing_reasons": missing_reasons,
                "status": "READY" if not missing else "PENDING",
                "provenance": {
                    "robust_dataset_version": robust_version,
                    "robust_dataset_files": robust_hashes,
                    "schedule_identity": schedule_identity,
                    "fx": fx_identity,
                    "liquidity": liquidity["liquidity_provenance"],
                    "synthetic_defaults": False,
                },
            }
    finally:
        ledger.close()
    dataset_version = hashlib.sha256(
        json.dumps(
            {
                "robust_version": robust_version,
                "components": dataset_components,
                "evaluation_artifact": _read_object(Path(evaluations)).get(
                    "dataset_version"
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    for row in rows.values():
        row["binding"]["dataset_version"] = dataset_version
    missing = list(dict.fromkeys(all_missing))
    ready = not missing and all(row["status"] == "READY" for row in rows.values())
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "provenance": BUNDLE_PROVENANCE,
        "status": "READY" if ready else "PENDING",
        "producer": "tools.paper_research_risk_bundle",
        "producer_contract": "REAL_PIT_INPUTS_NO_SYNTHETIC_DEFAULTS",
        "dataset_version": dataset_version,
        "config_hash": config_hash,
        "alphas": rows,
        "missing_inputs": missing,
        "synthetic_defaults": False,
        "execution_authority_granted": False,
        "order_submission_allowed": False,
    }


def build_automatic_research_risk_bundle(
    *,
    forward_db: str | Path = DEFAULT_FORWARD_DB,
    dataset_evidence: str | Path = DEFAULT_DATASET_EVIDENCE,
    evaluations: str | Path = DEFAULT_EVALUATIONS,
    forward_evaluations: str | Path = DEFAULT_FORWARD_EVALUATIONS,
    forward_status: str | Path = DEFAULT_FORWARD_STATUS,
    fx_db: str | Path = DEFAULT_FX_DB,
    maintenance: str | Path = DEFAULT_MAINTENANCE,
    alpha_ids: Sequence[str] = ALPHA_IDS,
    paper_aum_usd: float = DEFAULT_PAPER_AUM_USD,
) -> dict[str, Any]:
    """Extend the static bundle with every forward challenger.

    Dynamic challengers precompute every non-time-based risk input as soon as
    an attested decision exists.  Eight forward periods and independent
    evaluation remain formal admission gates, but they do not postpone PIT
    return/regime, capacity, ADV, AUM, or dataset-provenance construction.
    This lets realtime engineering paper and evidence production advance in
    parallel without pretending an immature candidate is formally eligible.
    """
    selected = tuple(alpha_ids)
    if selected != tuple(dict.fromkeys(selected)) or not selected:
        raise ValueError("alpha_ids must be a non-empty unique sequence")
    static_selected = tuple(alpha for alpha in selected if alpha in ALPHA_IDS)
    dynamic_selected = tuple(alpha for alpha in selected if alpha not in ALPHA_IDS)
    base = (
        build_research_risk_bundle(
            forward_db=forward_db,
            evaluations=evaluations,
            fx_db=fx_db,
            maintenance=maintenance,
            alpha_ids=static_selected,
            paper_aum_usd=paper_aum_usd,
        )
        if static_selected
        else {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "provenance": BUNDLE_PROVENANCE,
            "status": "PENDING",
            "producer": "tools.paper_research_risk_bundle",
            "producer_contract": "REAL_PIT_INPUTS_NO_SYNTHETIC_DEFAULTS",
            "config_hash": _config_hash(),
            "alphas": {},
            "missing_inputs": [],
            "synthetic_defaults": False,
            "execution_authority_granted": False,
            "order_submission_allowed": False,
        }
    )
    if not dynamic_selected:
        return base

    evaluation_rows = _verified_forward_evaluation_rows(
        Path(forward_evaluations), dynamic_selected
    )
    maintenance_document = _read_object(Path(maintenance))
    config_hash = str(base.get("config_hash") or _config_hash())
    ledger = AttestedForwardLedger(str(forward_db), create=False)
    dynamic_rows: dict[str, dict[str, Any]] = {}
    dynamic_missing: list[str] = []
    dataset_components: list[dict[str, Any]] = []
    panel = None
    base_schedules = None
    base_research = None
    try:
        for alpha_id in dynamic_selected:
            pending, decision_basis, decision_blockers, decision_error = (
                _dynamic_evidence_decision(ledger, alpha_id)
            )
            if pending is None:
                dynamic_missing.append("forward_decision")
                dynamic_rows[alpha_id] = {
                    "binding": {"alpha_id": alpha_id, "config_hash": config_hash},
                    "missing_inputs": ["forward_decision"],
                    "missing_reasons": {
                        "forward_decision": decision_error
                        or "attested_forward_decision_missing"
                    },
                    "input_evidence_status": "PENDING",
                    "admission_blockers": decision_blockers,
                    "status": "PENDING",
                    "maturity": {"forward_periods": None, "required": REQUIRED_FORWARD_PERIODS},
                    "provenance": {
                        "independent_evaluation": "PENDING",
                        "decision_basis": decision_basis,
                        "synthetic_defaults": False,
                    },
                }
                continue

            pending["alpha_id"] = alpha_id
            decision_ts = int(pending["decided_ts"])
            binding = {
                "alpha_id": alpha_id,
                "decision_ts": decision_ts,
                "decision_payload_sha256": forward_decision_payload_sha256(pending),
                "config_hash": config_hash,
                "dataset_version": "PENDING",
            }
            forward_periods = _forward_periods(Path(forward_status), alpha_id, ledger)
            row_missing: list[str] = []
            missing_reasons: dict[str, str] = {}
            evaluation_row = evaluation_rows.get(alpha_id)
            evaluation = (
                evaluation_row.get("evaluation")
                if isinstance(evaluation_row, Mapping)
                else None
            )
            admission_blockers = _dynamic_admission_blockers(
                forward_periods=forward_periods,
                evaluation=evaluation if isinstance(evaluation, Mapping) else None,
            )
            admission_blockers = list(
                dict.fromkeys(decision_blockers + admission_blockers)
            )
            if evaluation_row is None or not isinstance(evaluation, Mapping):
                row_missing.append("alpha_metadata")
                missing_reasons["alpha_metadata"] = "independent_evaluation_missing"

            # Non-time inputs are evidence production, not admission.  Build
            # them immediately from the attested PIT decision even while the
            # forward/evaluation gates remain visibly immature or failed.
            inputs: dict[str, Any] = {}
            provenance: dict[str, Any] = {
                "independent_evaluation": (
                    evaluation_row.get("run_id")
                    if isinstance(evaluation_row, Mapping)
                    else None
                ),
                "forward_periods": forward_periods,
                "required_forward_periods": REQUIRED_FORWARD_PERIODS,
                "formal_forward_gate_passed": (
                    forward_periods >= REQUIRED_FORWARD_PERIODS
                ),
                "independent_evaluation_verdict": (
                    evaluation.get("verdict")
                    if isinstance(evaluation, Mapping)
                    else None
                ),
                "decision_basis": decision_basis,
                "synthetic_defaults": False,
            }
            try:
                if panel is None:
                    from tools.crypto_oi_research import build_panel
                    from tools.crypto_robust_optimization import (
                        _build_bundle,
                        load_alpha_schedules,
                    )

                    base_schedules, schedule_identity = load_alpha_schedules()
                    base_research = _build_bundle(base_schedules)
                    panel, _ = build_panel()
                schedule = _dynamic_candidate_schedule(panel, alpha_id)
                reference_id = (
                    "carry_oi_majors"
                    if alpha_id == "crowd_contra"
                    else "crowd_contra"
                )
                reference = base_schedules[reference_id]
                returns, labels, timestamps = _candidate_risk_history(
                    schedule,
                    reference,
                    base_research,
                )
                cny_per_usd, fx_identity = _cny_per_usd_as_of(
                    Path(fx_db), decision_ts
                )
                liquidity = _decision_adv_inputs(
                    schedule=schedule,
                    decision_ts=decision_ts,
                    weights=json.loads(str(pending["weights_json"])),
                    cny_per_usd=cny_per_usd,
                    paper_aum_usd=float(paper_aum_usd),
                )
                inputs = {
                    "capacity_cny": liquidity["capacity_cny"],
                    "daily_volume_cny": liquidity["daily_volume_cny"],
                    "total_aum": liquidity["total_aum"],
                    "alpha_returns": {key: list(value) for key, value in returns.items()},
                    "regime_labels": labels,
                    "timestamps": timestamps,
                }
                if isinstance(evaluation_row, Mapping):
                    try:
                        metadata = _metadata_from_evaluation(
                            deployed_alpha_id=alpha_id,
                            evaluation_row=evaluation_row,
                            decision_recorded_at=int(pending["recorded_at"]),
                            forward_periods=forward_periods,
                            maintenance=maintenance_document,
                            allow_post_decision_monitoring=True,
                        )
                    except RuntimeError as exc:
                        if "alpha_metadata" not in row_missing:
                            row_missing.append("alpha_metadata")
                        missing_reasons["alpha_metadata"] = str(exc)
                    else:
                        inputs["alpha_metadata"] = [metadata]
                        if metadata.get("evidence_timing") == "POST_DECISION_MONITORING_ONLY":
                            admission_blockers = list(
                                dict.fromkeys(
                                    admission_blockers
                                    + ["INDEPENDENT_EVALUATION_POSTDATES_DECISION"]
                                )
                            )
                        if "alpha_metadata" in row_missing:
                            row_missing.remove("alpha_metadata")
                        missing_reasons.pop("alpha_metadata", None)
                provenance.update(
                    {
                        "schedule_identity": schedule_identity,
                        "reference_alpha_id": reference_id,
                        "fx": fx_identity,
                        "liquidity": liquidity["liquidity_provenance"],
                        "non_time_inputs_precomputed": True,
                    }
                )
                schedule_digest = hashlib.sha256(
                    json.dumps(
                        {
                            "alpha_id": alpha_id,
                            "returns": {
                                str(key): list(value)
                                for key, value in returns.items()
                            },
                            "labels": labels,
                            "timestamps": timestamps,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("utf-8")
                ).hexdigest()
                dataset_components.append(
                    {
                        "alpha_id": alpha_id,
                        "decision_ts": decision_ts,
                        "forward_periods": forward_periods,
                        "evaluation_content_hash": (
                            evaluation_row.get("content_hash")
                            if isinstance(evaluation_row, Mapping)
                            else None
                        ),
                        "schedule_digest": schedule_digest,
                        "fx": fx_identity,
                        "liquidity": liquidity["liquidity_provenance"],
                    }
                )
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                row_missing.extend(REQUIRED_INPUT_FIELDS)
                missing_reasons["candidate_schedule"] = str(exc)[:240]
                inputs = {}
            dynamic_missing.extend(row_missing)
            dynamic_rows[alpha_id] = {
                "binding": binding,
                "inputs": inputs,
                "missing_inputs": list(dict.fromkeys(row_missing)),
                "missing_reasons": missing_reasons,
                "input_evidence_status": "READY" if not row_missing else "PENDING",
                "admission_blockers": admission_blockers,
                "status": (
                    "READY"
                    if not row_missing and not admission_blockers
                    else "PENDING"
                ),
                "maturity": {
                    "forward_periods": forward_periods,
                    "required": REQUIRED_FORWARD_PERIODS,
                },
                "provenance": provenance,
            }
    finally:
        ledger.close()

    combined_rows = dict(base.get("alphas") or {})
    combined_rows.update(dynamic_rows)
    combined_missing = list(dict.fromkeys(
        list(base.get("missing_inputs") or []) + dynamic_missing
    ))
    dataset_version = hashlib.sha256(
        json.dumps(
            {
                "base": base.get("dataset_version"),
                "forward_evaluations": _read_object(Path(forward_evaluations)).get("dataset_version"),
                "dynamic_components": dataset_components,
                "candidate_ids": list(dynamic_selected),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    for row in combined_rows.values():
        binding = row.get("binding") if isinstance(row, Mapping) else None
        if isinstance(binding, dict):
            binding["dataset_version"] = dataset_version
    ready = not combined_missing and all(
        isinstance(row, Mapping) and row.get("status") == "READY"
        for row in combined_rows.values()
    )
    return {
        **base,
        "status": "READY" if ready else "PENDING",
        "dataset_version": dataset_version,
        "alphas": combined_rows,
        "missing_inputs": combined_missing,
        "producer_contract": "REAL_PIT_INPUTS_NO_SYNTHETIC_DEFAULTS_DYNAMIC_FORWARD_GATE",
        "candidate_universe": {
            "static": list(static_selected),
            "dynamic": list(dynamic_selected),
            "count": len(selected),
        },
        "execution_authority_granted": False,
        "order_submission_allowed": False,
    }


def _write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_bundle(
    *,
    forward_db: str | Path = DEFAULT_FORWARD_DB,
    dataset_evidence: str | Path = DEFAULT_DATASET_EVIDENCE,
    evaluations: str | Path = DEFAULT_EVALUATIONS,
    forward_evaluations: str | Path = DEFAULT_FORWARD_EVALUATIONS,
    forward_status: str | Path = DEFAULT_FORWARD_STATUS,
    fx_db: str | Path = DEFAULT_FX_DB,
    maintenance: str | Path = DEFAULT_MAINTENANCE,
    paper_aum_usd: float = DEFAULT_PAPER_AUM_USD,
    output: str | Path = DEFAULT_OUTPUT,
    alpha_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Compute and publish one source-stable, release-attestable risk bundle."""
    global _LAST_RELEASE_RUN_ATTESTATION
    _LAST_RELEASE_RUN_ATTESTATION = None
    if alpha_ids is None:
        from tools.paper_candidate_universe import paper_candidate_ids

        selected = tuple(
            paper_candidate_ids(
                forward_status=str(forward_status),
                evaluations=str(forward_evaluations),
            )
        )
    else:
        selected = tuple(alpha_ids)
    if selected != tuple(dict.fromkeys(selected)) or not selected:
        raise ValueError("alpha_ids must be a non-empty unique sequence")

    source_paths = _source_authority_paths(
        forward_db=forward_db,
        dataset_evidence=dataset_evidence,
        evaluations=evaluations,
        forward_evaluations=forward_evaluations,
        forward_status=forward_status,
        fx_db=fx_db,
        maintenance=maintenance,
    )
    source_before = _source_authority_view(source_paths)
    built = build_automatic_research_risk_bundle(
        forward_db=forward_db,
        dataset_evidence=dataset_evidence,
        evaluations=evaluations,
        forward_evaluations=forward_evaluations,
        forward_status=forward_status,
        fx_db=fx_db,
        maintenance=maintenance,
        alpha_ids=selected,
        paper_aum_usd=paper_aum_usd,
    )
    if not isinstance(built, Mapping):
        raise RuntimeError("paper research-risk builder returned a non-object")
    document = dict(built)
    expected_safety = {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }
    existing_safety = document.get("safety")
    if existing_safety is not None and existing_safety != expected_safety:
        raise RuntimeError("paper research-risk safety contract is invalid")
    document["safety"] = expected_safety
    output_path = Path(output)
    _write(output_path, document)
    source_after = _source_authority_view(source_paths)
    contract = {
        "schema_version": 1,
        "paper_aum_usd": float(paper_aum_usd),
        "max_fx_age_days": MAX_FX_AGE_DAYS,
        "required_forward_periods": REQUIRED_FORWARD_PERIODS,
        "required_input_fields": list(REQUIRED_INPUT_FIELDS),
        "risk_config": dict(RiskConfig().__dict__),
        "sources": {
            name: os.path.normpath(str(Path(path).resolve()))
            for name, path in sorted(source_paths.items())
        },
    }
    _LAST_RELEASE_RUN_ATTESTATION = _build_release_run_attestation(
        document,
        candidate_ids=selected,
        contract=contract,
        source_before=source_before,
        source_after=source_after,
        output_path=output_path,
    )
    return document


def main(argv: Sequence[str] | None = None) -> int:
    global _LAST_RELEASE_RUN_ATTESTATION
    _LAST_RELEASE_RUN_ATTESTATION = None
    parser = argparse.ArgumentParser(description="Publish a fail-closed paper research-risk preflight")
    parser.add_argument("--forward-db", default=str(DEFAULT_FORWARD_DB))
    parser.add_argument("--dataset-evidence", default=str(DEFAULT_DATASET_EVIDENCE))
    parser.add_argument("--evaluations", default=str(DEFAULT_EVALUATIONS))
    parser.add_argument("--forward-evaluations", default=str(DEFAULT_FORWARD_EVALUATIONS))
    parser.add_argument("--forward-status", default=str(DEFAULT_FORWARD_STATUS))
    parser.add_argument("--fx-db", default=str(DEFAULT_FX_DB))
    parser.add_argument("--maintenance", default=str(DEFAULT_MAINTENANCE))
    parser.add_argument("--paper-aum-usd", type=float, default=DEFAULT_PAPER_AUM_USD)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        document = run_bundle(
            forward_db=args.forward_db,
            dataset_evidence=args.dataset_evidence,
            evaluations=args.evaluations,
            forward_evaluations=args.forward_evaluations,
            forward_status=args.forward_status,
            fx_db=args.fx_db,
            maintenance=args.maintenance,
            paper_aum_usd=args.paper_aum_usd,
            output=args.output,
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"paper research-risk bundle remains pending: {exc}", file=sys.stderr)
        return 1
    print(
        f"paper research-risk bundle: {len(document['alphas'])} alphas; "
        f"status={document['status']}; missing={','.join(document['missing_inputs'])}"
    )
    # PENDING is a valid fail-closed research/admission state carried by the
    # artifact, not a failed production job.  A successful compute + atomic
    # publish must keep Task Scheduler and release receipts healthy; only the
    # exception path above returns non-zero.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
