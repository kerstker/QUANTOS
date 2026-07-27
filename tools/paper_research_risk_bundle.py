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
from pathlib import Path
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
DEFAULT_OUTPUT = Path("data/cache/paper_research_risk_inputs.json")


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
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        document = build_pending_bundle(
            forward_db=args.forward_db,
            dataset_evidence=args.dataset_evidence,
        )
        _write(Path(args.output), document)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"paper research-risk bundle remains pending: {exc}", file=sys.stderr)
        return 1
    print(
        f"paper research-risk preflight: {len(document['alphas'])} alphas; "
        f"status={document['status']}; missing={','.join(document['missing_inputs'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
