import json
from pathlib import Path

import pytest

from tools.fx_pit_bundle import (
    FXPITBundleError,
    build_fx_pit_panel_from_rows,
    load_fx_pit_panel,
)


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "evidence/datasets/fx_pit_tradable_v1.jsonl.gz"
MANIFEST = ROOT / "evidence/datasets/fx_pit_tradable_v1.manifest.json"


def _row(key: str, event_time: int, close: float, *, knowledge_time: int | None = None) -> dict:
    known = event_time if knowledge_time is None else knowledge_time
    payload = {
        "close": close,
        "event_time": event_time,
        "high": close,
        "instrument_id": key,
        "knowledge_time": known,
        "low": close,
        "open": close,
        "volume": 0.0,
    }
    return {
        "event_time": event_time,
        "key": key,
        "knowledge_time": known,
        "payload_json": json.dumps(payload, sort_keys=True),
    }


def test_build_fx_panel_preserves_gaps_without_forward_fill():
    rows = [
        _row("A", 1000, 1.0),
        _row("A", 2000, 1.1),
        _row("B", 1000, 2.0),
    ]

    panel = build_fx_pit_panel_from_rows(rows, expected_keys=("A", "B"))

    assert panel.instruments == ("A", "B")
    assert panel.event_times == (1000, 2000)
    assert panel.matrices["close"] == [[1.0, 2.0], [1.1, None]]


def test_build_fx_panel_rejects_future_known_observation():
    rows = [_row("A", 1000, 1.0, knowledge_time=1001)]

    with pytest.raises(FXPITBundleError, match="future-known"):
        build_fx_pit_panel_from_rows(rows, expected_keys=("A",))


def test_build_fx_panel_rejects_payload_identity_mismatch():
    row = _row("A", 1000, 1.0)
    payload = json.loads(row["payload_json"])
    payload["instrument_id"] = "B"
    row["payload_json"] = json.dumps(payload)

    with pytest.raises(FXPITBundleError, match="payload identity"):
        build_fx_pit_panel_from_rows([row], expected_keys=("A",))


def test_versioned_fx_bundle_builds_without_local_source_database():
    panel, manifest = load_fx_pit_panel(
        BUNDLE,
        MANIFEST,
        repo_root=ROOT,
    )

    assert len(panel.instruments) == 18
    assert len(panel.event_times) > 6_000
    assert manifest["row_count"] == 126_113
    assert panel.event_times[0] == manifest["min_event_time"]
    assert panel.event_times[-1] == manifest["max_event_time"]
