import json
from decimal import Decimal

import pytest

from services.execution.paper_execution_store import PaperExecutionStore
from services.execution.simulated_sor import BookLevel, VenueBook
from tools import paper_shadow_auto
from tools.paper_shadow_auto import (
    SHADOW_CLASS,
    _latest_attested_shadow_decision,
    admit_shadow_preview,
)


def _preview():
    return {
        "alpha_id": "crowd_contra",
        "decision_ts": 100,
        "run_id": "run-1",
        "n_intents": 2,
        "n_approved": 2,
        "n_rejected": 0,
        "n_paper_fills": 2,
        "reconciliation_residual": 0.0,
        "paper_execution": "PAPER_BLOCKED",
        "live_allowed": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
        "broker_order_submission": False,
        "credentials_accessed": False,
        "execution_tca": {
            "route_tca_available": True,
            "fill_model": "REAL_PUBLIC_BOOK_SIMULATED_SOR",
            "routing_snapshot_id": "snapshot-1",
        },
    }


def test_shadow_admission_is_filled_but_nonpromotable():
    row = admit_shadow_preview(_preview())

    assert row["paper_execution"] == "PAPER_FILLED"
    assert row["execution_class"] == SHADOW_CLASS
    assert row["official_paper_execution"] == "PAPER_BLOCKED"
    assert row["promotion_eligible"] is False
    assert row["forward_evidence_eligible"] is False
    assert row["deployment_eligible"] is False
    assert row["live_allowed"] is False
    assert row["order_submission_allowed"] is False


@pytest.mark.parametrize(
    "field",
    [
        "live_allowed",
        "order_submission_allowed",
        "broker_connectivity",
        "broker_order_submission",
        "credentials_accessed",
    ],
)
def test_shadow_admission_rejects_any_authority_surface(field):
    preview = _preview()
    preview[field] = True

    with pytest.raises(RuntimeError, match="safety boundary"):
        admit_shadow_preview(preview)


def test_shadow_admission_requires_exact_reconciliation_and_fills():
    preview = _preview()
    preview["n_paper_fills"] = 1
    with pytest.raises(RuntimeError, match="pre-trade and reconciliation"):
        admit_shadow_preview(preview)


def test_shadow_decision_prefers_pending_without_mutating_forward_evidence(monkeypatch):
    class FakeLedger:
        def __init__(self, path, create):
            assert path == "forward.db"
            assert create is False

        def attested_pending(self, alpha_id):
            return [{"decided_ts": 200, "realized": 0}]

        def attested_decisions(self, alpha_id):
            raise AssertionError("fallback must not be queried")

        def close(self):
            pass

    monkeypatch.setattr(paper_shadow_auto, "AttestedForwardLedger", FakeLedger)
    row, selection = _latest_attested_shadow_decision("forward.db", "challenger")
    assert row["alpha_id"] == "challenger"
    assert row["realized"] == 0
    assert selection == "ATTESTED_PENDING"


def test_shadow_decision_reuses_latest_attested_realized_target(monkeypatch):
    class FakeLedger:
        def __init__(self, path, create):
            pass

        def attested_pending(self, alpha_id):
            return []

        def attested_decisions(self, alpha_id):
            return [
                {"decided_ts": 100, "realized": 1},
                {"decided_ts": 200, "realized": 1},
            ]

        def close(self):
            pass

    monkeypatch.setattr(paper_shadow_auto, "AttestedForwardLedger", FakeLedger)
    row, selection = _latest_attested_shadow_decision("forward.db", "challenger")
    assert row["decided_ts"] == 200
    assert row["realized"] == 1
    assert selection == "LATEST_ATTESTED_REALIZED"


