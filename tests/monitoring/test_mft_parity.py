from __future__ import annotations

import sqlite3

import pytest

from quantos.contracts.trading import OrderSide
from services.monitoring.mft_parity import (
    MftParityPolicy,
    PaperDecisionLeg,
    TestnetFillObservation as FillObservation,
    TestnetOrderObservation as OrderObservation,
    build_mft_parity_report,
)
from services.monitoring.mft_parity_store import (
    MftParityStore,
    MftParityStoreError,
)


NOW = 2_000
RUN = "canonical-run-1"


def _policy(**overrides) -> MftParityPolicy:
    values = {
        "policy_id": "mft-parity-engineering",
        "policy_version": "1",
        "max_target_quantity_diff": 0.01,
        "max_request_quantity_diff": 0.01,
        "min_fill_ratio": 0.80,
        "max_abs_slippage_bps": 200.0,
        "max_latency_ms": 500,
        "max_position_drift": 0.01,
        "max_abs_equity_drift": 10.0,
    }
    values.update(overrides)
    return MftParityPolicy(**values)


def _paper(**overrides) -> PaperDecisionLeg:
    values = {
        "intent_id": "intent-1",
        "decision_id": "decision-1",
        "client_order_id": "client-1",
        "decided_at_ms": 1_000,
        "instrument_id": "BTC",
        "side": OrderSide.BUY,
        "target_signed_quantity": 1.0,
        "request_quantity": 1.0,
        "decision_price": 100.0,
    }
    values.update(overrides)
    return PaperDecisionLeg(**values)


def _fill(**overrides) -> FillObservation:
    values = {
        "fill_id": "fill-1",
        "quantity": 1.0,
        "price": 101.0,
        "filled_at_ms": 1_200,
    }
    values.update(overrides)
    return FillObservation(**values)


def _testnet(**overrides) -> OrderObservation:
    values = {
        "intent_id": "intent-1",
        "decision_id": "decision-1",
        "client_order_id": "client-1",
        "decided_at_ms": 1_000,
        "instrument_id": "BTC",
        "side": OrderSide.BUY,
        "target_signed_quantity": 1.0,
        "request_quantity": 1.0,
        "submitted_at_ms": 1_100,
        "order_status": "FILLED",
        "fills": (_fill(),),
    }
    values.update(overrides)
    return OrderObservation(**values)


def _report(*, paper=(), testnet=(), paper_positions=None, testnet_positions=None,
            paper_equity=1_000.0, testnet_equity=1_000.0,
            fee_total=1.5, funding_total=-0.2, policy=None):
    return build_mft_parity_report(
        canonical_run_id=RUN,
        paper_legs=paper or (_paper(),),
        testnet_orders=testnet or (_testnet(),),
        paper_positions={"BTC": 1.0} if paper_positions is None else paper_positions,
        testnet_positions={"BTC": 1.0} if testnet_positions is None else testnet_positions,
        paper_equity=paper_equity,
        testnet_equity=testnet_equity,
        testnet_fee_total=fee_total,
        testnet_funding_total=funding_total,
        policy=policy or _policy(),
        as_of_ms=NOW,
    )


def test_exact_identity_full_fill_reports_testnet_tca_without_production_claim() -> None:
    report = _report()

    assert report["schema"] == "mft-parity.v1"
    assert report["status"] == "PASS"
    assert report["evidence_status"] == "SUFFICIENT_TESTNET_EXECUTION_EVIDENCE"
    assert report["microstructure_scope"] == "TESTNET_MICROSTRUCTURE_NOT_PRODUCTION"
    assert report["engineering_gate"]["calibration"] == "UNCALIBRATED"
    assert report["engineering_gate"]["is_real_money_mandate"] is False
    leg = report["legs"][0]
    assert leg["identity_status"] == "EXACT_MATCH"
    assert leg["fill_ratio"] == pytest.approx(1.0)
    assert leg["slippage_bps"] == pytest.approx(100.0)
    assert leg["first_fill_latency_ms"] == 200
    assert report["position_drift"]["BTC"] == pytest.approx(0.0)
    assert report["equity_drift"] == pytest.approx(0.0)


