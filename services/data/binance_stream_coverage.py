"""Pure validation for the official Binance USD-M WebSocket stream catalog."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


STATUSES = frozenset({"LOAD_BEARING", "REDUNDANT", "NOT_APPLICABLE", "GAP"})
ROUTES = frozenset({"PUBLIC", "MARKET"})


class BinanceStreamCoverageError(ValueError):
    """The checked-in WebSocket coverage contract is incomplete or invalid."""


def assess_stream_coverage(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise BinanceStreamCoverageError("unsupported stream coverage schema")
    capabilities = payload.get("capabilities")
    expected = payload.get("catalog_stream_count")
    if (
        not isinstance(capabilities, Sequence)
        or isinstance(capabilities, (str, bytes))
        or isinstance(expected, bool)
        or not isinstance(expected, int)
        or expected <= 0
    ):
        raise BinanceStreamCoverageError("stream coverage catalog shape is invalid")
    if len(capabilities) != expected:
        raise BinanceStreamCoverageError("official stream count mismatch")

    ids: set[str] = set()
    route_streams: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    gaps: list[dict[str, str]] = []
    for raw in capabilities:
        if not isinstance(raw, Mapping):
            raise BinanceStreamCoverageError("stream capability must be an object")
        capability_id = raw.get("id")
        title = raw.get("title")
        route = raw.get("route")
        stream = raw.get("stream")
        status = raw.get("status")
        implementation = raw.get("implementation")
        reason = raw.get("reason")
        if (
            not isinstance(capability_id, str)
            or not capability_id
            or not isinstance(title, str)
            or not title
            or route not in ROUTES
            or not isinstance(stream, str)
            or not stream
            or status not in STATUSES
            or not isinstance(implementation, list)
            or not all(isinstance(item, str) and item for item in implementation)
            or not isinstance(reason, str)
            or not reason
        ):
            raise BinanceStreamCoverageError(
                f"invalid stream capability contract: {capability_id!r}"
            )
        key = (route, stream)
        if capability_id in ids or key in route_streams:
            raise BinanceStreamCoverageError("duplicate stream capability identity")
        ids.add(capability_id)
        route_streams.add(key)
        counts[status] += 1
        if status == "LOAD_BEARING" and not implementation:
            raise BinanceStreamCoverageError(
                f"load-bearing stream lacks implementation: {capability_id}"
            )
        if status == "GAP":
            gaps.append(
                {
                    "id": capability_id,
                    "title": title,
                    "route": route,
                    "stream": stream,
                    "reason": reason,
                }
            )

    accounted = expected - counts["GAP"]
    return {
        "schema_version": 1,
        "product": payload.get("product"),
        "scope": payload.get("scope"),
        "official_public_catalog_url": payload.get(
            "official_public_catalog_url"
        ),
        "official_market_catalog_url": payload.get(
            "official_market_catalog_url"
        ),
        "catalog_stream_count": expected,
        "status_counts": {status: counts[status] for status in sorted(STATUSES)},
        "accounted_stream_count": accounted,
        "accounted_ratio": accounted / expected,
        "load_bearing_stream_count": counts["LOAD_BEARING"],
        "gap_count": counts["GAP"],
        "gaps": gaps,
        "safety": {
            "public_market_data_only": True,
            "credentials_loaded": False,
            "private_streams": False,
            "account_events": False,
            "order_submission": False,
        },
    }


__all__ = [
    "BinanceStreamCoverageError",
    "ROUTES",
    "STATUSES",
    "assess_stream_coverage",
]
