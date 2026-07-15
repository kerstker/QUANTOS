"""Data-owned adapter for the research market-data port."""

from __future__ import annotations

from typing import Optional, Sequence

from services.data.versioning import content_hash_of


class PITResearchDataAdapter:
    """Expose PIT reads plus a stable dataset version over a raw bar store."""

    def __init__(self, source) -> None:
        missing = [
            name for name in ("as_of", "latest_as_of", "fetch_bars") if not callable(getattr(source, name, None))
        ]
        if missing:
            raise TypeError("research market-data source is missing callable method(s): " + ", ".join(missing))
        self._source = source

    def as_of(
        self,
        key: str,
        as_of_time: int,
        event_time_max: Optional[int] = None,
    ):
        return self._source.as_of(key, as_of_time, event_time_max)

    def latest_as_of(
        self,
        key: str,
        as_of_time: int,
        event_time_max: Optional[int] = None,
    ):
        return self._source.latest_as_of(key, as_of_time, event_time_max)

    def dataset_version(
        self,
        instruments: Sequence[str],
        event_times: Sequence[int],
    ) -> str:
        times = tuple(event_times)
        if not times:
            return ""
        lower, upper = min(times), max(times)
        bars = [bar for instrument in instruments for bar in self._source.fetch_bars(instrument, lower, upper)]
        return content_hash_of(bars) if bars else ""


def adapt_research_market_data(source):
    """Keep an existing versioned port or adapt a raw PIT store once."""
    required = ("as_of", "latest_as_of", "dataset_version")
    if all(callable(getattr(source, name, None)) for name in required):
        return source
    return PITResearchDataAdapter(source)
