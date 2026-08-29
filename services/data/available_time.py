"""Explicit event/decision-time reads for versioned market bars.

``fetch_bars_pit`` is intentionally an own-event-time convenience view: it
keeps only rows whose ``knowledge_time <= event_time``.  That contract is not
valid for a bar whose event key is its open while OHLCV becomes available at
the close.  This module provides the bulk equivalent for consumers that make
their decision after a declared lag and need to reject legacy, backdated
vintages in a mixed cache.
"""
from __future__ import annotations

import json


def _canonical_bar_payload(bar) -> str:
    dump = getattr(bar, "model_dump", None)
    payload = dump(mode="json") if callable(dump) else vars(bar)
    try:
        return json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("bar payload is not canonical JSON") from exc


def fetch_bars_for_event_decisions(
    store,
    instrument_id: str,
    start: int,
    end: int,
    *,
    decision_lag: int,
    minimum_knowledge_lag: int,
) -> list:
    """Return one admissible vintage per event, ordered by event time.

    For an event at ``t``, the decision clock is ``t + decision_lag``.  A row
    is admissible only when both of these are true:

    * ``knowledge_time <= t + decision_lag`` (known by the decision); and
    * ``knowledge_time >= t + minimum_knowledge_lag`` (not a legacy backdate).

    When several admissible vintages coexist, the latest one knowable at the
    decision is selected.  The source store is read through its raw versioned
    window deliberately; applying ``fetch_bars_pit`` first would discard valid
    close-time rows before this policy can evaluate them.
    """
    if not isinstance(instrument_id, str) or not instrument_id:
        raise ValueError("instrument_id must be a non-empty string")
    for name, value in (
        ("start", start),
        ("end", end),
        ("decision_lag", decision_lag),
        ("minimum_knowledge_lag", minimum_knowledge_lag),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
    if end < start:
        raise ValueError("end cannot precede start")
    if decision_lag < 0 or minimum_knowledge_lag < 0:
        raise ValueError("knowledge lags must be non-negative")
    if decision_lag < minimum_knowledge_lag:
        raise ValueError("decision_lag cannot be shorter than minimum_knowledge_lag")

    selected = {}
    temporal_payloads: dict[tuple[str, int, int], str] = {}
    for bar in store.fetch_bars(instrument_id, start, end):
        try:
            bar_instrument = bar.instrument_id
            event_time = bar.event_time
            knowledge_time = bar.knowledge_time
        except AttributeError as exc:
            raise ValueError("bar is missing required temporal identity fields") from exc
        if bar_instrument != instrument_id:
            raise ValueError(
                "bar instrument identity does not match the requested instrument"
            )
        for name, value in (
            ("bar.event_time", event_time),
            ("bar.knowledge_time", knowledge_time),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not start <= event_time <= end:
            raise ValueError("store returned a bar outside the requested event window")
        if knowledge_time < event_time:
            raise ValueError("bar knowledge_time cannot precede event_time")
        temporal_identity = (bar_instrument, event_time, knowledge_time)
        payload = _canonical_bar_payload(bar)
        prior_payload = temporal_payloads.get(temporal_identity)
        if prior_payload is not None and prior_payload != payload:
            raise ValueError(
                "bar temporal identity conflict: equal instrument/event_time/"
                "knowledge_time has different content"
            )
        temporal_payloads[temporal_identity] = payload
        if not (
            event_time + minimum_knowledge_lag
            <= knowledge_time
            <= event_time + decision_lag
        ):
            continue
        prior = selected.get(event_time)
        if prior is None or knowledge_time > prior.knowledge_time:
            selected[event_time] = bar
    return [selected[event_time] for event_time in sorted(selected)]
