"""Generate and persist a Paper/Testnet MFT parity report."""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
import sys
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from quantos.contracts.trading import OrderSide
from services.monitoring.mft_parity import (
    MftParityPolicy,
    PaperDecisionLeg,
    TestnetFillObservation,
    TestnetOrderObservation,
    build_mft_parity_report,
)
from services.monitoring.mft_parity_store import MftParityStore
from tools.cache_io import atomic_write_json


def _load(path: str, *, label: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} input is unavailable or malformed") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} input must be a JSON object")
    return value


def _paper_leg(row: Mapping[str, Any]) -> PaperDecisionLeg:
    return PaperDecisionLeg(
        intent_id=row.get("intent_id"),
        decision_id=row.get("decision_id"),
        client_order_id=row.get("client_order_id"),
        decided_at_ms=row.get("decided_at_ms"),
        instrument_id=row.get("instrument_id"),
        side=OrderSide(row.get("side")),
        target_signed_quantity=row.get("target_signed_quantity"),
        request_quantity=row.get("request_quantity"),
        decision_price=row.get("decision_price"),
    )


def _fill(row: Mapping[str, Any]) -> tuple[str, TestnetFillObservation]:
    client_order_id = row.get("client_order_id")
    if not isinstance(client_order_id, str) or not client_order_id:
        raise ValueError("fill client_order_id is invalid")
    return client_order_id, TestnetFillObservation(
        fill_id=row.get("fill_id"),
        quantity=row.get("quantity"),
        price=row.get("price"),
        filled_at_ms=row.get("filled_at_ms"),
    )


def _order(row: Mapping[str, Any], fills: tuple[TestnetFillObservation, ...]) -> TestnetOrderObservation:
    return TestnetOrderObservation(
        intent_id=row.get("intent_id"),
        decision_id=row.get("decision_id"),
        client_order_id=row.get("client_order_id"),
        decided_at_ms=row.get("decided_at_ms"),
        instrument_id=row.get("instrument_id"),
        side=OrderSide(row.get("side")),
        target_signed_quantity=row.get("target_signed_quantity"),
        request_quantity=row.get("request_quantity"),
        submitted_at_ms=row.get("submitted_at_ms"),
        order_status=row.get("order_status"),
        fills=fills,
    )


def _policy(document: Mapping[str, Any]) -> MftParityPolicy:
    names = (
        "policy_id",
        "policy_version",
        "max_target_quantity_diff",
        "max_request_quantity_diff",
        "min_fill_ratio",
        "max_abs_slippage_bps",
        "max_latency_ms",
        "max_position_drift",
        "max_abs_equity_drift",
    )
    return MftParityPolicy(**{name: document.get(name) for name in names})


def generate_report(
    *,
    paper_path: str,
    testnet_path: str,
    policy_path: str,
    store_path: str,
    output_path: str,
    as_of_ms: int,
) -> dict[str, Any]:
    paper = _load(paper_path, label="paper")
    testnet = _load(testnet_path, label="testnet")
    policy_document = _load(policy_path, label="policy")
    canonical_run_id = paper.get("canonical_run_id")
    if canonical_run_id != testnet.get("canonical_run_id"):
        raise ValueError("paper and Testnet inputs must bind the same canonical run")
    paper_rows = paper.get("legs")
    order_rows = testnet.get("orders")
    fill_rows = testnet.get("fills")
    if not isinstance(paper_rows, list) or not isinstance(order_rows, list) or not isinstance(fill_rows, list):
        raise ValueError("paper legs and Testnet orders/fills must be lists")
    parsed_fills: dict[str, list[TestnetFillObservation]] = {}
    fill_ids: set[str] = set()
    for row in fill_rows:
        if not isinstance(row, Mapping):
            raise ValueError("Testnet fill row must be an object")
        client_order_id, observation = _fill(row)
        if observation.fill_id in fill_ids:
            raise ValueError("Testnet fill_id is duplicated")
        fill_ids.add(observation.fill_id)
        parsed_fills.setdefault(client_order_id, []).append(observation)
    order_client_ids = {
        row.get("client_order_id") for row in order_rows if isinstance(row, Mapping)
    }
    orphan = sorted(set(parsed_fills) - order_client_ids)
    if orphan:
        raise ValueError(f"orphan fill has no Testnet order: {orphan}")
    paper_legs = tuple(_paper_leg(row) for row in paper_rows if isinstance(row, Mapping))
    if len(paper_legs) != len(paper_rows):
        raise ValueError("paper leg row must be an object")
    testnet_orders = tuple(
        _order(row, tuple(parsed_fills.get(str(row.get("client_order_id")), ())))
        for row in order_rows
        if isinstance(row, Mapping)
    )
    if len(testnet_orders) != len(order_rows):
        raise ValueError("Testnet order row must be an object")
    report = build_mft_parity_report(
        canonical_run_id=canonical_run_id,
        paper_legs=paper_legs,
        testnet_orders=testnet_orders,
        paper_positions=paper.get("positions"),
        testnet_positions=testnet.get("positions"),
        paper_equity=paper.get("equity"),
        testnet_equity=testnet.get("equity"),
        testnet_fee_total=testnet.get("fee_total"),
        testnet_funding_total=testnet.get("funding_total"),
        policy=_policy(policy_document),
        as_of_ms=as_of_ms,
    )
    create = not os.path.exists(store_path)
    store = MftParityStore(store_path, create=create)
    try:
        store.append(report, recorded_at_ms=as_of_ms)
    finally:
        store.close()
    output_parent = os.path.dirname(os.path.abspath(output_path))
    if output_parent:
        os.makedirs(output_parent, exist_ok=True)
    atomic_write_json(output_path, report, indent=2)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True)
    parser.add_argument("--testnet", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--as-of-ms", type=int, required=True)
    args = parser.parse_args(argv)
    report = generate_report(
        paper_path=args.paper,
        testnet_path=args.testnet,
        policy_path=args.policy,
        store_path=args.store,
        output_path=args.output,
        as_of_ms=args.as_of_ms,
    )
    print(json.dumps({
        "report_id": report["report_id"],
        "status": report["status"],
        "microstructure_scope": report["microstructure_scope"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["generate_report", "main"]
