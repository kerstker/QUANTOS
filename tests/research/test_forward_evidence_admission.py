import pytest

from services.data.admission_store import DataQualityAdmissionStore
from services.research.paper_trading.forward_evidence import (
    ForwardEvidenceError,
    load_forward_admission_ref,
)


def _evidence(*, verdict="PASS"):
    blocked = verdict == "BLOCK"
    return {
        "schema_version": 2,
        "generated_at_ms": 100,
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "FORWARD_EVIDENCE_DATA_ADMISSION",
        "instrument_scope": ["BTC", "ETH"],
        "price_basis": "PERP_MARK_PRICE",
        "quality_verdict": "BLOCK" if blocked else "PASS",
        "lineage_verdict": "BLOCK" if blocked else "PASS",
        "verdict": verdict,
        "block_reasons": ["QUALITY:funding:['BTC']"] if blocked else [],
        "sources": {},
        "refresh_observation": {"readable": True, "run_id": "refresh-1"},
    }


def _authority(tmp_path, *, verdict="PASS"):
    store = DataQualityAdmissionStore(str(tmp_path / "admission.db"), create=True)
    decision, _ = store.record_evidence(_evidence(verdict=verdict), recorded_at_ms=110)
    return store, decision


def test_loads_only_persisted_pass_admission_covering_instruments(tmp_path):
    store, decision = _authority(tmp_path)
    ref = load_forward_admission_ref(
        store,
        admission_id=decision.admission_id,
        evidence_sha256=decision.evidence_sha256,
        decision_hash=decision.decision_hash,
        instruments={"BTC"},
    )
    assert ref.admission_id == decision.admission_id
    assert ref.instrument_scope == ("BTC", "ETH")
    assert ref.price_basis == "PERP_MARK_PRICE"


@pytest.mark.parametrize("change", ["id", "evidence", "decision", "scope"])
def test_rejects_missing_mismatched_or_out_of_scope_authority(tmp_path, change):
    store, decision = _authority(tmp_path)
    values = {
        "admission_id": decision.admission_id,
        "evidence_sha256": decision.evidence_sha256,
        "decision_hash": decision.decision_hash,
        "instruments": {"BTC"},
    }
    if change == "id":
        values["admission_id"] = "missing"
    elif change == "evidence":
        values["evidence_sha256"] = "0" * 64
    elif change == "decision":
        values["decision_hash"] = "0" * 64
    else:
        values["instruments"] = {"SOL"}
    with pytest.raises(ForwardEvidenceError):
        load_forward_admission_ref(store, **values)


def test_rejects_block_admission(tmp_path):
    store, decision = _authority(tmp_path, verdict="BLOCK")
    with pytest.raises(ForwardEvidenceError, match="PASS"):
        load_forward_admission_ref(
            store,
            admission_id=decision.admission_id,
            evidence_sha256=decision.evidence_sha256,
            decision_hash=decision.decision_hash,
            instruments={"BTC"},
        )
