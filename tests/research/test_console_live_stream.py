"""Contract tests for the read-only dry-run server event stream."""

from __future__ import annotations

import json

from services.console.api.live_stream_views import (
    _dry_run_watch_token,
    _encode_snapshot_event,
    _snapshot_fingerprint,
)
from services.console.main import app


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
    routes = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/console/stream/dry-run"
    ]
    assert len(routes) == 1
    assert routes[0].methods == {"GET"}