def test_shadow_worker_recovers_all_books_and_replays_idempotently_after_restart(
    tmp_path, monkeypatch
):
    alpha_ids = [f"candidate_{index:02d}" for index in range(10)]
    store_path = tmp_path / "shadow.db"
    output_path = tmp_path / "shadow.json"

    monkeypatch.setattr(paper_shadow_auto, "paper_candidate_ids", lambda: alpha_ids)
    monkeypatch.setattr(paper_shadow_auto, "_load_json", lambda _path: {})
    monkeypatch.setattr(
        paper_shadow_auto, "load_mark_prices", lambda _path: {"BTC": 100.0}
    )
    monkeypatch.setattr(
        paper_shadow_auto,
        "_latest_attested_shadow_decision",
        lambda _path, alpha_id: (
            {"alpha_id": alpha_id, "decided_ts": 1_000, "run_id": f"run-{alpha_id}"},
            "ATTESTED_PENDING",
        ),
    )

    def preview(alpha_id, pending, **_kwargs):
        row = _preview()
        row.update(
            {
                "alpha_id": alpha_id,
                "decision_ts": pending["decided_ts"],
                "run_id": pending["run_id"],
                "paper_positions": {"BTC": 1},
            }
        )
        return row

    monkeypatch.setattr(paper_shadow_auto, "build_alpha_preview", preview)
    monkeypatch.setattr(
        paper_shadow_auto,
        "load_persisted_routing_snapshot",
        lambda **_kwargs: ({}, {"BTC": 100.0}, "snapshot-1", 2_000),
    )

    first = paper_shadow_auto.run_shadow(
        store_path=str(store_path),
        output_path=str(output_path),
        generated_at_ms=2_000,
    )
    restarted = paper_shadow_auto.run_shadow(
        store_path=str(store_path),
        output_path=str(output_path),
        generated_at_ms=3_000,
    )

    assert first["status"] == "SAFE_BLOCKED"
    assert first["verdict"]["shadow_paper_running"] is False
    assert first["ledger"]["write_attempted"] is False
    assert restarted["status"] == "SAFE_BLOCKED"
    assert restarted["alphas"] == {}
    assert not store_path.exists()


def test_shadow_worker_opens_new_account_through_persisted_multivenue_sor(
    tmp_path, monkeypatch
):
    decision_ts = 1_700_000_000_000
    execution_ts = decision_ts + 2_000
    pending = {
        "decided_ts": decision_ts,
        "recorded_at": decision_ts,
        "weights_json": json.dumps({"BTC": 0.125}),
        "source": "FORWARD_L2_ATTESTED_DECISION",
    }
    status = {
        "quality_evidence_current": True,
        "data_quality_admission": {"verdict": "PASS"},
        "alerts": [],
        "ran_at": decision_ts + 1_000,
        "staleness_days": {"prices": {"worst_days": 1}},
    }
    books = (
        VenueBook(
            venue_id="binance_usdm",
            observed_at_ms=execution_ts,
            taker_fee_bps=Decimal("5"),
            latency_ms=20,
            bids=(BookLevel(Decimal("49999"), 10),),
            asks=(BookLevel(Decimal("50001"), 10),),
        ),
        VenueBook(
            venue_id="bybit_linear",
            observed_at_ms=execution_ts,
            taker_fee_bps=Decimal("5.5"),
            latency_ms=30,
            bids=(BookLevel(Decimal("49998"), 10),),
            asks=(BookLevel(Decimal("50002"), 10),),
        ),
    )
    monkeypatch.setattr(
        paper_shadow_auto, "paper_candidate_ids", lambda: ("crowd_contra",)
    )
    monkeypatch.setattr(paper_shadow_auto, "_load_json", lambda _path: status)
    monkeypatch.setattr(
        paper_shadow_auto,
        "load_mark_prices",
        lambda _path: {"BTC": {decision_ts: 50_000.0}},
    )
    monkeypatch.setattr(
        paper_shadow_auto,
        "_latest_attested_shadow_decision",
        lambda _path, _alpha_id: (pending, "ATTESTED_PENDING"),
    )
    monkeypatch.setattr(
        paper_shadow_auto,
        "load_persisted_routing_snapshot",
        lambda **_kwargs: (
            {"BTC": books},
            {"BTC": 50_000.0},
            "a" * 64,
            execution_ts,
        ),
    )

    result = paper_shadow_auto.run_shadow(
        store_path=str(tmp_path / "routed.db"),
        output_path=str(tmp_path / "status.json"),
        generated_at_ms=execution_ts,
    )

    assert result["status"] == "SAFE_BLOCKED"
    assert result["alphas"] == {}
    assert result["routing"]["mode"] == "NOT_RUN"
    assert result["ledger"]["write_attempted"] is False
    assert not (tmp_path / "routed.db").exists()
