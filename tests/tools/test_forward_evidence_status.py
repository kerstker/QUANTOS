import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

import tools.forward_evidence_status as status_tool
from services.data.admission_store import DataQualityAdmissionStore
from services.research.paper_trading.forward_evidence import load_forward_admission_ref
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.paper_trading.store import PaperTrackStore
from tools.forward_evidence_status import (
    ForwardEvidenceError,
    build_forward_evidence,
    main,
    verify_forward_evidence,
)


_DAY_MS = 86_400_000


def _pass_evidence(*, generated_at_ms=100):
    return {
        "schema_version": 2,
        "generated_at_ms": generated_at_ms,
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "FORWARD_EVIDENCE_DATA_ADMISSION",
        "instrument_scope": ["BTC"],
        "price_basis": "PERP_MARK_PRICE",
        "quality_verdict": "PASS",
        "lineage_verdict": "PASS",
        "verdict": "PASS",
        "block_reasons": [],
        "sources": {},
        "refresh_observation": {"readable": True, "run_id": "test-refresh"},
    }


def _make_authority(root: Path, *, verdict="PASS"):
    path = root / "data" / "cache" / "data_quality_admission.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    authority = DataQualityAdmissionStore(str(path), create=True)
    evidence = _pass_evidence()
    if verdict == "BLOCK":
        evidence.update(
            quality_verdict="BLOCK",
            lineage_verdict="BLOCK",
            verdict="BLOCK",
            block_reasons=["QUALITY:test"],
        )
    decision, _ = authority.record_evidence(evidence, recorded_at_ms=110)
    ref = load_forward_admission_ref(
        authority,
        admission_id=decision.admission_id,
        evidence_sha256=decision.evidence_sha256,
        decision_hash=decision.decision_hash,
        instruments={"BTC"},
    ) if verdict == "PASS" else None
    authority.close()
    return path, ref


def _make_forward_store(root: Path, ref):
    path = root / "data" / "cache" / "paper_track.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    store = PaperTrackStore(str(path), create=True)
    ledger = ForwardLedger(str(path), create=True)
    ledger._conn.execute(
        "INSERT INTO forward_decisions"
        "(alpha_id,decided_ts,recorded_at,weights_json,signals_json,source,realized) "
        "VALUES (?,?,?,?,?,?,0)",
        ("legacy", 1, 1, '{"BTC":1.0}', None, "FORWARD_LIVE"),
    )
    ledger._conn.commit()
    ledger.record_decision("alpha", 100, recorded_at=100, weights={"BTC": 1.0}, admission_ref=ref)
    assert ledger.commit_realization(
        "alpha",
        100,
        as_of_ts=100 + 7 * _DAY_MS,
        realized_return=0.01,
        realized_ic=0.2,
        source="FORWARD_L2_ATTESTED_PERP_MARK_GROSS_PLUS_FUNDING",
        inputs={"BTC": {"weight": 1.0, "price_return": 0.01, "funding": 0.0}},
        admission_ref=ref,
        committed_at_ms=200,
    )
    ledger.record_decision("alpha", 200, recorded_at=200, weights={"BTC": 1.0}, admission_ref=ref)
    ledger._conn.close()
    store._conn.close()
    return path


def _copy_implementation(root: Path):
    source_root = Path(__file__).resolve().parents[2]
    for relative in (
        "services/research/paper_trading/forward.py",
        "services/research/paper_trading/forward_evidence.py",
        "services/research/paper_trading/forward_store.py",
        "tools/forward_track_advance.py",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, target)


def _add_attested_weeks(root: Path, ref, *, count: int = 7):
    path = root / "data" / "cache" / "paper_track.db"
    store = PaperTrackStore(str(path), create=False)
    ledger = ForwardLedger(str(path), create=False)
    for index in range(count):
        decided_ts = (index + 2) * 10 * _DAY_MS
        assert ledger.record_decision(
            "alpha",
            decided_ts,
            recorded_at=decided_ts,
            weights={"BTC": 1.0},
            admission_ref=ref,
        )
        assert ledger.commit_realization(
            "alpha",
            decided_ts,
            as_of_ts=decided_ts + 7 * _DAY_MS,
            realized_return=0.01,
            realized_ic=0.2,
            source="FORWARD_L2_ATTESTED_PERP_MARK_GROSS_PLUS_FUNDING",
            inputs={"BTC": {"weight": 1.0, "price_return": 0.01, "funding": 0.0}},
            admission_ref=ref,
            committed_at_ms=decided_ts + 1,
        )
    ledger._conn.close()
    store._conn.close()


