from services.research.paper_trading.attested_store import AttestedForwardLedger
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.paper_trading.store import PaperTrackStore


def _admission() -> ForwardAdmissionRef:
    return ForwardAdmissionRef(
        admission_id="data-admission-test",
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        recorded_at_ms=1_700_000_000_000,
        price_basis="PERP_MARK_PRICE",
        instrument_scope=("BTC", "ETH"),
    )


def test_attested_decision_and_realization_are_hash_bound_and_atomic(tmp_path):
    path = str(tmp_path / "paper_track.db")
    PaperTrackStore(path, create=True)
    ledger = AttestedForwardLedger(path, create=True)
    ref = _admission()

    assert ledger.record_decision(
        "crowd_contra",
        1_700_000_000_000,
        recorded_at=1_700_000_000_100,
        weights={"BTC": 0.5, "ETH": -0.5},
        signals={"BTC": 1.0, "ETH": -1.0},
        admission_ref=ref,
    ) is True
    assert len(ledger.attested_pending("crowd_contra")) == 1
    assert len(ledger.attested_decisions("crowd_contra")) == 1
    assert ledger.legacy_decisions() == []
    assert ledger.decision_chain_valid() is True

    assert ledger.commit_realization(
        "crowd_contra",
        1_700_000_000_000,
        as_of_ts=1_700_604_800_000,
        realized_return=0.02,
        realized_ic=0.4,
        inputs={"BTC": {"weight": 0.5}, "ETH": {"weight": -0.5}},
        admission_ref=ref,
        committed_at_ms=1_700_604_800_100,
    ) is True
    assert ledger.attested_pending("crowd_contra") == []
    assert len(ledger.attested_realizations("crowd_contra")) == 1
    assert ledger.realization_chain_valid() is True


def test_legacy_rows_remain_legacy_and_cannot_count_as_attested(tmp_path):
    path = str(tmp_path / "paper_track.db")
    PaperTrackStore(path, create=True)
    legacy = ForwardLedger(path, create=True)
    legacy.record_decision(
        "crowd_contra", 1_700_000_000_000, recorded_at=1_700_000_000_100,
        weights={"BTC": 1.0},
    )
    attested = AttestedForwardLedger(path, create=True)
    assert len(attested.legacy_decisions()) == 1
    assert attested.attested_decisions("crowd_contra") == []
    assert attested.attested_pending("crowd_contra") == []
    assert attested.attested_realizations("crowd_contra") == []
    assert attested.decision_chain_valid() is True
