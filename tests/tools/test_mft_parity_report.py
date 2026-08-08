from __future__ import annotations

import json

import pytest

from services.monitoring.mft_parity_store import MftParityStore
from tools.mft_parity_report import generate_report


NOW = 2_000


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _paper():
    return {
        "canonical_run_id": "run-1",
        "positions": {"BTC": 1.0},
        "equity": 1_000.0,
        "legs": [
            {
                "intent_id": "intent-1",
                "decision_id": "decision-1",
                "client_order_id": "client-1",
                "decided_at_ms": 1_000,
                "instrument_id": "BTC",
                "side": "BUY",
                "target_signed_quantity": 1.0,
                "request_quantity": 1.0,
                "decision_price": 100.0,
            }
        ],
    }


def _testnet(*, fills=True):
    return {
        "canonical_run_id": "run-1",
        "positions": {"BTC": 1.0 if fills else 0.0},
        "equity": 1_000.0,
        "fee_total": 1.0 if fills else None,
        "funding_total": None,
        "orders": [
            {
                "intent_id": "intent-1",
                "decision_id": "decision-1",
                "client_order_id": "client-1",
                "decided_at_ms": 1_000,
                "instrument_id": "BTC",
                "side": "BUY",
                "target_signed_quantity": 1.0,
                "request_quantity": 1.0,
                "submitted_at_ms": 1_100,
                "order_status": "FILLED" if fills else "NEW",
            }
        ],
        "fills": (
            [
                {
                    "fill_id": "fill-1",
                    "client_order_id": "client-1",
                    "quantity": 1.0,
                    "price": 101.0,
                    "filled_at_ms": 1_200,
                }
            ]
            if fills
            else []
        ),
    }


def _policy():
    return {
        "policy_id": "mft-parity-engineering",
        "policy_version": "1",
        "max_target_quantity_diff": 0.01,
        "max_request_quantity_diff": 0.01,
        "min_fill_ratio": 0.8,
        "max_abs_slippage_bps": 200.0,
        "max_latency_ms": 500,
        "max_position_drift": 0.01,
        "max_abs_equity_drift": 10.0,
    }


def test_generate_report_writes_atomic_artifact_and_append_only_row(tmp_path) -> None:
    paper = tmp_path / "paper.json"
    testnet = tmp_path / "testnet.json"
    policy = tmp_path / "policy.json"
    output = tmp_path / "report.json"
    store_path = tmp_path / "parity.db"
    _write(paper, _paper())
    _write(testnet, _testnet())
    _write(policy, _policy())

    report = generate_report(
        paper_path=str(paper),
        testnet_path=str(testnet),
        policy_path=str(policy),
        store_path=str(store_path),
        output_path=str(output),
        as_of_ms=NOW,
    )

    assert report["status"] == "PASS"
    assert json.loads(output.read_text(encoding="utf-8")) == report
    store = MftParityStore(str(store_path))
    assert store.rows()[0]["report_id"] == report["report_id"]
    assert store.chain_valid()
    store.close()

    replay = generate_report(
        paper_path=str(paper),
        testnet_path=str(testnet),
        policy_path=str(policy),
        store_path=str(store_path),
        output_path=str(output),
        as_of_ms=NOW,
    )
    assert replay == report
    store = MftParityStore(str(store_path))
    assert len(store.rows()) == 1
    store.close()


def test_generate_report_without_fills_is_explicitly_insufficient(tmp_path) -> None:
    paper = tmp_path / "paper.json"
    testnet = tmp_path / "testnet.json"
    policy = tmp_path / "policy.json"
    _write(paper, _paper())
    _write(testnet, _testnet(fills=False))
    _write(policy, _policy())

    report = generate_report(
        paper_path=str(paper),
        testnet_path=str(testnet),
        policy_path=str(policy),
        store_path=str(tmp_path / "parity.db"),
        output_path=str(tmp_path / "report.json"),
        as_of_ms=NOW,
    )

    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["aggregate"]["slippage_bps"] is None
    assert report["cost_evidence"]["testnet_fee_total"] is None


def test_generate_report_rejects_cross_run_or_orphan_fill(tmp_path) -> None:
    paper = tmp_path / "paper.json"
    testnet = tmp_path / "testnet.json"
    policy = tmp_path / "policy.json"
    _write(paper, _paper())
    wrong = _testnet()
    wrong["canonical_run_id"] = "other-run"
    _write(testnet, wrong)
    _write(policy, _policy())

    with pytest.raises(ValueError, match="canonical run"):
        generate_report(
            paper_path=str(paper), testnet_path=str(testnet), policy_path=str(policy),
            store_path=str(tmp_path / "parity.db"), output_path=str(tmp_path / "out.json"),
            as_of_ms=NOW,
        )

    wrong["canonical_run_id"] = "run-1"
    wrong["fills"][0]["client_order_id"] = "orphan"
    _write(testnet, wrong)
    with pytest.raises(ValueError, match="orphan fill"):
        generate_report(
            paper_path=str(paper), testnet_path=str(testnet), policy_path=str(policy),
            store_path=str(tmp_path / "parity.db"), output_path=str(tmp_path / "out.json"),
            as_of_ms=NOW,
        )