def _attested_gate_root(root: Path):
    _copy_implementation(root)
    authority_path, ref = _make_authority(root)
    paper_path = root / "data" / "cache" / "paper_track.db"
    paper_path.parent.mkdir(parents=True, exist_ok=True)
    store = PaperTrackStore(str(paper_path), create=True)
    ledger = ForwardLedger(str(paper_path), create=True)
    ledger._conn.close()
    store._conn.close()
    _add_attested_weeks(root, ref, count=8)
    return authority_path


@pytest.fixture()
def evidence_root(tmp_path):
    _copy_implementation(tmp_path)
    _, ref = _make_authority(tmp_path)
    _make_forward_store(tmp_path, ref)
    return tmp_path


def test_build_is_bit_identical_and_counts_only_attested_authority(evidence_root):
    first = build_forward_evidence(evidence_root, observed_at_ms=500)
    second = build_forward_evidence(evidence_root, observed_at_ms=500)

    assert first == second
    assert first["readiness"] == "L2_DRY_RUN_ONLY"
    assert first["counts"] == {
        "attested_decisions": 2,
        "attested_pending": 1,
        "attested_realizations": 1,
        "legacy_unattested_decisions": 1,
    }
    assert first["evidence_gate"] == {"minimum_weeks": 8, "eligible": False}
    assert first["max_age_ms"] == 172_800_000
    assert set(first["implementation"]["files"]) == {
        "services/research/paper_trading/forward.py",
        "services/research/paper_trading/forward_evidence.py",
        "services/research/paper_trading/forward_store.py",
        "tools/forward_track_advance.py",
    }
    alpha = first["alphas"][0]
    assert alpha["attested_weeks"] == 1
    assert alpha["attested_as_of_weeks"] == [100 + 7 * _DAY_MS]
    assert alpha["attested_weeks"] == len(set(alpha["attested_as_of_weeks"]))
    assert alpha["status"] == "ATTESTED"
    assert alpha["legacy_unattested_decisions"] == 0
    assert first["data_admission"]["status"] == "CURRENT"


def test_zero_weeks_and_legacy_only_never_satisfy_the_evidence_gate(tmp_path):
    _copy_implementation(tmp_path)
    _, ref = _make_authority(tmp_path)
    path = tmp_path / "data" / "cache" / "paper_track.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    store = PaperTrackStore(str(path), create=True)
    ledger = ForwardLedger(str(path), create=True)
    ledger._conn.execute(
        "INSERT INTO forward_decisions"
        "(alpha_id,decided_ts,recorded_at,weights_json,signals_json,source,realized) "
        "VALUES (?,?,?,?,?,?,0)",
        ("legacy", 1, 1, '{\"BTC\":1.0}', None, "FORWARD_LIVE"),
    )
    ledger._conn.commit()
    ledger._conn.close()
    store._conn.close()

    document = build_forward_evidence(tmp_path, observed_at_ms=500)

    assert document["counts"] == {
        "attested_decisions": 0,
        "attested_pending": 0,
        "attested_realizations": 0,
        "legacy_unattested_decisions": 1,
    }
    assert document["alphas"][0]["attested_weeks"] == 0
    assert document["evidence_gate"]["eligible"] is False


def test_stale_admission_is_explicitly_stale(evidence_root):
    document = build_forward_evidence(
        evidence_root,
        observed_at_ms=110 + 172_800_001,
    )

    assert document["data_admission"]["status"] == "STALE"
    assert document["alphas"][0]["status"] == "STALE"


@pytest.mark.parametrize("admission_state", ["UNAVAILABLE", "BLOCKED", "STALE"])
def test_evidence_gate_requires_current_admission_after_eight_attested_weeks(
    evidence_root,
    admission_state,
):
    root = evidence_root / admission_state.lower()
    authority_path = _attested_gate_root(root)
    kwargs = {"observed_at_ms": 500}
    if admission_state == "UNAVAILABLE":
        kwargs["admission_database"] = root / "missing-admission.db"
    elif admission_state == "BLOCKED":
        _make_authority(root, verdict="BLOCK")
    else:
        kwargs["observed_at_ms"] = 110 + 172_800_001

    document = build_forward_evidence(root, **kwargs)

    assert authority_path.is_file()
    assert document["alphas"][0]["attested_weeks"] == 8
    assert document["data_admission"]["status"] == admission_state
    assert document["evidence_gate"]["eligible"] is False


