#!/usr/bin/env python
"""Report the trial ledger's integrity and its HONEST provenance coverage (727 §7).

Read-only. Opens the production ledger with `mode=ro`, recomputes every hash
chain, and reports what is genuinely recorded versus what is still bare. It does
not migrate, backfill, or repair — a ledger that fails a check must be reported
as failing, not fixed on the way past.

The coverage numbers are the point. §7.1 demands ~28 TrialRecord fields; the
mechanism now exists, but only trials written THROUGH it carry provenance.
Legacy config-hash-only rows are reported as `bare_trials`, so nobody can read
this artifact as "§7 complete" while most of the population is still thin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data" / "cache" / "experiment_registry.db"
DEFAULT_OUTPUT = ROOT / "evidence" / "trial_ledger_integrity.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_VERSION = 1
ARTIFACT_MODE = "TRIAL_LEDGER_INTEGRITY_READ_ONLY"

# The §7.1 TrialRecord fields, split by where they can come from. `provenance`
# fields exist only for rows written through record_trial_provenance().
_TRIAL_RECORD_FIELDS = (
    "trial_id",
    "parent_trial_id",
    "research_session_id",
    "actor_id",
    "actor_type",
    "model_name",
    "model_version",
    "prompt_hash",
    "hypothesis_id",
    "factor_ids",
    "alpha_expression",
    "canonical_expression",
    "expression_hash",
    "parameter_set",
    "parameter_hash",
    "dataset_versions",
    "universe_version",
    "cost_model_version",
    "validation_policy_version",
    "code_hash",
    "started_at",
    "completed_at",
    "status",
    "metrics",
    "failure_reasons",
    "artifact_paths",
    "artifact_hashes",
    "viewed_oos_boundaries",
    "prev_hash",
    "entry_hash",
)

_APPEND_ONLY_TABLES = (
    "trials",
    "evaluations",
    "search_attempt_events",
    "search_candidates",
    "search_evaluations",
    "trial_provenance",
)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else "UNKNOWN"


def _read_only_connection(ledger: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        ledger.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.0
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _canonical_hash(payload: Any) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _append_only_guards(connection: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    """Which append-only guards are actually installed, per table."""
    trigger_sql = [
        " ".join(str(row[0]).upper().split())
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL"
        )
    ]
    result: dict[str, Any] = {}
    for table in _APPEND_ONLY_TABLES:
        if table not in tables:
            result[table] = "TABLE_ABSENT"
            continue
        guarded = []
        for operation in ("UPDATE", "DELETE"):
            marker = f"BEFORE {operation} ON {table.upper()}"
            guarded.append(
                any(
                    marker in sql and "RAISE" in sql
                    for sql in trigger_sql
                )
            )
        result[table] = "GUARDED" if all(guarded) else "UNGUARDED"
    return result


def _provenance_state(
    connection: sqlite3.Connection, tables: set[str]
) -> dict[str, Any]:
    """Recompute the provenance chain and measure coverage against `trials`."""
    trial_hashes = {
        str(row["config_hash"])
        for row in connection.execute("SELECT config_hash FROM trials")
    }
    if "trial_provenance" not in tables:
        return {
            "table_present": False,
            "chain_valid": None,
            "chain_head": None,
            "attempts_recorded": 0,
            "configs_with_provenance": 0,
            "bare_trials": len(trial_hashes),
            "coverage_ratio": 0.0,
            "max_oos_peek_count": None,
            "actor_type_counts": {},
            "status_counts": {},
            "reason": "PROVENANCE_SCHEMA_NOT_YET_MIGRATED",
        }

    previous = ""
    chain_valid = True
    failure: str | None = None
    covered: set[str] = set()
    attempts = 0
    max_peeks = 0
    actor_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}

    for row in connection.execute(
        "SELECT provenance_seq,trial_id,config_hash,actor_type,status,"
        " oos_peek_count,record_json,prev_entry_hash,entry_hash"
        " FROM trial_provenance ORDER BY provenance_seq"
    ):
        attempts += 1
        try:
            record = json.loads(row["record_json"])
        except (TypeError, ValueError):
            chain_valid = False
            failure = f"CORRUPT_RECORD_JSON:{row['trial_id']}"
            break
        if row["prev_entry_hash"] != previous:
            chain_valid = False
            failure = f"CHAIN_BREAK:{row['trial_id']}"
            break
        expected = _canonical_hash({"entry": record, "prev_hash": previous})
        if row["entry_hash"] != expected:
            chain_valid = False
            failure = f"ENTRY_HASH_MISMATCH:{row['trial_id']}"
            break
        if str(row["config_hash"]) not in trial_hashes:
            chain_valid = False
            failure = f"CONFIG_ABSENT_FROM_TRIALS:{row['trial_id']}"
            break
        previous = expected
        covered.add(str(row["config_hash"]))
        max_peeks = max(max_peeks, int(row["oos_peek_count"]))
        actor = str(row["actor_type"])
        status = str(row["status"])
        actor_counts[actor] = actor_counts.get(actor, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1

    total = len(trial_hashes)
    return {
        "table_present": True,
        "chain_valid": chain_valid,
        "chain_head": previous if chain_valid else None,
        "attempts_recorded": attempts,
        "configs_with_provenance": len(covered),
        "bare_trials": max(0, total - len(covered)),
        "coverage_ratio": round(len(covered) / total, 6) if total else 0.0,
        "max_oos_peek_count": max_peeks if attempts else None,
        "actor_type_counts": dict(sorted(actor_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "reason": failure,
    }


def _family_testing_available() -> dict[str, Any]:
    """Is the §7.3 family hierarchy importable, and is it WIRED to a gate?"""
    try:
        from services.alpha import family_testing  # noqa: F401
    except Exception as exc:  # pragma: no cover - import failure is the report
        return {"available": False, "reason": f"IMPORT_FAILED:{exc}"}
    exported = [
        name
        for name in (
            "assign_families",
            "simes_pvalue",
            "hierarchical_fdr",
            "family_adjusted_trial_count",
            "marginal_ir",
        )
        if hasattr(family_testing, name)
    ]
    return {
        "available": True,
        "exported": exported,
        # Honest: the hierarchy exists as a diagnostic. It does not yet replace
        # the flat BH/BHY lens inside the promotion guard, and saying otherwise
        # would overstate what gates a candidate today.
        "wired_into_promotion_gate": False,
        "role": "DIAGNOSTIC_NOT_GATE",
    }


def _provenance_wiring() -> dict[str, Any]:
    """Which search entry points WRITE §7.1 TrialRecords (code-level, verified).

    The ledger numbers alone cannot show this: the production ledger only gains
    provenance rows when a driver next runs through a wired entry, and the
    historical trial population stays bare forever. So the wiring itself is
    verified at the code level (same pattern as _family_testing_available):
    the two canonical entries must accept the provenance kwargs and the
    registry facade must expose record_trial_provenance. Runtime behavior is
    pinned by tests/alpha_factory/test_trial_provenance_wiring.py.
    """
    import inspect

    try:
        from services.intelligence.registered_candidate_batch import (
            run_registered_candidate_batch,
        )
        from services.research.alpha_factory import run_alpha_factory
        from services.research.experiment_registry.registry import (
            ExperimentRegistry,
        )
    except Exception as exc:  # pragma: no cover - import failure is the report
        return {"available": False, "reason": f"IMPORT_FAILED:{exc}"}

    entries: dict[str, Any] = {}
    for name, fn in (
        ("run_alpha_factory", run_alpha_factory),
        ("run_registered_candidate_batch", run_registered_candidate_batch),
    ):
        params = inspect.signature(fn).parameters
        entries[name] = {
            "wired": "hypothesis_by_candidate" in params,
            "hypothesis_binding": "hypothesis_by_candidate" in params,
            "code_hash_pin": "code_hash" in params,
        }
    return {
        "available": True,
        "registry_record_api": hasattr(
            ExperimentRegistry, "record_trial_provenance"
        ),
        "entries": entries,
        "wired_entry_count": sum(1 for e in entries.values() if e["wired"]),
        # Honest: these are the TWO canonical entries. The 15+ other research
        # drivers that register bare config hashes remain unwired — see
        # open_gaps. This section is a code-level fact, not ledger coverage.
        "role": "CANONICAL_ENTRIES_WIRED_NOT_LEDGER_COVERAGE",
    }


def build_report(*, root: Path, ledger: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": ARTIFACT_MODE,
        "generated_at_ms": int(time.time_ns() // 1_000_000),
        "commit": _commit(root),
        "readiness": "L2_DRY_RUN_ONLY",
        "ledger": {
            "path": os.path.relpath(ledger, root).replace("\\", "/"),
            "exists": ledger.is_file(),
            "sha256": _sha256(ledger),
        },
    }

    if not ledger.is_file():
        report["verdict"] = "BLOCKED"
        report["block_reasons"] = ["LEDGER_ABSENT"]
        return report

    connection: sqlite3.Connection | None = None
    try:
        connection = _read_only_connection(ledger)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in sorted(_APPEND_ONLY_TABLES)
            if table in tables
        }
        report["ledger"].update(
            {
                "sqlite_integrity": integrity,
                "user_version": user_version,
                "row_counts": counts,
                "append_only_guards": _append_only_guards(connection, tables),
            }
        )
        report["provenance"] = _provenance_state(connection, tables)
    except sqlite3.DatabaseError as exc:
        report["verdict"] = "BLOCKED"
        report["block_reasons"] = [f"LEDGER_UNREADABLE:{exc}"]
        return report
    finally:
        if connection is not None:
            connection.close()

    report["family_multiple_testing"] = _family_testing_available()
    report["provenance_wiring"] = _provenance_wiring()
    report["trial_record_fields_required"] = list(_TRIAL_RECORD_FIELDS)

    guards = report["ledger"]["append_only_guards"]
    provenance = report["provenance"]
    block_reasons: list[str] = []
    if report["ledger"]["sqlite_integrity"] != "ok":
        block_reasons.append("SQLITE_INTEGRITY_FAILED")
    for table, state in sorted(guards.items()):
        if state == "UNGUARDED":
            block_reasons.append(f"APPEND_ONLY_GUARD_MISSING:{table}")
    if provenance.get("chain_valid") is False:
        block_reasons.append(
            f"PROVENANCE_CHAIN_INVALID:{provenance.get('reason') or 'UNKNOWN'}"
        )

    # PARTIAL, not PASS: the mechanism is in place and verifiable, but most of
    # the historical trial population predates it. Reporting PASS here would be
    # exactly the "未实现模块伪装为 PASS" the spec forbids (§22.13).
    if block_reasons:
        report["verdict"] = "BLOCKED"
    elif provenance.get("bare_trials", 0) > 0:
        report["verdict"] = "PARTIAL"
    else:
        report["verdict"] = "PASS"
    report["block_reasons"] = block_reasons

    report["open_gaps"] = [
        {
            "id": "TRIAL_PROVENANCE_NOT_YET_WRITTEN_BY_EVERY_ENTRY_POINT",
            "requirement": "727 §7.2 — all AI, manual and sweep attempts enter one ledger",
            "state": "CANONICAL_ENTRIES_WIRED_OTHER_DRIVERS_PENDING",
            "detail": (
                "P5-1: the two canonical search entries "
                "(services.research.alpha_factory.run_alpha_factory and "
                "services.intelligence.registered_candidate_batch."
                "run_registered_candidate_batch) now write a full TrialRecord "
                "per registered candidate at registration time, pinned by "
                "tests/alpha_factory/test_trial_provenance_wiring.py and "
                "visible in provenance_wiring. Production rows appear as "
                "drivers re-run through them. Still open: the 15+ other "
                "research drivers register bare config hashes, and the entire "
                "pre-P5-1 historical trial population is permanently bare — "
                "bare_trials stays non-zero and this artifact stays PARTIAL."
            ),
        },
        {
            "id": "FAMILY_FDR_NOT_A_GATE",
            "requirement": "727 §7.3 — family-level and within-family FDR",
            "state": "DIAGNOSTIC_ONLY",
            "detail": (
                "services/alpha/family_testing.py implements the two-stage "
                "hierarchy, family-adjusted trial count and marginal IR. It is "
                "not yet the lens used by the promotion guard, which still "
                "applies the flat BH/BHY lens."
            ),
        },
        {
            "id": "OOS_PEEK_ACCOUNTING_DEPENDS_ON_CALLERS",
            "requirement": "727 §7.2 / §0.4.7 — OOS view count must be recorded",
            "state": "FIELD_EXISTS_CALLERS_PENDING",
            "detail": (
                "viewed_oos_boundaries is persisted and surfaced via "
                "oos_peek_totals(), but a caller that omits it records zero "
                "peeks. The field cannot by itself prove a boundary was unseen."
            ),
        },
    ]
    return report


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _volatile(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that legitimately change every run, for --check comparison."""
    stripped = dict(payload)
    stripped.pop("generated_at_ms", None)
    stripped.pop("commit", None)
    return stripped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report trial-ledger integrity and provenance coverage (727 §7)"
    )
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the tracked artifact matches the current ledger; write nothing",
    )
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    ledger = Path(args.ledger).resolve() if args.ledger else (root / "data" / "cache" / "experiment_registry.db")
    output = Path(args.output).resolve() if args.output else (root / "evidence" / "trial_ledger_integrity.json")

    report = build_report(root=root, ledger=ledger)

    if args.check:
        if not output.is_file():
            print(f"trial ledger integrity: artifact absent: {output}", file=sys.stderr)
            return 1
        try:
            with output.open(encoding="utf-8") as handle:
                tracked = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"trial ledger integrity: artifact unreadable: {exc}", file=sys.stderr)
            return 1
        if _volatile(tracked) != _volatile(report):
            print(
                f"trial ledger integrity: artifact drift: regenerate {output}",
                file=sys.stderr,
            )
            return 1
        print(
            "trial ledger integrity: current; "
            f"verdict={report['verdict']} "
            f"coverage={report.get('provenance', {}).get('coverage_ratio')}"
        )
        return 0

    _write(output, report)
    provenance = report.get("provenance", {})
    print(
        f"trial ledger integrity: verdict={report['verdict']} "
        f"attempts={provenance.get('attempts_recorded')} "
        f"bare_trials={provenance.get('bare_trials')} "
        f"chain_valid={provenance.get('chain_valid')}"
    )
    for reason in report.get("block_reasons", ()):
        print(f"  BLOCK: {reason}")
    return 0 if report["verdict"] != "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
