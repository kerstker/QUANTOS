"""Contract tests for the read-only dry-run server event stream."""

from __future__ import annotations

import json
import time

import pytest

from services.console.api.live_stream_views import (
    _dry_run_watch_token,
    _encode_snapshot_event,
    _mft_stream_notice,
    _snapshot_fingerprint,
    stream_mft_execution,
)
from services.console.main import app
from services.monitoring.mft_read_model import (
    MFT_READ_MODEL_SECTIONS,
    MftFreshnessPolicy,
    MftReadModelStore,
)


def test_snapshot_event_is_browser_message_and_stable():
    payload = {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "safety": {"allow_order_submission": False, "live_allowed": False},
    }
    fingerprint = _snapshot_fingerprint(payload)
    assert fingerprint == _snapshot_fingerprint(dict(reversed(list(payload.items()))))

    encoded = _encode_snapshot_event(payload, fingerprint)

    lines = encoded.strip().splitlines()
    assert lines[0] == f"id: {fingerprint}"
    assert lines[1].startswith("data: ")
    assert not any(line.startswith("event:") for line in lines)
    decoded = json.loads(lines[1].removeprefix("data: "))
    assert decoded == payload
    assert decoded["safety"]["allow_order_submission"] is False


def test_watch_token_changes_without_reading_snapshot_contents(tmp_path):
    watched = tmp_path / "snapshot.json"
    before = _dry_run_watch_token((str(watched),))
    watched.write_text("{}", encoding="utf-8")
    after = _dry_run_watch_token((str(watched),))

    assert before != after
    assert before[0][1] is False
    assert after[0][1] is True


def test_dry_run_event_stream_is_registered_get_only():
    path_item = app.openapi()["paths"]["/api/v1/console/stream/dry-run"]

    assert set(path_item) == {"get"}


def test_mft_stream_notice_is_metadata_only_and_never_an_execution_surface():
    snapshot = {
        "status": "AVAILABLE",
        "snapshot_status": "REAL",
        "as_of_ms": 4_000,
        "cursor": "1:" + "a" * 64,
        "watermark": {"sequence": 1, "event_at_ms": 3_000, "event_hash": "a" * 64},
        "freshness": {"status": "CURRENT", "age_ms": 1_000},
        "oms": {"orders": ["must-not-leak-into-notice"]},
    }
    current = _mft_stream_notice(snapshot, client_cursor=snapshot["cursor"])
    changed = _mft_stream_notice(snapshot, client_cursor="0:" + "0" * 64)

    assert current["status"] == "CURRENT"
    assert changed["status"] == "CHANGED"
    assert current["read_only"] is True
    assert "oms" not in current and "safety" not in current
    assert "submit" not in json.dumps(current).lower()


def test_mft_event_stream_is_registered_get_only():
    path_item = app.openapi()["paths"]["/api/v1/console/stream/mft-execution"]

    assert set(path_item) == {"get"}


@pytest.mark.asyncio
async def test_mft_stream_emits_only_notice_and_prefers_last_event_id(tmp_path, monkeypatch):
    path = tmp_path / "mft.db"
    now_ms = time.time_ns() // 1_000_000
    with MftReadModelStore(str(path), create=True) as store:
        for offset, section in enumerate(MFT_READ_MODEL_SECTIONS):
            store.append_section(
                section,
                {"status": "READY"},
                event_at_ms=now_ms - 100 + offset,
                event_id=f"notice-{offset}",
            )
        snapshot = store.snapshot(
            as_of_ms=now_ms,
            freshness_policy=MftFreshnessPolicy("stream-test", "1", 1_000),
        )
    state = tmp_path / "state"
    resolved = state / "data" / "cache" / "mft_read_model.db"
    resolved.parent.mkdir(parents=True)
    path.replace(resolved)
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(state))

    class Request:
        headers = {"last-event-id": "0:" + "0" * 64}

        async def is_disconnected(self):
            return False

    response = await stream_mft_execution(
        Request(), cursor=snapshot["cursor"]
    )
    chunk = await anext(response.body_iterator)
    await response.body_iterator.aclose()
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    data_line = next(line for line in text.splitlines() if line.startswith("data: "))
    notice = json.loads(data_line.removeprefix("data: "))

    assert notice["status"] == "CHANGED"
    assert notice["cursor"] == snapshot["cursor"]
    assert not any(section in notice for section in MFT_READ_MODEL_SECTIONS)
