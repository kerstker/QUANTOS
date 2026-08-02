"""Longitudinal analytics over retained simulation-only route TCA records.

The append-only ``DryRunStore`` remains the authority.  This module is a pure,
read-only projection: it verifies every retained route-TCA record before
building route and venue time series.  It has no broker, network or order
submission capability.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

from quantos.contracts.trading import OrderSide
from services.execution.simulated_tca import verify_route_tca_record


class PaperTCAHistoryError(ValueError):
    """Retained dry-run TCA evidence is malformed or internally inconsistent."""


def _weighted_bps(shortfalls: list[tuple[Decimal, Decimal]]) -> str | None:
    notional = sum((weight for weight, _ in shortfalls), Decimal("0"))
    if notional <= 0:
        return None
    return str(sum((weight * bps for weight, bps in shortfalls), Decimal("0")) / notional)


def _record_from_envelope(value: object) -> tuple[int | None, Mapping[str, object]]:
    if isinstance(value, Mapping) and set(value) == {"period", "tca"}:
        period = value["period"]
        record = value["tca"]
        if type(period) is not int or not isinstance(record, Mapping):
            raise PaperTCAHistoryError("multi-period TCA envelope is invalid")
        return period, record
    if not isinstance(value, Mapping):
        raise PaperTCAHistoryError("route TCA record is invalid")
    return None, value


def build_paper_tca_history(runs: Sequence[Mapping[str, object]]) -> dict:
    """Project verified route-TCA evidence into longitudinal route/venue views."""

    points: list[dict] = []
    venue_acc: dict[str, dict[str, object]] = {}
    all_shortfalls: list[tuple[Decimal, Decimal]] = []
    routed_runs: set[str] = set()
    timestamps: set[int] = set()

    for run in sorted(runs, key=lambda row: (row.get("timestamp", -1), str(row.get("run_id", "")))):
        if not isinstance(run, Mapping):
            raise PaperTCAHistoryError("dry-run history row must be an object")
        detail = run.get("detail")
        if not isinstance(detail, Mapping) or detail.get("route_tca_schema_version") is None:
            continue
        run_id = run.get("run_id")
        strategy_id = run.get("strategy_id")
        timestamp = run.get("timestamp")
        snapshot_id = detail.get("routing_snapshot_id")
        records = detail.get("route_tca")
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(strategy_id, str)
            or not strategy_id
            or type(timestamp) is not int
            or timestamp < 0
            or not isinstance(snapshot_id, str)
            or not snapshot_id
            or not isinstance(records, list)
        ):
            raise PaperTCAHistoryError("routed dry-run metadata is invalid")
        for index, envelope in enumerate(records):
            period, record = _record_from_envelope(envelope)
            if not verify_route_tca_record(record):
                raise PaperTCAHistoryError("retained route TCA failed verification")
            filled = int(record["filled_quantity"])
            reference = Decimal(str(record["reference_price"]))
            reference_notional = reference * filled
            shortfall = Decimal(str(record["implementation_shortfall_bps"]))
            if reference_notional > 0:
                all_shortfalls.append((reference_notional, shortfall))
            point = {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "timestamp": timestamp,
                "routing_snapshot_id": snapshot_id,
                "record_index": index,
                "period": period,
                "instrument_id": record["instrument_id"],
                "side": record["side"],
                "route_status": record["route_status"],
                "requested_quantity": record["requested_quantity"],
                "filled_quantity": filled,
                "fill_fraction": record["fill_fraction"],
                "reference_notional": str(reference_notional),
                "implementation_shortfall_bps": str(shortfall),
                "venues": [item["venue_id"] for item in record["venues"]],
            }
            points.append(point)
            routed_runs.add(run_id)
            timestamps.add(timestamp)

            for venue in record["venues"]:
                venue_id = str(venue["venue_id"])
                quantity = int(venue["filled_quantity"])
                venue_reference = reference * quantity
                raw = Decimal(str(venue["raw_notional"]))
                cost = Decimal(str(venue["route_cost"]))
                all_in = (
                    raw + cost
                    if record["side"] == OrderSide.BUY.value
                    else raw - cost
                )
                signed = (
                    all_in - venue_reference
                    if record["side"] == OrderSide.BUY.value
                    else venue_reference - all_in
                )
                venue_bps = signed / venue_reference * Decimal("10000")
                acc = venue_acc.setdefault(
                    venue_id,
                    {
                        "record_count": 0,
                        "filled_quantity": 0,
                        "reference_notional": Decimal("0"),
                        "shortfalls": [],
                        "first_timestamp": timestamp,
                        "last_timestamp": timestamp,
                    },
                )
                acc["record_count"] = int(acc["record_count"]) + 1
                acc["filled_quantity"] = int(acc["filled_quantity"]) + quantity
                acc["reference_notional"] = Decimal(acc["reference_notional"]) + venue_reference
                acc["shortfalls"].append((venue_reference, venue_bps))
                acc["first_timestamp"] = min(int(acc["first_timestamp"]), timestamp)
                acc["last_timestamp"] = max(int(acc["last_timestamp"]), timestamp)

    venues = [
        {
            "venue_id": venue_id,
            "record_count": int(acc["record_count"]),
            "filled_quantity": int(acc["filled_quantity"]),
            "reference_notional": str(acc["reference_notional"]),
            "weighted_shortfall_bps": _weighted_bps(acc["shortfalls"]),
            "first_timestamp": int(acc["first_timestamp"]),
            "last_timestamp": int(acc["last_timestamp"]),
        }
        for venue_id, acc in sorted(venue_acc.items())
    ]
    status = (
        "NO_ROUTE_TCA"
        if not points
        else "LONGITUDINAL_READY"
        if len(timestamps) >= 2
        else "SINGLE_SNAPSHOT"
    )
    return {
        "schema_version": 1,
        "simulation_only": True,
        "status": status,
        "run_count": len(routed_runs),
        "record_count": len(points),
        "venue_count": len(venues),
        "first_timestamp": min(timestamps) if timestamps else None,
        "last_timestamp": max(timestamps) if timestamps else None,
        "weighted_shortfall_bps": _weighted_bps(all_shortfalls),
        "points": points,
        "venues": venues,
    }


__all__ = [
    "PaperTCAHistoryError",
    "build_paper_tca_history",
]
