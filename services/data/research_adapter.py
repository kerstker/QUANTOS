"""Data-owned adapter for the research market-data port.

Two identity surfaces, deliberately different:

* ``dataset_version(instruments, event_times)`` — the ORIGINAL content fingerprint of the
  raw records inside the event window. Kept byte-for-byte for the callers that pin it
  (the research drivers write it into evidence as ``bar_version`` /
  ``bar_source_version``).
* ``dataset_identity(instruments, event_times)`` — the BOUND identity a research session
  persists: it binds the exact instrument set, the decision event grid, the knowledge-time
  PIT policy (including the decision / minimum-knowledge lags), the source/version stamp
  and the canonical record hash, so any change in any of them changes the identity.
  A content fingerprint alone lets two runs over different data contracts (a wider grid,
  one more instrument, a different knowledge clock) share one accounting identity.
"""

from __future__ import annotations

from typing import Optional, Sequence

from quantos.contracts.hashing import stable_hash
from services.data.versioning import content_hash_of

# The knowledge-time policy every read through this adapter implements: a record is visible
# only when its knowledge_time <= the decision time. It is part of the dataset identity, so a
# run consumed under a different PIT policy cannot reuse this run's accounting identity.
PIT_POLICY_ID = "as_of_knowledge_time_le_decision_time"


def _source_identity(source) -> dict:
    """Best-effort source/version stamp for the dataset identity (never raises)."""
    stamp = {"type": f"{type(source).__module__}.{type(source).__qualname__}"}
    for name in ("source", "source_version", "version", "dataset_id"):
        value = getattr(source, name, None)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            stamp[name] = value
    return stamp


def _record_hash(source, instruments: Sequence[str], event_times: Sequence[int]) -> str:
    """Canonical record hash over the raw window the identity is built from ("" when none)."""
    times = tuple(event_times)
    if not times:
        return ""
    lower, upper = min(times), max(times)
    bars = [bar for instrument in instruments for bar in source.fetch_bars(instrument, lower, upper)]
    return content_hash_of(bars) if bars else ""


def dataset_identity(
    source,
    instruments: Sequence[str],
    event_times: Sequence[int],
    *,
    decision_lag: int = 0,
    minimum_knowledge_lag: int = 0,
) -> str:
    """Bound, reproducible dataset identity of what a run consumed.

    Binds the exact instrument set, the decision event grid, the knowledge-time PIT policy
    (with its decision / minimum-knowledge lags), the source/version stamp and the canonical
    record hash via the shared canonical hasher (``quantos.contracts.hashing.stable_hash``),
    so any change in any of them changes the identity and identical inputs reproduce it.
    Returns "" when the store holds no records for the grid (nothing consumed -> no identity,
    the same fail-closed empty value the content fingerprint returns)."""
    instrument_set = sorted({str(inst) for inst in instruments})
    # the record hash is taken over the CANONICAL instrument set: reordering or repeating a
    # name must not change the identity (the content hash itself is order-independent)
    records = _record_hash(source, instrument_set, event_times)
    if not records:
        return ""
    return stable_hash({
        "instrument_set": instrument_set,
        "event_grid": sorted({int(t) for t in event_times}),
        "pit_policy": {"id": PIT_POLICY_ID,
                       "decision_lag": int(decision_lag),
                       "minimum_knowledge_lag": int(minimum_knowledge_lag)},
        "source": _source_identity(source),
        "record_hash": records,
    })


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
        """Content fingerprint of the raw records in the event window (unchanged contract)."""
        return _record_hash(self._source, instruments, event_times)

    def dataset_identity(
        self,
        instruments: Sequence[str],
        event_times: Sequence[int],
        *,
        decision_lag: int = 0,
        minimum_knowledge_lag: int = 0,
    ) -> str:
        """Bound dataset identity over this adapter's source (see :func:`dataset_identity`)."""
        return dataset_identity(self._source, instruments, event_times,
                                decision_lag=decision_lag,
                                minimum_knowledge_lag=minimum_knowledge_lag)


def adapt_research_market_data(source):
    """Keep an existing versioned port or adapt a raw PIT store once."""
    required = ("as_of", "latest_as_of", "dataset_version")
    if all(callable(getattr(source, name, None)) for name in required):
        return source
    return PITResearchDataAdapter(source)
