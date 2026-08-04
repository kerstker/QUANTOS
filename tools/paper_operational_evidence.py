"""Collect continuous-window and isolated fault evidence for Production Paper."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from collections.abc import Mapping, Sequence
from typing import Any
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from services.execution.paper_realtime_store import (
    PaperRealtimeStore,
    PaperRealtimeStoreError,
)
from services.monitoring.paper_operational_evidence import (
    PRODUCTION_PAPER_POLICY_V1,
    PaperOperationalEvidenceStore,
    assess_current_shadow_cycle,
    assess_shadow_artifact,
    evaluate_operational_window,
)
from tools.cache_io import atomic_write_json
from tools.paper_candidate_universe import (
    paper_candidate_ids,
    shadow_paper_candidate_ids,
)


DEFAULT_STORE = "data/cache/paper_operational_evidence.db"
DEFAULT_OUTPUT = "data/cache/paper_operational_evidence.json"
DEFAULT_SHADOW_STATUS = "data/cache/paper_shadow_realtime_status.json"
DEFAULT_RECEIPT_ROOT = ".runtime/l2-release-runs"
_RELEASE_PATTERN_LENGTH = 40


def _shadow_candidate_ids() -> tuple[str, ...]:
    """Use the same routed shadow scope as the two upstream workers."""
    population = tuple(paper_candidate_ids())
    return shadow_paper_candidate_ids(candidate_ids=population)


def _load_object(path: str | Path) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _iso_ms(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return None


def _sha256_file(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _latest_receipt(receipt_root: str | Path) -> tuple[Path, dict[str, Any]] | None:
    candidates: list[tuple[int, Path, dict[str, Any]]] = []
    for path in Path(receipt_root).glob("*-paper_shadow_realtime.json"):
        receipt = _load_object(path)
        if not receipt or receipt.get("module") != "paper_shadow_realtime":
            continue
        finished = _iso_ms(receipt.get("finished_at"))
        if finished is not None:
            candidates.append((finished, path, receipt))
    if not candidates:
        return None
    _finished, path, receipt = max(candidates, key=lambda item: item[0])
    return path, receipt


def _cycle_payload(
    *,
    receipt_path: Path,
    receipt: Mapping[str, Any],
    shadow: Mapping[str, Any] | None,
    expected_candidates: Sequence[str],
    collector_release_commit: str,
) -> tuple[str, int, dict[str, Any]]:
    started_at_ms = _iso_ms(receipt.get("started_at"))
    finished_at_ms = _iso_ms(receipt.get("finished_at"))
    if started_at_ms is None or finished_at_ms is None or finished_at_ms < started_at_ms:
        raise ValueError("release receipt timestamps are invalid")
    receipt_sha = receipt.get("receipt_sha256")
    if not isinstance(receipt_sha, str) or len(receipt_sha) != 64:
        raise ValueError("release receipt identity is invalid")
    release_commit = receipt.get("release_commit")
    release_valid = (
        isinstance(release_commit, str)
        and len(release_commit) == _RELEASE_PATTERN_LENGTH
        and receipt.get("release_verified") is True
    )
    collector_release_valid = bool(
        len(collector_release_commit) == _RELEASE_PATTERN_LENGTH
        and all(char in "0123456789abcdef" for char in collector_release_commit)
    )
    bound = False
    artifact_accepted = False
    artifact_reasons: list[str] = ["CURRENT_ARTIFACT_NOT_BOUND_TO_RECEIPT"]
    artifact_sha = None
    observed_at_ms = None
    candidate_count = 0
    ledger_chain_valid = False
    reconciled = False
    artifact_refreshed = False
    if isinstance(shadow, Mapping):
        artifact_sha = hashlib.sha256(
            json.dumps(
                dict(shadow),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        upstream = shadow.get("upstream_cycle")
        if isinstance(upstream, Mapping):
            upstream_start = upstream.get("started_at_ms")
            upstream_finish = upstream.get("finished_at_ms")
            bound = bool(
                isinstance(upstream_start, int)
                and isinstance(upstream_finish, int)
                and started_at_ms <= upstream_start <= upstream_finish <= finished_at_ms
            )
            artifact_refreshed = upstream.get("artifact_refreshed") is True
            if bound:
                artifact_accepted, reasons = assess_shadow_artifact(
                    shadow,
                    expected_candidates=expected_candidates,
                    upstream_exit_code=int(upstream.get("exit_code", -1)),
                )
                artifact_reasons = list(reasons)
        alphas = shadow.get("alphas")
        candidate_count = len(alphas) if isinstance(alphas, Mapping) else 0
        if isinstance(alphas, Mapping) and alphas:
            residuals = [
                row.get("reconciliation_residual")
                for row in alphas.values()
                if isinstance(row, Mapping)
            ]
            reconciled = bool(
                len(residuals) == len(alphas)
                and all(
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isfinite(float(value))
                    and abs(float(value)) <= 1e-6
                    for value in residuals
                )
            )
        persistence = shadow.get("persistence")
        ledger_chain_valid = bool(
            isinstance(persistence, Mapping) and persistence.get("chain_valid") is True
        )
        observed = shadow.get("observed_at")
        observed_at_ms = observed if isinstance(observed, int) else None
    receipt_ok = bool(
        receipt.get("status") == "COMPLETED"
        and receipt.get("exit_code") == 0
        and release_valid
    )
    accepted = bool(
        receipt_ok
        and collector_release_valid
        and bound
        and artifact_refreshed
        and artifact_accepted
        and ledger_chain_valid
        and reconciled
    )
    reasons: list[str] = []
    if not receipt_ok:
        reasons.append("RELEASE_RECEIPT_NOT_SUCCESSFUL")
    if not collector_release_valid:
        reasons.append("COLLECTOR_RELEASE_NOT_PINNED")
    if not bound:
        reasons.append("CURRENT_ARTIFACT_NOT_BOUND_TO_RECEIPT")
    if not artifact_refreshed:
        reasons.append("CURRENT_ARTIFACT_NOT_REFRESHED")
    reasons.extend(reason for reason in artifact_reasons if reason not in reasons)
    if not ledger_chain_valid:
        reasons.append("PAPER_MARK_CHAIN_INVALID")
    if not reconciled:
        reasons.append("PAPER_RECONCILIATION_INVALID")
    payload = {
        "started_at_ms": started_at_ms,
        "finished_at_ms": finished_at_ms,
        "release_commit": release_commit,
        "release_verified": release_valid,
        "collector_release_commit": collector_release_commit,
        "collector_release_verified": collector_release_valid,
        "receipt_status": receipt.get("status"),
        "receipt_exit_code": receipt.get("exit_code"),
        "receipt_file_sha256": _sha256_file(receipt_path),
        "artifact_sha256": artifact_sha,
        "artifact_bound": bound,
        "artifact_refreshed": artifact_refreshed,
        "artifact_observed_at_ms": observed_at_ms,
        "candidate_count": candidate_count,
        "candidate_coverage_complete": candidate_count == len(expected_candidates),
        "ledger_chain_valid": ledger_chain_valid,
        "reconciled": reconciled,
        "accepted": accepted,
        "reasons": reasons,
        "safety": {
            "paper_only": True,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "broker_order_submission": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
    }
    return receipt_sha, finished_at_ms, payload


def _valid_shadow_fixture(expected: Sequence[str]) -> dict[str, Any]:
    return {
        "errors": {},
        "verdict": {
            "paper_mark_ready": True,
            "backtest_reference_present_for_all_execution_candidates": False,
        },
        "safety": {
            "paper_only": True,
            "broker_connectivity": False,
            "broker_order_submission": False,
        },
        "alphas": {item: {"status": "PAPER_MARKED"} for item in expected},
    }


def run_isolated_fault_matrix() -> dict[str, bool]:
    """Exercise real store and artifact gates only inside a temporary directory."""
    expected = ("alpha_a", "alpha_b")
    results: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="quantos-paper-drill-") as tmp:
        store_path = str(Path(tmp) / "paper_marks.db")
        store = PaperRealtimeStore(store_path, create=True)
        snapshot = {
            "alpha_id": "alpha_a",
            "observed_at": 1,
            "status": "PAPER_MARKED",
            "paper_only": True,
        }
        first = store.append_snapshot(snapshot)
        second = store.append_snapshot(snapshot)
        results["EXACT_RETRY_IDEMPOTENT"] = first is True and second is False and len(store.rows()) == 1
        conflict_rejected = False
        try:
            store.append_snapshot({**snapshot, "status": "CONFLICT"})
        except PaperRealtimeStoreError:
            conflict_rejected = True
        results["CONFLICTING_DUPLICATE_REJECTED"] = conflict_rejected
        store.close()
        conn = sqlite3.connect(store_path)
        conn.execute("DROP TRIGGER paper_mark_snapshots_no_update")
        conn.execute(
            "UPDATE paper_mark_snapshots SET payload_json=? WHERE seq=1",
            ('{"tampered":true}',),
        )
        conn.commit()
        conn.close()
        tampered = PaperRealtimeStore(store_path, create=False)
        results["HASH_CHAIN_TAMPER_DETECTED"] = tampered.chain_valid() is False
        tampered.close()

    valid = _valid_shadow_fixture(expected)
    stale_ok, _reasons = assess_current_shadow_cycle(
        valid,
        expected_candidates=expected,
        upstream_exit_code=1,
        artifact_refreshed=False,
    )
    results["STALE_CYCLE_REJECTED"] = stale_ok is False
    unsafe = json.loads(json.dumps(valid))
    unsafe["safety"]["broker_connectivity"] = True
    unsafe_ok, _ = assess_shadow_artifact(
        unsafe, expected_candidates=expected, upstream_exit_code=1
    )
    results["UNSAFE_BROKER_BOUNDARY_REJECTED"] = unsafe_ok is False
    incomplete = json.loads(json.dumps(valid))
    incomplete["alphas"].pop("alpha_b")
    incomplete_ok, _ = assess_shadow_artifact(
        incomplete, expected_candidates=expected, upstream_exit_code=1
    )
    results["INCOMPLETE_CANDIDATE_SET_REJECTED"] = incomplete_ok is False
    return results


def collect(
    *,
    store_path: str = DEFAULT_STORE,
    output_path: str = DEFAULT_OUTPUT,
    shadow_status_path: str = DEFAULT_SHADOW_STATUS,
    receipt_root: str = DEFAULT_RECEIPT_ROOT,
    now_ms: int | None = None,
) -> dict[str, Any]:
    observed_now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    store = PaperOperationalEvidenceStore(store_path, create=True)
    new_events = 0
    try:
        collector_release = os.environ.get("QUANTOS_RELEASE_COMMIT", "UNPINNED")
        latest = _latest_receipt(receipt_root)
        if latest is not None:
            receipt_path, receipt = latest
            event_id, occurred_at_ms, payload = _cycle_payload(
                receipt_path=receipt_path,
                receipt=receipt,
                shadow=_load_object(shadow_status_path),
                expected_candidates=_shadow_candidate_ids(),
                collector_release_commit=collector_release,
            )
            new_events += int(
                store.append(
                    event_type="CYCLE",
                    event_id=event_id,
                    occurred_at_ms=occurred_at_ms,
                    payload=payload,
                )
            )
        existing_drill_times = {
            event["event_id"]: event["occurred_at_ms"]
            for event in store.events("DRILL")
        }
        for name, passed in run_isolated_fault_matrix().items():
            event_id = hashlib.sha256(
                f"{PRODUCTION_PAPER_POLICY_V1.policy_version}:{collector_release}:{name}".encode(
                    "utf-8"
                )
            ).hexdigest()
            new_events += int(
                store.append(
                    event_type="DRILL",
                    event_id=event_id,
                    occurred_at_ms=existing_drill_times.get(event_id, observed_now),
                    payload={
                        "name": name,
                        "passed": passed,
                        "isolated": True,
                        "collector_release_commit": collector_release,
                    },
                )
            )
        events = store.events()
        chain_valid = store.chain_valid()
        window = evaluate_operational_window(
            events,
            now_ms=observed_now,
            chain_valid=chain_valid,
        )
    finally:
        store.close()
    report = {
        "schema_version": 1,
        "flow": "PRODUCTION_PAPER_OPERATIONAL_EVIDENCE",
        "generated_at_ms": observed_now,
        "policy": {
            "version": PRODUCTION_PAPER_POLICY_V1.policy_version,
            "cadence_ms": PRODUCTION_PAPER_POLICY_V1.cadence_ms,
            "max_gap_ms": PRODUCTION_PAPER_POLICY_V1.max_gap_ms,
            "smoke_minimum_span_ms": PRODUCTION_PAPER_POLICY_V1.smoke_minimum_span_ms,
            "production_minimum_span_ms": PRODUCTION_PAPER_POLICY_V1.production_minimum_span_ms,
            "production_minimum_successes": PRODUCTION_PAPER_POLICY_V1.production_minimum_successes,
            "minimum_success_ratio": PRODUCTION_PAPER_POLICY_V1.minimum_success_ratio,
        },
        "new_events": new_events,
        "evidence_store": store_path,
        "chain_valid": chain_valid,
        "window": window,
        "verdict": {
            "engineering_smoke_ready": window["engineering_smoke_ready"],
            "production_window_ready": window["production_window_ready"],
            "paper_operation_continues": True,
            "formal_paper_admission_changed": False,
            "promotion_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "safety": {
            "paper_only": True,
            "public_market_data_only": True,
            "fault_injection_isolated": True,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "broker_order_submission": False,
        },
    }
    atomic_write_json(output_path, report, indent=2)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--shadow-status", default=DEFAULT_SHADOW_STATUS)
    parser.add_argument("--receipt-root", default=DEFAULT_RECEIPT_ROOT)
    args = parser.parse_args(argv)
    try:
        report = collect(
            store_path=args.store,
            output_path=args.output,
            shadow_status_path=args.shadow_status,
            receipt_root=args.receipt_root,
        )
    except Exception as exc:
        print(f"paper_operational_evidence: {type(exc).__name__}: {str(exc)[:240]}")
        return SCHEDULE_EXIT_FAILURE
    print(
        json.dumps(
            {
                "status": report["window"]["status"],
                "accepted_cycles": report["window"]["accepted_cycle_count"],
                "history_span_ms": report["window"]["history_span_ms"],
                "engineering_smoke_ready": report["window"]["engineering_smoke_ready"],
                "production_window_ready": report["window"]["production_window_ready"],
                "blockers": report["window"]["blockers"],
            },
            ensure_ascii=False,
        )
    )
    return (
        SCHEDULE_EXIT_SUCCESS
        if report["window"]["production_window_ready"]
        else SCHEDULE_EXIT_SAFE_BLOCKED
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_OUTPUT",
    "DEFAULT_RECEIPT_ROOT",
    "DEFAULT_SHADOW_STATUS",
    "DEFAULT_STORE",
    "collect",
    "main",
    "run_isolated_fault_matrix",
]