def test_no_testnet_fill_is_insufficient_not_zero_cost_perfection() -> None:
    report = _report(testnet=(_testnet(order_status="NEW", fills=()),))

    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert "NO_TESTNET_FILLS" in report["reasons"]
    assert report["aggregate"]["fill_ratio"] == pytest.approx(0.0)
    assert report["aggregate"]["slippage_bps"] is None
    assert report["aggregate"]["first_fill_latency_ms"] is None
    assert report["engineering_gate"]["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_partial_fill_is_measured_and_fails_versioned_fill_gate() -> None:
    partial = _fill(quantity=0.4, price=102.0)
    report = _report(
        testnet=(_testnet(order_status="PARTIALLY_FILLED", fills=(partial,)),),
        testnet_positions={"BTC": 0.4},
    )

    assert report["status"] == "FAIL"
    assert report["aggregate"]["fill_ratio"] == pytest.approx(0.4)
    assert report["aggregate"]["slippage_bps"] == pytest.approx(200.0)
    assert "MIN_FILL_RATIO_BREACHED" in report["engineering_gate"]["breaches"]
    assert "POSITION_DRIFT_BREACHED:BTC" in report["engineering_gate"]["breaches"]


def test_missing_paper_leg_and_unexpected_testnet_leg_are_not_silently_zipped() -> None:
    extra = _testnet(
        intent_id="intent-2",
        decision_id="decision-2",
        client_order_id="client-2",
        instrument_id="ETH",
        target_signed_quantity=-2.0,
        request_quantity=2.0,
        side=OrderSide.SELL,
        fills=(),
    )
    report = _report(testnet=(_testnet(), extra))

    assert report["status"] == "FAIL"
    assert report["identity"]["unexpected_testnet"] == [
        ["intent-2", "decision-2", "client-2"]
    ]
    assert "UNEXPECTED_TESTNET_ORDER" in report["reasons"]


def test_identity_mismatch_becomes_missing_and_unexpected_not_near_match() -> None:
    report = _report(testnet=(_testnet(client_order_id="wrong-client"),))

    assert report["status"] == "FAIL"
    assert report["identity"]["missing_testnet"] == [
        ["intent-1", "decision-1", "client-1"]
    ]
    assert report["identity"]["unexpected_testnet"] == [
        ["intent-1", "decision-1", "wrong-client"]
    ]


def test_client_order_id_cannot_be_reused_across_different_identity() -> None:
    duplicate = _testnet(intent_id="intent-2", decision_id="decision-2", fills=())
    report = _report(testnet=(_testnet(), duplicate))

    assert report["status"] == "FAIL"
    assert "DUPLICATE_TESTNET_CLIENT_ORDER_ID" in report["reasons"]


def test_fill_id_cannot_be_counted_across_two_testnet_orders() -> None:
    paper_two = (
        _paper(),
        _paper(intent_id="intent-2", decision_id="decision-2", client_order_id="client-2",
               instrument_id="ETH", side=OrderSide.SELL, target_signed_quantity=-1.0),
    )
    duplicated_fill = _fill()
    testnet_two = (
        _testnet(),
        _testnet(intent_id="intent-2", decision_id="decision-2", client_order_id="client-2",
                 instrument_id="ETH", side=OrderSide.SELL, target_signed_quantity=-1.0,
                 fills=(duplicated_fill,)),
    )
    report = _report(
        paper=paper_two,
        testnet=testnet_two,
        paper_positions={"BTC": 1.0, "ETH": -1.0},
        testnet_positions={"BTC": 1.0, "ETH": -1.0},
    )

    assert report["status"] == "FAIL"
    assert "DUPLICATE_TESTNET_FILL_ID" in report["reasons"]


def test_empty_paper_decision_set_is_not_a_parity_run() -> None:
    with pytest.raises(ValueError, match="paper_legs must not be empty"):
        build_mft_parity_report(
            canonical_run_id=RUN,
            paper_legs=(),
            testnet_orders=(),
            paper_positions={},
            testnet_positions={},
            paper_equity=1_000.0,
            testnet_equity=1_000.0,
            testnet_fee_total=None,
            testnet_funding_total=None,
            policy=_policy(),
            as_of_ms=NOW,
        )


def test_missing_fee_and_funding_are_unknown_not_zero() -> None:
    report = _report(fee_total=None, funding_total=None)

    assert report["status"] == "PASS"
    assert report["cost_evidence"] == {
        "status": "INCOMPLETE",
        "testnet_fee_total": None,
        "testnet_funding_total": None,
        "unknown": ["FEES_UNKNOWN", "FUNDING_UNKNOWN"],
    }


def test_same_identity_with_different_decision_time_fails() -> None:
    report = _report(testnet=(_testnet(decided_at_ms=999),))

    assert report["status"] == "FAIL"
    assert report["legs"][0]["decision_time_status"] == "MISMATCH"
    assert "DECISION_TIME_MISMATCH:client-1" in report["reasons"]


def test_future_submission_and_fill_are_rejected() -> None:
    valid_fill = _fill(quantity=0.5)
    future_fill = _fill(fill_id="fill-future", quantity=0.5, filled_at_ms=NOW + 1)
    report = _report(
        testnet=(_testnet(submitted_at_ms=NOW + 1, fills=(valid_fill, future_fill)),)
    )

    assert report["status"] == "FAIL"
    assert "SUBMISSION_FROM_FUTURE:client-1" in report["reasons"]
    assert "FILL_FROM_FUTURE:client-1" in report["reasons"]
    assert report["legs"][0]["first_fill_latency_ms"] is None


def test_sell_side_slippage_direction_and_latency_are_correct() -> None:
    paper = _paper(side=OrderSide.SELL, target_signed_quantity=-1.0)
    fill = _fill(price=99.0)
    testnet = _testnet(
        side=OrderSide.SELL,
        target_signed_quantity=-1.0,
        fills=(fill,),
    )
    report = _report(
        paper=(paper,),
        testnet=(testnet,),
        paper_positions={"BTC": -1.0},
        testnet_positions={"BTC": -1.0},
    )

    assert report["legs"][0]["slippage_bps"] == pytest.approx(100.0)
    assert report["legs"][0]["first_fill_latency_ms"] == 200


def test_input_order_does_not_change_hash_or_report_identity() -> None:
    paper = (_paper(), _paper(intent_id="i2", decision_id="d2", client_order_id="c2",
                              instrument_id="ETH", target_signed_quantity=-2.0,
                              request_quantity=2.0, side=OrderSide.SELL))
    testnet = (_testnet(), _testnet(intent_id="i2", decision_id="d2", client_order_id="c2",
                                    instrument_id="ETH", target_signed_quantity=-2.0,
                                    request_quantity=2.0, side=OrderSide.SELL,
                                    fills=(_fill(fill_id="f2", quantity=2.0),)))
    left = _report(paper=paper, testnet=testnet, paper_positions={"BTC": 1.0, "ETH": -2.0},
                   testnet_positions={"BTC": 1.0, "ETH": -2.0})
    right = _report(paper=tuple(reversed(paper)), testnet=tuple(reversed(testnet)),
                    paper_positions={"ETH": -2.0, "BTC": 1.0},
                    testnet_positions={"ETH": -2.0, "BTC": 1.0})

    assert left["input_hash"] == right["input_hash"]
    assert left["report_id"] == right["report_id"]
    assert left == right


def test_append_only_store_binds_canonical_run_and_exact_input(tmp_path) -> None:
    path = str(tmp_path / "parity.db")
    store = MftParityStore(path, create=True)
    report = _report()

    assert store.append(report, recorded_at_ms=NOW)
    assert not store.append(report, recorded_at_ms=NOW)
    assert store.chain_valid()
    assert store.latest(RUN)["report_id"] == report["report_id"]

    changed = _report(testnet_positions={"BTC": 0.99})
    assert store.append(changed, recorded_at_ms=NOW + 1)
    assert len(store.rows(canonical_run_id=RUN)) == 2
    assert store.chain_valid()

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("UPDATE mft_parity_reports SET status='PASS'")
    store.close()


def test_store_rejects_report_id_reuse_and_missing_schema(tmp_path) -> None:
    path = str(tmp_path / "parity.db")
    store = MftParityStore(path, create=True)
    report = _report()
    assert store.append(report, recorded_at_ms=NOW)
    tampered = {**report, "status": "FAIL"}
    with pytest.raises(MftParityStoreError, match="report_id reuse"):
        store.append(tampered, recorded_at_ms=NOW)
    fabricated = {**report, "input_hash": "0" * 64}
    with pytest.raises(MftParityStoreError, match="input_hash"):
        store.append(fabricated, recorded_at_ms=NOW)
    store.close()

    with pytest.raises(MftParityStoreError, match="schema"):
        MftParityStore(str(tmp_path / "missing.db"), create=False)


@pytest.mark.parametrize(
    "factory,kwargs",
    (
        (PaperDecisionLeg, {"request_quantity": float("nan")}),
        (FillObservation, {"quantity": 0.0}),
        (OrderObservation, {"submitted_at_ms": -1}),
        (MftParityPolicy, {"min_fill_ratio": 1.1}),
    ),
)
def test_contracts_reject_malformed_numeric_inputs(factory, kwargs) -> None:
    bases = {
        PaperDecisionLeg: _paper().__dict__,
        FillObservation: _fill().__dict__,
        OrderObservation: _testnet().__dict__,
        MftParityPolicy: _policy().__dict__,
    }
    with pytest.raises((TypeError, ValueError)):
        factory(**{**bases[factory], **kwargs})
