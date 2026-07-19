"""Publish and verify the read-only L2 forward-evidence clock.

This tool is deliberately diagnostic only: it neither advances paper-track time
nor creates an authority database.  Its reusable document construction is pure;
SQLite access and the optional CLI wall clock live at the boundary below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # direct ``python tools/...`` driver boundary
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data.admission_store import DataQualityAdmissionStore
from services.research.paper_trading.forward_store import ForwardLedger


class ForwardEvidenceError(RuntimeError):
    """The forward-evidence authority is unavailable or cannot be verified."""


_MAX_AGE_MS = 172_800_000
_MINIMUM_WEEKS = 8
_READINESS = "L2_DRY_RUN_ONLY"
_PRICE_BASIS = "PERP_MARK_PRICE"
_DATABASE = Path("data/cache/paper_track.db")
_ADMISSION_DATABASE = Path("data/cache/data_quality_admission.db")
_IMPLEMENTATION_FILES = (
    "services/research/paper_trading/forward.py",
    "services/research/paper_trading/forward_evidence.py",
    "services/research/paper_trading/forward_store.py",
    "tools/forward_track_advance.py",
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "forward_decision_bindings_no_update",
        "forward_decision_bindings_no_delete",
        "forward_decisions_identity_no_update",
        "forward_decisions_no_delete",
        "forward_realizations_no_update",
        "forward_realizations_no_delete",
        "forward_local_events_no_update",
        "forward_local_events_no_delete",
        "paper_periods_no_update",
        "paper_periods_no_delete",
    }
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _implementation_binding(repo_root: Path) -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for relative in _IMPLEMENTATION_FILES:
        path = repo_root / relative
        if not path.is_file():
            raise ForwardEvidenceError(f"bound implementation is unavailable: {relative}")
        payload = path.read_bytes()
        files[relative] = {"sha256": _sha256_bytes(payload), "size": len(payload)}
    return {"files": files, "sha256": _sha256_bytes(_canonical(files).encode("utf-8"))}


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ForwardEvidenceError("forward ledger database is unavailable")
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.DatabaseError as exc:
        raise ForwardEvidenceError("forward ledger database is unreadable") from exc


def _append_only_triggers_valid(path: Path) -> bool:
    try:
        connection = _read_only_connection(path)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        finally:
            connection.close()
    except ForwardEvidenceError:
        return False
    return {str(row["name"]) for row in rows} >= _REQUIRED_TRIGGERS


def _admission_summary(path: Path, *, observed_at_ms: int) -> dict[str, object]:
    if not path.is_file():
        return {
            "status": "UNAVAILABLE",
            "current_admission_ref": None,
            "expires_at_ms": None,
            "price_basis": None,
            "source": "DATA_QUALITY_ADMISSION_AUTHORITY",
        }
    try:
        authority = DataQualityAdmissionStore(str(path), create=False)
        try:
            if not authority.chain_valid():
                return {
                    "status": "UNAVAILABLE",
                    "current_admission_ref": None,
                    "expires_at_ms": None,
                    "price_basis": None,
                    "source": "DATA_QUALITY_ADMISSION_AUTHORITY",
                }
            decision = authority.latest_decision()
        finally:
            authority.close()
    except Exception:
        return {
            "status": "UNAVAILABLE",
            "current_admission_ref": None,
            "expires_at_ms": None,
            "price_basis": None,
            "source": "DATA_QUALITY_ADMISSION_AUTHORITY",
        }
    if decision is None:
        return {
            "status": "UNAVAILABLE",
            "current_admission_ref": None,
            "expires_at_ms": None,
            "price_basis": None,
            "source": "DATA_QUALITY_ADMISSION_AUTHORITY",
        }
    expiry = int(decision.recorded_at_ms) + _MAX_AGE_MS
    ref = {
        "admission_id": decision.admission_id,
        "evidence_sha256": decision.evidence_sha256,
        "decision_hash": decision.decision_hash,
        "recorded_at_ms": int(decision.recorded_at_ms),
        "expires_at_ms": expiry,
        "price_basis": decision.price_basis,
        "instrument_scope": list(decision.instrument_scope),
        "source": "DATA_QUALITY_ADMISSION_AUTHORITY",
        "verdict": decision.verdict,
    }
    if decision.verdict != "PASS" or decision.price_basis != _PRICE_BASIS:
        status = "BLOCKED"
    elif observed_at_ms > expiry:
        status = "STALE"
    else:
        status = "CURRENT"
    return {
        "status": status,
        "current_admission_ref": ref,
        "expires_at_ms": expiry,
        "price_basis": decision.price_basis,
        "source": "DATA_QUALITY_ADMISSION_AUTHORITY",
    }


def _status_for_admission(summary: Mapping[str, object]) -> str:
    status = summary["status"]
    if status == "CURRENT":
        return "ATTESTED"
    if status == "STALE":
        return "STALE"
    if status == "BLOCKED":
        return "BLOCKED"
    return "UNAVAILABLE"


def _unavailable_ledger_snapshot() -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], bool, bool]:
    return {"decisions": [], "realizations": []}, [], False, False


def _ledger_snapshot(path: Path) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], bool, bool]:
    if not path.is_file():
        return _unavailable_ledger_snapshot()
    try:
        ledger = ForwardLedger(str(path), create=False)
    except Exception:
        return _unavailable_ledger_snapshot()
    try:
        decision_chain_valid = ledger.decision_chain_valid()
        realization_chain_valid = ledger.realization_chain_valid()
        if not decision_chain_valid or not realization_chain_valid or not _append_only_triggers_valid(path):
            return _unavailable_ledger_snapshot()
        connection = _read_only_connection(path)
        try:
            attested_decisions = [
                dict(row)
                for row in connection.execute(
                    "SELECT d.alpha_id,d.decided_ts,d.source,b.admission_id,"
                    "b.admission_evidence_sha256,b.admission_decision_hash,"
                    "b.admission_recorded_at_ms,b.price_basis,b.instrument_scope_json "
                    "FROM forward_decisions AS d INNER JOIN forward_decision_bindings AS b "
                    "ON b.alpha_id=d.alpha_id AND b.decided_ts=d.decided_ts "
                    "ORDER BY d.alpha_id,d.decided_ts"
                ).fetchall()
            ]
        finally:
            connection.close()
        realizations = ledger.attested_realizations()
        legacy = ledger.legacy_decisions()
        return ({"decisions": attested_decisions, "realizations": realizations}, legacy, decision_chain_valid, realization_chain_valid)
    except Exception:
        return _unavailable_ledger_snapshot()
    finally:
        ledger._conn.close()


def _admission_ref_from_binding(row: Mapping[str, Any]) -> dict[str, object]:
    return {
        "admission_id": str(row["admission_id"]),
        "evidence_sha256": str(row["admission_evidence_sha256"]),
        "decision_hash": str(row["admission_decision_hash"]),
        "recorded_at_ms": int(row["admission_recorded_at_ms"]),
        "price_basis": str(row["price_basis"]),
        "instrument_scope": json.loads(str(row["instrument_scope_json"])),
    }


def _build_document(
    *,
    observed_at_ms: int,
    snapshot: Mapping[str, Sequence[Mapping[str, Any]]],
    legacy: Sequence[Mapping[str, Any]],
    decision_chain_valid: bool,
    realization_chain_valid: bool,
    admission_summary: Mapping[str, object],
    implementation: Mapping[str, object],
) -> dict[str, object]:
    decisions = snapshot["decisions"]
    realizations = snapshot["realizations"]
    alpha_ids = sorted({str(row["alpha_id"]) for row in decisions} | {str(row["alpha_id"]) for row in legacy})
    alphas: list[dict[str, object]] = []
    for alpha_id in alpha_ids:
        alpha_decisions = [row for row in decisions if row["alpha_id"] == alpha_id]
        alpha_realizations = [row for row in realizations if row["alpha_id"] == alpha_id]
        as_of_weeks = sorted({int(row["as_of_ts"]) for row in alpha_realizations})
        pending = len(alpha_decisions) - len(alpha_realizations)
        if pending < 0:
            raise ForwardEvidenceError("attested realization count exceeds decisions")
        alphas.append(
            {
                "alpha_id": alpha_id,
                "status": _status_for_admission(admission_summary),
                "attested_decisions": len(alpha_decisions),
                "attested_pending": pending,
                "attested_realizations": len(alpha_realizations),
                "attested_weeks": len(as_of_weeks),
                "attested_as_of_weeks": as_of_weeks,
                "legacy_unattested_decisions": sum(1 for row in legacy if row["alpha_id"] == alpha_id),
                "decision_sources": sorted({str(row["source"]) for row in alpha_decisions}),
                "realization_sources": sorted({str(row["source"]) for row in alpha_realizations}),
                "decision_admission_refs": [_admission_ref_from_binding(row) for row in alpha_decisions],
                "current_admission_ref": admission_summary["current_admission_ref"],
            }
        )
    counts = {
        "attested_decisions": len(decisions),
        "attested_pending": sum(row["attested_pending"] for row in alphas),
        "attested_realizations": len(realizations),
        "legacy_unattested_decisions": len(legacy),
    }
    return {
        "schema_version": 1,
        "observed_at_ms": observed_at_ms,
        "max_age_ms": _MAX_AGE_MS,
        "readiness": _READINESS,
        "source_database": _DATABASE.as_posix(),
        "price_basis": _PRICE_BASIS,
        "decision_chain_valid": decision_chain_valid,
        "realization_chain_valid": realization_chain_valid,
        "counts": counts,
        "alphas": alphas,
        "data_admission": dict(admission_summary),
        "implementation": dict(implementation),
        "evidence_gate": {
            "minimum_weeks": _MINIMUM_WEEKS,
            "eligible": (
                decision_chain_valid
                and realization_chain_valid
                and admission_summary["status"] == "CURRENT"
                and bool(alphas)
                and all(row["attested_weeks"] >= _MINIMUM_WEEKS for row in alphas)
            ),
        },
        "honesty_note": "Only v2 authority-bound realizations count; legacy and backfilled rows never count.",
    }


def build_forward_evidence(
    repo_root: Path | str,
    *,
    observed_at_ms: int,
    database: Path | str | None = None,
    admission_database: Path | str | None = None,
) -> dict[str, object]:
    """Read the authorities once and build a deterministic evidence document."""
    if isinstance(observed_at_ms, bool) or not isinstance(observed_at_ms, int) or observed_at_ms < 0:
        raise ValueError("observed_at_ms must be a non-negative integer")
    root = Path(repo_root).resolve()
    database_path = Path(database).resolve() if database is not None else root / _DATABASE
    admission_path = (
        Path(admission_database).resolve()
        if admission_database is not None
        else root / _ADMISSION_DATABASE
    )
    snapshot, legacy, decision_chain_valid, realization_chain_valid = _ledger_snapshot(database_path)
    admission_summary = _admission_summary(admission_path, observed_at_ms=observed_at_ms)
    return _build_document(
        observed_at_ms=observed_at_ms,
        snapshot=snapshot,
        legacy=legacy,
        decision_chain_valid=decision_chain_valid,
        realization_chain_valid=realization_chain_valid,
        admission_summary=admission_summary,
        implementation=_implementation_binding(root),
    )


def verify_forward_evidence(
    document: Mapping[str, object],
    repo_root: Path | str,
    *,
    database: Path | str | None = None,
    admission_database: Path | str | None = None,
) -> dict[str, object]:
    """Fail closed unless a stored document exactly matches current authority."""
    if not isinstance(document, Mapping) or isinstance(document.get("observed_at_ms"), bool):
        raise ForwardEvidenceError("forward evidence document is malformed")
    observed_at_ms = document.get("observed_at_ms")
    if not isinstance(observed_at_ms, int):
        raise ForwardEvidenceError("forward evidence observation time is malformed")
    rebuilt = build_forward_evidence(
        repo_root,
        observed_at_ms=observed_at_ms,
        database=database,
        admission_database=admission_database,
    )
    if not rebuilt["decision_chain_valid"] or not rebuilt["realization_chain_valid"]:
        raise ForwardEvidenceError("forward ledger authority is unavailable or invalid")
    if rebuilt["data_admission"]["status"] != "CURRENT":
        raise ForwardEvidenceError("current data-admission authority is unavailable")
    if _canonical(dict(document)) != _canonical(rebuilt):
        raise ForwardEvidenceError("forward evidence document no longer matches authority")
    return rebuilt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="publish or verify L2 forward evidence")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--database")
    parser.add_argument("--admission-database")
    parser.add_argument("--output")
    parser.add_argument("--observed-at-ms", type=int)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    output = Path(args.output).resolve() if args.output else root / "evidence" / "forward_evidence_baseline.json"
    try:
        if args.check:
            if not output.is_file():
                raise ForwardEvidenceError("forward evidence output is unavailable")
            document = json.loads(output.read_text(encoding="utf-8"))
            verify_forward_evidence(
                document,
                root,
                database=args.database,
                admission_database=args.admission_database,
            )
            print("forward evidence: VERIFIED")
        else:
            observed_at_ms = args.observed_at_ms
            if observed_at_ms is None:
                observed_at_ms = int(time.time() * 1000)
            document = build_forward_evidence(
                root,
                observed_at_ms=observed_at_ms,
                database=args.database,
                admission_database=args.admission_database,
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"forward evidence: {document['data_admission']['status']}")
        return 0
    except (ForwardEvidenceError, ValueError, json.JSONDecodeError) as exc:
        print(f"forward evidence: BLOCKED ({type(exc).__name__})")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
