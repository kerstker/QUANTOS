"""Market data ingestion interface (Phase 2).

Domain code depends on the MarketDataSource interface, never on a concrete
vendor SDK (blueprint §5.4 adapter isolation). The only implementation here is
an in-memory/synthetic source for research and tests — there is NO real feed.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Protocol, runtime_checkable

from quantos.contracts.data import Bar


@runtime_checkable
class MarketDataSource(Protocol):
    def fetch_bars(self, instrument_id: str, start: int, end: int) -> list[Bar]:
        ...


class InMemoryMarketDataSource:
    """Synthetic, deterministic source. Holds pre-loaded bars; fetch returns the
    inclusive [start, end] event_time window, time-ordered."""

    def __init__(self) -> None:
        self._bars: dict[str, list[Bar]] = defaultdict(list)

    def load(self, instrument_id: str, bars) -> None:
        self._bars[instrument_id].extend(bars)

    def fetch_bars(self, instrument_id: str, start: int, end: int) -> list[Bar]:
        rows = [b for b in self._bars.get(instrument_id, []) if start <= b.event_time <= end]
        return sorted(rows, key=lambda b: (b.event_time, b.knowledge_time))
