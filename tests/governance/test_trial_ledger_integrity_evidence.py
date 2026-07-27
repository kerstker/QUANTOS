"""Governance guards for the trial-ledger integrity artifact (727 §7).

Deliberately NOT a `--check` byte-equality test. The artifact embeds the live
ledger's sha256 and row counts, which legitimately change on every research run,
so pinning bytes would make ordinary research activity turn the gate red — the
gate would then be measuring "did anyone do research today", not integrity.

What is pinned instead are the invariants that make the artifact trustworthy:
  * the generator is READ-ONLY against the ledger;
  * it can never report PASS while part of the trial population is bare;
  * a broken hash chain, a missing append-only guard, or a config that escaped
    the trial population is reported as BLOCKED, not smoothed over;
  * a missing/unreadable ledger is BLOCKED, never an empty PASS.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.generate_trial_ledger_integrity import build_report, main
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.trial_provenance import (
    ActorType,
    TrialProvenance,
    TrialStatus,
)

ROOT = Path(__file__).resolve().parents[2]


def _record(**overrides) -> TrialProvenance:
    base = dict(
        trial_id="gov-trial-1",
        research_session_id="gov-session",
        config_hash="gov-cfg",
        actor_id="tests.governance",
        actor_type=ActorType.AUTOMATED_SEARCH,
        status=TrialStatus.RECORDED,
        started_at=100,
        completed_at=200,
    )
    base.update(overrides)
    return TrialProvenance(**base)


def _ledger(tmp_path: Path, *, with_provenance: bool = True) -> Path:
    path = tmp_path / "experiment_registry.db"
    store = SqliteExperimentStore(str(path), create=True)
    store.add_trials(["legacy-cfg"], first_seen_ts=1)
    if with_provenance:
        store.record_trial_provenance(_record())
    store._conn.close()
    return path


def test_repository_artifact_exists_and_declares_its_honest_verdict() -> None:
    """The tracked artifact must exist and must not claim more than it proved."""
    artifact = ROOT / "evidence" / "trial_ledger_integrity.json"
    assert artifact.is_file(), "run scripts/generate_trial_ledger_integrity.py"
    payload = json.loads(artifact.read_text(encoding="utf-8"))

    assert payload["mode"] == "TRIAL_LEDGER_INTEGRITY_READ_ONLY"
    assert payload["readiness"] == "L2_DRY_RUN_ONLY"
    assert payload["verdict"] in {"PASS", "PARTIAL", "BLOCKED"}
    # §22.13 — an unfinished subsystem must never be dressed up as PASS.
    if payload["provenance"].get("bare_trials", 0) > 0:
        assert payload["verdict"] != "PASS"
        assert payload["open_gaps"], "PARTIAL must enumerate what is still open"
    for gap in payload["open_gaps"]:
        assert gap["id"] and gap["requirement"] and gap["state"] and gap["detail"]
    # P5-1 honest status: the two canonical entries ARE wired (code-level),
    # while ledger coverage stays PARTIAL — both halves must be declared.
    wiring = payload["provenance_wiring"]
    assert wiring["available"] is True
    assert wiring["registry_record_api"] is True
    assert wiring["wired_entry_count"] == 2
    assert wiring["entries"]["run_alpha_factory"]["wired"] is True
    assert wiring["entries"]["run_registered_candidate_batch"]["wired"] is True
    assert wiring["role"] == "CANONICAL_ENTRIES_WIRED_NOT_LEDGER_COVERAGE"


def test_provenance_wiring_reports_both_canonical_entries() -> None:
    """The wiring detector must keep seeing both canonical entries — if the
    hypothesis binding is ever removed from either signature, the artifact
    must stop claiming the wiring exists."""
    from scripts.generate_trial_ledger_integrity import _provenance_wiring

    wiring = _provenance_wiring()
    assert wiring["available"] is True
    assert wiring["wired_entry_count"] == 2
    assert all(entry["wired"] for entry in wiring["entries"].values())


def test_generator_does_not_mutate_the_ledger(tmp_path) -> None:
    """§7 evidence is read-only: reporting must never repair or migrate."""
    ledger = _ledger(tmp_path)
    before = ledger.read_bytes()
    report = build_report(root=tmp_path, ledger=ledger)
    assert report["verdict"] in {"PASS", "PARTIAL"}
    assert ledger.read_bytes() == before


def test_absent_ledger_is_blocked_not_an_empty_pass(tmp_path) -> None:
    report = build_report(root=tmp_path, ledger=tmp_path / "nope.db")
    assert report["verdict"] == "BLOCKED"
    assert report["block_reasons"] == ["LEDGER_ABSENT"]


def test_unreadable_ledger_is_blocked(tmp_path) -> None:
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database")
    report = build_report(root=tmp_path, ledger=corrupt)
    assert report["verdict"] == "BLOCKED"
    assert any(r.startswith("LEDGER_UNREADABLE") for r in report["block_reasons"])


def test_bare_population_forces_partial(tmp_path) -> None:
    """A ledger whose trials have no TrialRecord is PARTIAL, never PASS."""
    ledger = _ledger(tmp_path, with_provenance=False)
    report = build_report(root=tmp_path, ledger=ledger)
    assert report["verdict"] == "PARTIAL"
    assert report["provenance"]["bare_trials"] >= 1
    assert report["provenance"]["coverage_ratio"] < 1.0


def test_fully_covered_ledger_can_reach_pass(tmp_path) -> None:
    """The PASS path must be reachable, otherwise PARTIAL means nothing."""
    path = tmp_path / "covered.db"
    store = SqliteExperimentStore(str(path), create=True)
    store.record_trial_provenance(_record())
    store._conn.close()
    report = build_report(root=tmp_path, ledger=path)
    assert report["verdict"] == "PASS"
    assert report["provenance"]["bare_trials"] == 0
    assert report["provenance"]["coverage_ratio"] == 1.0
    assert report["provenance"]["chain_valid"] is True


def test_tampered_chain_is_reported_as_blocked(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    raw = sqlite3.connect(ledger)
    raw.execute("DROP TRIGGER trial_provenance_no_update")
    forged = json.loads(
        raw.execute(
            "SELECT record_json FROM trial_provenance WHERE trial_id='gov-trial-1'"
        ).fetchone()[0]
    )
    forged["status"] = "PASSED_TO_CATALOG"
    raw.execute(
        "UPDATE trial_provenance SET record_json=? WHERE trial_id='gov-trial-1'",
        (json.dumps(forged, ensure_ascii=True, sort_keys=True, separators=(",", ":")),),
    )
    raw.commit()
    raw.close()

    report = build_report(root=tmp_path, ledger=ledger)
    assert report["verdict"] == "BLOCKED"
    assert any(
        r.startswith("PROVENANCE_CHAIN_INVALID") for r in report["block_reasons"]
    )


def test_missing_append_only_guard_is_reported_as_blocked(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    raw = sqlite3.connect(ledger)
    raw.execute("DROP TRIGGER trials_no_delete")
    raw.commit()
    raw.close()

    report = build_report(root=tmp_path, ledger=ledger)
    assert report["verdict"] == "BLOCKED"
    assert "APPEND_ONLY_GUARD_MISSING:trials" in report["block_reasons"]


def test_config_escaping_the_trial_population_is_blocked(tmp_path) -> None:
    """§7.2 — an attempt that is not in the DSR/PBO/FDR denominator is a defect,
    not a rounding detail."""
    ledger = _ledger(tmp_path)
    raw = sqlite3.connect(ledger)
    raw.execute("DROP TRIGGER trials_no_delete")
    raw.execute("DELETE FROM trials WHERE config_hash='gov-cfg'")
    raw.commit()
    raw.close()

    report = build_report(root=tmp_path, ledger=ledger)
    assert report["verdict"] == "BLOCKED"
    assert any(
        "CONFIG_ABSENT_FROM_TRIALS" in reason for reason in report["block_reasons"]
    )


def test_family_multiple_testing_is_declared_a_diagnostic_not_a_gate(tmp_path) -> None:
    """§7.3 exists as analysis. Claiming it gates promotion would overstate what
    actually stops a candidate today."""
    report = build_report(root=tmp_path, ledger=_ledger(tmp_path))
    family = report["family_multiple_testing"]
    assert family["available"] is True
    assert family["wired_into_promotion_gate"] is False
    assert family["role"] == "DIAGNOSTIC_NOT_GATE"
    assert "hierarchical_fdr" in family["exported"]


def test_cli_check_detects_drift(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    output = tmp_path / "artifact.json"
    assert main(
        ["--repo-root", str(tmp_path), "--ledger", str(ledger), "--output", str(output)]
    ) == 0
    assert main(
        [
            "--repo-root",
            str(tmp_path),
            "--ledger",
            str(ledger),
            "--output",
            str(output),
            "--check",
        ]
    ) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["provenance"]["bare_trials"] = 999
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    assert main(
        [
            "--repo-root",
            str(tmp_path),
            "--ledger",
            str(ledger),
            "--output",
            str(output),
            "--check",
        ]
    ) == 1


def test_cli_returns_nonzero_on_blocked(tmp_path) -> None:
    assert main(
        [
            "--repo-root",
            str(tmp_path),
            "--ledger",
            str(tmp_path / "absent.db"),
            "--output",
            str(tmp_path / "out.json"),
        ]
    ) == 1
