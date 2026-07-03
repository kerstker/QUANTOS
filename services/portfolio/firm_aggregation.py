"""Firm-level cross-book aggregation (Rank #25) — a read-only diagnostic.

Each dry-run book is dollar-neutral and looks fine on its own, but the firm holds the SUM of their
positions: a name held (same side) by several 'independent' books is a hidden concentration no
per-book view reveals (the 2007-Aug quant-crowding pattern). This aggregates the books' notional-
weighted positions into the true firm net/gross exposure per symbol and flags the overlaps
(symbols carried by >= 2 books). Pure/deterministic; no data stores, execution, or wall-clock.
Diagnostic only — it does not size or gate anything.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

_EPS = 1e-12


def aggregate_firm_books(book_positions: Mapping[str, Mapping[str, float]],
                         book_notionals: Optional[Mapping[str, float]] = None) -> dict:
    """Aggregate per-book positions (book -> {symbol: weight}) into the firm view. `book_notionals`
    scales each book's weights to $ (default 1.0 each = equal-notional). Returns firm net/gross,
    per-symbol firm position, and the overlaps (a symbol held same-or-mixed side by multiple books,
    which per-book views hide). Fail-closed: no books or a non-finite weight/notional -> BLOCKED."""
    if not book_positions:
        return {"status": "BLOCKED", "reasons": ("empty_book_positions",), "provenance": "REAL_FIRM_AGGREGATION"}

    notionals = {}
    for book in sorted(book_positions):
        n = 1.0 if book_notionals is None else book_notionals.get(book, 1.0)
        if not _finite(n) or float(n) < 0.0:
            return _blocked(f"invalid_notional:{book}")
        notionals[book] = float(n)

    combined: dict[str, float] = {}
    holders: dict[str, list[str]] = {}          # symbol -> books holding a non-trivial position
    for book in sorted(book_positions):
        for symbol in sorted(book_positions[book]):
            w = book_positions[book][symbol]
            if not _finite(w):
                return _blocked(f"invalid_weight:{book}:{symbol}")
            contrib = notionals[book] * float(w)
            if abs(float(w)) > _EPS:
                holders.setdefault(symbol, []).append(book)
            combined[symbol] = combined.get(symbol, 0.0) + contrib

    firm_gross = sum(abs(v) for v in combined.values())
    firm_net = sum(combined.values())
    overlaps = []
    for symbol in sorted(holders):
        books = holders[symbol]
        if len(books) >= 2:
            overlaps.append({"symbol": symbol, "n_books": len(books), "books": sorted(books),
                             "firm_position": round(combined[symbol], 10),
                             "firm_weight": round(combined[symbol] / firm_gross, 10) if firm_gross > _EPS else 0.0})
    overlaps.sort(key=lambda o: (-abs(o["firm_position"]), o["symbol"]))
    top = sorted(combined.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[:10]

    return {
        "status": "AGGREGATED",
        "n_books": len(book_positions),
        "firm_gross": round(firm_gross, 10),
        "firm_net": round(firm_net, 10),
        "n_overlaps": len(overlaps),
        "overlaps": overlaps,
        "top_firm_positions": [{"symbol": s, "firm_position": round(v, 10)} for s, v in top],
        "provenance": "REAL_FIRM_AGGREGATION",
    }


def _finite(x: object) -> bool:
    import math
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _blocked(reason: str) -> dict:
    return {"status": "BLOCKED", "reasons": (reason,), "provenance": "REAL_FIRM_AGGREGATION"}
