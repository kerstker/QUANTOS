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
DEFAULT_FX_DB = Path("data/cache/fx.db")
DEFAULT_MAINTENANCE = Path("data/cache/alpha_maintenance_status.json")
DEFAULT_OUTPUT = Path("data/cache/paper_research_risk_inputs.json")
DEFAULT_PAPER_AUM_USD = 1_000_000.0
MAX_FX_AGE_DAYS = 8
REQUIRED_FORWARD_PERIODS = 8


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


def _metadata_from_evaluation(
    *,
    deployed_alpha_id: str,
    evaluation_row: Mapping[str, Any],
    decision_recorded_at: int,
    forward_periods: int,
    maintenance: Mapping[str, Any],
) -> dict[str, Any]:
    evaluation = evaluation_row.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise RuntimeError("verified evaluation payload is missing")
    as_of = evaluation.get("as_of")
    if not isinstance(as_of, int) or as_of > int(decision_recorded_at):
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


def _write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish a fail-closed paper research-risk preflight")
    parser.add_argument("--forward-db", default=str(DEFAULT_FORWARD_DB))
    parser.add_argument("--dataset-evidence", default=str(DEFAULT_DATASET_EVIDENCE))
    parser.add_argument("--evaluations", default=str(DEFAULT_EVALUATIONS))
    parser.add_argument("--fx-db", default=str(DEFAULT_FX_DB))
    parser.add_argument("--maintenance", default=str(DEFAULT_MAINTENANCE))
    parser.add_argument("--paper-aum-usd", type=float, default=DEFAULT_PAPER_AUM_USD)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        document = build_research_risk_bundle(
            forward_db=args.forward_db,
            evaluations=args.evaluations,
            fx_db=args.fx_db,
            maintenance=args.maintenance,
            paper_aum_usd=args.paper_aum_usd,
        )
        _write(Path(args.output), document)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"paper research-risk bundle remains pending: {exc}", file=sys.stderr)
        return 1
    print(
        f"paper research-risk bundle: {len(document['alphas'])} alphas; "
        f"status={document['status']}; missing={','.join(document['missing_inputs'])}"
    )
    return 0 if document["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