def test_invalid_attested_as_of_week_cannot_be_counted(evidence_root):
    db = evidence_root / "data" / "cache" / "paper_track.db"
    copy = evidence_root / "copy.db"
    shutil.copyfile(db, copy)
    connection = sqlite3.connect(copy)
    connection.execute("DROP TRIGGER forward_realizations_no_update")
    connection.execute("UPDATE forward_realizations SET as_of_ts=604800100 WHERE realization_seq=1")
    connection.commit()
    connection.close()

    document = build_forward_evidence(evidence_root, observed_at_ms=500, database=copy)
    assert document["counts"]["attested_realizations"] == 0
    assert document["decision_chain_valid"] is False
    assert document["realization_chain_valid"] is False


def test_missing_ledger_is_unavailable_without_synthetic_weeks(evidence_root):
    document = build_forward_evidence(
        evidence_root,
        observed_at_ms=500,
        database=evidence_root / "missing-paper-track.db",
    )

    assert document["counts"] == {
        "attested_decisions": 0,
        "attested_pending": 0,
        "attested_realizations": 0,
        "legacy_unattested_decisions": 0,
    }
    assert document["decision_chain_valid"] is False
    assert document["realization_chain_valid"] is False
    with pytest.raises(ForwardEvidenceError):
        verify_forward_evidence(document, evidence_root, database=evidence_root / "missing-paper-track.db")


def test_tampered_chain_or_required_trigger_fails_closed(evidence_root):
    db = evidence_root / "data" / "cache" / "paper_track.db"
    copy = evidence_root / "tampered.db"
    shutil.copyfile(db, copy)
    connection = sqlite3.connect(copy)
    connection.execute("DROP TRIGGER forward_decision_bindings_no_update")
    connection.execute("UPDATE forward_decision_bindings SET price_basis='SPOT' WHERE binding_seq=1")
    connection.commit()
    connection.close()

    document = build_forward_evidence(evidence_root, observed_at_ms=500)
    with pytest.raises(ForwardEvidenceError):
        verify_forward_evidence(document, evidence_root, database=copy)


def test_missing_or_blocked_admission_is_explicitly_non_operable(evidence_root):
    unavailable = build_forward_evidence(
        evidence_root,
        observed_at_ms=500,
        admission_database=evidence_root / "missing-admission.db",
    )
    assert unavailable["data_admission"]["status"] == "UNAVAILABLE"
    assert unavailable["alphas"][0]["status"] == "UNAVAILABLE"

    blocked_root = evidence_root / "blocked"
    _copy_implementation(blocked_root)
    _, ref = _make_authority(blocked_root, verdict="PASS")
    _make_forward_store(blocked_root, ref)
    _make_authority(blocked_root, verdict="BLOCK")
    blocked = build_forward_evidence(blocked_root, observed_at_ms=500)
    assert blocked["data_admission"]["status"] == "BLOCKED"
    assert blocked["alphas"][0]["status"] == "BLOCKED"


def test_check_is_read_only(evidence_root):
    output = evidence_root / "evidence" / "forward_evidence_baseline.json"
    assert main([
        "--repo-root", str(evidence_root), "--observed-at-ms", "500", "--output", str(output),
    ]) == 0
    database = evidence_root / "data" / "cache" / "paper_track.db"
    admission = evidence_root / "data" / "cache" / "data_quality_admission.db"
    before = {path: (path.read_bytes(), os.stat(path).st_mtime_ns) for path in (database, admission)}

    assert main([
        "--repo-root", str(evidence_root), "--observed-at-ms", "500", "--output", str(output), "--check",
    ]) == 0

    assert {path: (path.read_bytes(), os.stat(path).st_mtime_ns) for path in before} == before


def test_check_never_reads_wall_clock(evidence_root, monkeypatch):
    output = evidence_root / "evidence" / "forward_evidence_baseline.json"
    assert main([
        "--repo-root", str(evidence_root), "--observed-at-ms", "500", "--output", str(output),
    ]) == 0
    monkeypatch.setattr(status_tool.time, "time", lambda: pytest.fail("--check read wall clock"))

    assert status_tool.main([
        "--repo-root", str(evidence_root), "--output", str(output), "--check",
    ]) == 0
