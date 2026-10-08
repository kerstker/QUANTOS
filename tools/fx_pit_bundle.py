"""Build a research FieldPanel from the retained non-crypto FX PIT bundle.

The adapter is offline and read-only.  It deliberately does not fall back to
``data/cache/fx.db``: a clean checkout must replay the same versioned bytes.
"""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path
from typing import Iterable, Mapping

from services.governance.model_risk_dataset import DatasetBundleError, verify_pit_bundle
from services.research.alpha_expression import FieldPanel


REPO = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = REPO / "evidence/datasets/fx_pit_tradable_v1.jsonl.gz"
DEFAULT_MANIFEST = REPO / "evidence/datasets/fx_pit_tradable_v1.manifest.json"


class FXPITBundleError(ValueError):
    """The retained FX source cannot safely become a research panel."""


def _validate_payload(row: Mapping[str, object]) -> tuple[str, int, int, float]:
    key = row.get("key")
    event_time = row.get("event_time")
    knowledge_time = row.get("knowledge_time")
    payload_json = row.get("payload_json")
    if (
        not isinstance(key, str)
        or type(event_time) is not int
        or type(knowledge_time) is not int
        or not isinstance(payload_json, str)
    ):
        raise FXPITBundleError("FX PIT row shape is invalid")
    if knowledge_time > event_time:
        raise FXPITBundleError("future-known FX observation is forbidden")
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise FXPITBundleError("FX payload_json is invalid") from exc
    if not isinstance(payload, dict):
        raise FXPITBundleError("FX payload must be an object")
    if (
        payload.get("instrument_id") != key
        or payload.get("event_time") != event_time
        or payload.get("knowledge_time") != knowledge_time
    ):
        raise FXPITBundleError("FX payload identity does not match its PIT envelope")
    close = payload.get("close")
    if (
        isinstance(close, bool)
        or not isinstance(close, (int, float))
        or not math.isfinite(float(close))
        or float(close) <= 0.0
    ):
        raise FXPITBundleError("FX close must be finite and positive")
    return key, event_time, knowledge_time, float(close)


def build_fx_pit_panel_from_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    expected_keys: Iterable[str],
) -> FieldPanel:
    """Convert validated PIT envelopes into a sparse, never-forward-filled panel."""

    instruments = tuple(sorted(set(expected_keys)))
    if not instruments:
        raise FXPITBundleError("expected FX universe must be non-empty")
    allowed = set(instruments)
    cells: dict[tuple[str, int], tuple[int, float]] = {}
    observed: set[str] = set()
    event_times: set[int] = set()
    for row in rows:
        key, event_time, knowledge_time, close = _validate_payload(row)
        if key not in allowed:
            raise FXPITBundleError(f"unexpected FX instrument: {key}")
        cell = (key, event_time)
        previous = cells.get(cell)
        if previous is not None and knowledge_time <= previous[0]:
            raise FXPITBundleError("FX PIT revisions must have increasing knowledge_time")
        cells[cell] = (knowledge_time, close)
        observed.add(key)
        event_times.add(event_time)
    if observed != allowed:
        missing = ", ".join(sorted(allowed - observed))
        raise FXPITBundleError(f"FX PIT bundle is missing expected instruments: {missing}")

    ordered_times = tuple(sorted(event_times))
    close_matrix = [[cells.get((key, event_time), (0, None))[1] for key in instruments] for event_time in ordered_times]
    return FieldPanel(instruments, ordered_times, {"close": close_matrix})


def load_fx_pit_panel(
    bundle_path: str | Path = DEFAULT_BUNDLE,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    *,
    repo_root: str | Path = REPO,
) -> tuple[FieldPanel, dict]:
    """Verify exact bundle bytes, then build the clean-checkout FX panel."""

    root = Path(repo_root).resolve()
    try:
        manifest = verify_pit_bundle(
            bundle_path,
            manifest_path,
            repo_root=root,
        )
        bundle = Path(bundle_path)
        if not bundle.is_absolute():
            bundle = root / bundle
        with gzip.open(bundle, "rt", encoding="utf-8", newline="") as handle:
            rows = (json.loads(line) for line in handle)
            panel = build_fx_pit_panel_from_rows(rows, expected_keys=manifest["keys"])
    except (DatasetBundleError, OSError, EOFError, json.JSONDecodeError) as exc:
        raise FXPITBundleError(f"retained FX PIT bundle cannot be loaded: {exc}") from exc
    if panel.event_times[0] != manifest["min_event_time"]:
        raise FXPITBundleError("FX panel minimum event time does not match manifest")
    if panel.event_times[-1] != manifest["max_event_time"]:
        raise FXPITBundleError("FX panel maximum event time does not match manifest")
    return panel, manifest


__all__ = [
    "DEFAULT_BUNDLE",
    "DEFAULT_MANIFEST",
    "FXPITBundleError",
    "build_fx_pit_panel_from_rows",
    "load_fx_pit_panel",
]
