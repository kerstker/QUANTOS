"""Pure validation and assessment for the official Binance USD-M market catalog."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


STATUSES = frozenset(
    {
        "LOAD_BEARING",
        "REDUNDANT",
        "NOT_APPLICABLE",
        "BLOCKED_CREDENTIALS",
        "GAP",
    }
)
SECURITY_TYPES = frozenset({"NONE", "MARKET_DATA"})


class BinanceApiCoverageError(ValueError):
    """The checked-in coverage contract is incomplete or internally invalid."""


def assess_coverage(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise BinanceApiCoverageError("unsupported coverage schema")
    capabilities = payload.get("capabilities")
    expected = payload.get("catalog_endpoint_count")
    if (
        not isinstance(capabilities, Sequence)
        or isinstance(capabilities, (str, bytes))
        or isinstance(expected, bool)
        or not isinstance(expected, int)
        or expected <= 0
    ):
        raise BinanceApiCoverageError("coverage catalog shape is invalid")
    if len(capabilities) != expected:
        raise BinanceApiCoverageError("official endpoint count mismatch")

    ids: set[str] = set()
    method_paths: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    gaps: list[dict[str, str]] = []
    for raw in capabilities:
        if not isinstance(raw, Mapping):
            raise BinanceApiCoverageError("capability must be an object")
        capability_id = raw.get("id")
        title = raw.get("title")
        method = raw.get("method")
        path = raw.get("path")
        security = raw.get("security")
        status = raw.get("status")
        implementation = raw.get("implementation")
        reason = raw.get("reason")
        if (
            not isinstance(capability_id, str)
            or not capability_id
            or not isinstance(title, str)
            or not title
            or method != "GET"
            or not isinstance(path, str)
            or not path.startswith("/")
            or security not in SECURITY_TYPES
            or status not in STATUSES
            or not isinstance(implementation, list)
            or not all(isinstance(item, str) and item for item in implementation)
            or not isinstance(reason, str)
            or not reason
        ):
            raise BinanceApiCoverageError(
                f"invalid capability contract: {capability_id!r}"
            )
        key = (method, path)
        if capability_id in ids or key in method_paths:
            raise BinanceApiCoverageError("duplicate capability identity")
        ids.add(capability_id)
        method_paths.add(key)
        counts[status] += 1
        if status == "LOAD_BEARING" and not implementation:
            raise BinanceApiCoverageError(
                f"load-bearing capability lacks implementation: {capability_id}"
            )
        if status == "GAP":
            gaps.append(
                {
                    "id": capability_id,
                    "title": title,
                    "path": path,
                    "reason": reason,
                }
            )

    accounted = expected - counts["GAP"]
    return {
        "schema_version": 1,
        "product": payload.get("product"),
        "scope": payload.get("scope"),
        "official_catalog_url": payload.get("official_catalog_url"),
        "catalog_endpoint_count": expected,
        "status_counts": {status: counts[status] for status in sorted(STATUSES)},
        "accounted_endpoint_count": accounted,
        "accounted_ratio": accounted / expected,
        "load_bearing_endpoint_count": counts["LOAD_BEARING"],
        "gap_count": counts["GAP"],
        "gaps": gaps,
        "safety": {
            "public_market_data_only": True,
            "credentials_loaded": False,
            "account_endpoints": False,
            "trade_endpoints": False,
            "order_submission": False,
        },
    }


__all__ = [
    "BinanceApiCoverageError",
    "SECURITY_TYPES",
    "STATUSES",
    "assess_coverage",
]
