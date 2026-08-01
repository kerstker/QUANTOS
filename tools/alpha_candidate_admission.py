"""Admit verified factory PASS survivors to the L2 forward-paper registry.

This is the only automatic discovery hand-off.  It verifies every retained
evaluation against the immutable experiment registry and grants only
forward-paper observation.  It cannot promote lifecycle state, connect to a
broker, submit orders, read credentials, or authorize live trading.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from services.research import alpha_expression as ax
from services.research.paper_candidate_registry import (
    PaperCandidate,
    PaperCandidateRegistry,
)
from tools.cache_io import atomic_write_json
from tools.search_trial_registry import production_experiment_registry


DEFAULT_FACTORY = Path("data/cache/crypto_factory_loop.json")
DEFAULT_REGISTRY = Path("data/cache/paper_candidate_registry.db")
DEFAULT_OUTPUT = Path("data/cache/alpha_candidate_admission.json")
FACTORY_SCHEMA_VERSION = 2


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"factory artifact is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("factory artifact is not an object")
    return value


def _candidate_from_verified_row(
    row: Mapping[str, Any],
    *,
    experiment_registry: Any,
) -> PaperCandidate:
    required = (
        "alpha_id",
        "expr",
        "definition_hash",
        "source_search_id",
        "evaluation_run_id",
        "dataset_version",
    )
    missing = [name for name in required if not isinstance(row.get(name), str) or not str(row[name])]
    if missing:
        raise RuntimeError(f"candidate admission fields are missing: {missing}")
    alpha_id = str(row["alpha_id"])
    expression = str(row["expr"])
    if ax.alpha_id(ax.deserialize(expression)) != alpha_id:
        raise RuntimeError("candidate expression identity mismatch")
    run_id = str(row["evaluation_run_id"])
    if experiment_registry.verify_evaluation(run_id) is not True:
        raise RuntimeError("candidate evaluation receipt is not verified")
    stored = experiment_registry.get_evaluation(run_id)
    if not isinstance(stored, Mapping):
        raise RuntimeError("candidate evaluation receipt is unavailable")
    bindings = {
        "alpha_id": alpha_id,
        "search_id": str(row["source_search_id"]),
        "dataset_version": str(row["dataset_version"]),
        "config_hash": str(row["definition_hash"]),
    }
    mismatches = [name for name, expected in bindings.items() if stored.get(name) != expected]
    if mismatches:
        raise RuntimeError(f"candidate evaluation binding mismatch: {mismatches}")
    if stored.get("verdict") != "PASS":
        raise RuntimeError("candidate evaluation verdict is not PASS")
    recorded_ts = stored.get("recorded_ts")
    if type(recorded_ts) is not int or recorded_ts < 0:
        raise RuntimeError("candidate evaluation timestamp is invalid")
    quantile = row.get("quantile", 0.3)
    cadence_days = row.get("cadence_days", 7)
    return PaperCandidate(
        alpha_id=alpha_id,
        expression=expression,
        definition_hash=str(row["definition_hash"]),
        source_search_id=str(row["source_search_id"]),
        evaluation_run_id=run_id,
        dataset_version=str(row["dataset_version"]),
        quantile=float(quantile),
        cadence_days=int(cadence_days),
        admitted_at_ms=recorded_ts,
    )


def admit_factory_candidates(
    document: Mapping[str, Any],
    *,
    candidate_registry: PaperCandidateRegistry,
    experiment_registry: Any,
    admitted_at_ms: int,
) -> dict[str, Any]:
    if document.get("schema_version") != FACTORY_SCHEMA_VERSION:
        raise RuntimeError("factory artifact schema is not admission-capable")
    rows = document.get("eligible_discoveries")
    if not isinstance(rows, list):
        raise RuntimeError("factory eligible_discoveries is missing")
    admitted: list[str] = []
    replayed: list[str] = []
    rejected: list[dict[str, str]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            rejected.append({"alpha_id": "UNKNOWN", "reason": "candidate row is not an object"})
            continue
        alpha_id = str(raw.get("alpha_id", "UNKNOWN"))
        try:
            candidate = _candidate_from_verified_row(
                raw,
                experiment_registry=experiment_registry,
            )
            inserted = candidate_registry.admit(candidate)
        except (RuntimeError, TypeError, ValueError, KeyError) as exc:
            rejected.append({"alpha_id": alpha_id, "reason": str(exc)[:240]})
            continue
        (admitted if inserted else replayed).append(candidate.alpha_id)
    chain_valid = candidate_registry.chain_valid()
    return {
        "schema_version": 1,
        "flow": "VERIFIED_FACTORY_TO_FORWARD_PAPER",
        "generated_at_ms": int(admitted_at_ms),
        "status": "PASS" if chain_valid and not rejected else "BLOCKED",
        "eligible_count": len(rows),
        "admitted": admitted,
        "replayed": replayed,
        "rejected": rejected,
        "registry": candidate_registry.snapshot(),
        "safety": {
            "paper_only": True,
            "lifecycle_promotion": False,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }


def run(
    *,
    factory_path: str | Path = DEFAULT_FACTORY,
    registry_path: str | Path = DEFAULT_REGISTRY,
    output_path: str | Path = DEFAULT_OUTPUT,
    admitted_at_ms: int | None = None,
) -> dict[str, Any]:
    observed_at = int(admitted_at_ms if admitted_at_ms is not None else time.time_ns() // 1_000_000)
    document = _read(Path(factory_path))
    with PaperCandidateRegistry(registry_path) as candidates:
        # An explicit empty eligible set is a complete, valid factory outcome.
        # Do not require the production experiment DB merely to attest that no
        # candidate was handed off; isolated research-runner tests and clean
        # deployments must be able to publish this honest zero state.
        if document.get("eligible_discoveries") == []:
            report = admit_factory_candidates(
                document,
                candidate_registry=candidates,
                experiment_registry=None,  # unused because the eligible set is empty
                admitted_at_ms=observed_at,
            )
        else:
            with production_experiment_registry() as experiments:
                report = admit_factory_candidates(
                    document,
                    candidate_registry=candidates,
                    experiment_registry=experiments,
                    admitted_at_ms=observed_at,
                )
    atomic_write_json(str(output_path), report, indent=2)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factory", default=str(DEFAULT_FACTORY))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        report = run(
            factory_path=args.factory,
            registry_path=args.registry,
            output_path=args.output,
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"candidate admission blocked: {exc}")
        return 2
    print(
        "candidate admission: "
        f"status={report['status']} admitted={len(report['admitted'])} "
        f"replayed={len(report['replayed'])} rejected={len(report['rejected'])}"
    )
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_FACTORY",
    "DEFAULT_OUTPUT",
    "DEFAULT_REGISTRY",
    "FACTORY_SCHEMA_VERSION",
    "admit_factory_candidates",
    "run",
]
