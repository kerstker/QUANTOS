"""Point-in-time market-data interfaces owned by the research core."""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from quantos.contracts.data import Bar


@runtime_checkable
class PointInTimeMarketDataPort(Protocol):
    def as_of(
        self,
        key: str,
        as_of_time: int,
        event_time_max: Optional[int] = None,
    ) -> Sequence[Bar]: ...

    def latest_as_of(
        self,
        key: str,
        as_of_time: int,
        event_time_max: Optional[int] = None,
    ) -> Optional[Bar]: ...


@runtime_checkable
class VersionedMarketDataPort(PointInTimeMarketDataPort, Protocol):
    def dataset_version(
        self,
        instruments: Sequence[str],
        event_times: Sequence[int],
    ) -> str: ...
