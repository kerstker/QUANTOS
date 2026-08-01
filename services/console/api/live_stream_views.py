"""Read-only server-sent events for dynamic operator console snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from services.console.api.artifact_paper_views import (
    _dry_run_payload,
    _dry_run_watch_paths,
)
from services.console.api.common import with_snapshot_metadata


router = APIRouter()


def _canonical_snapshot_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _snapshot_fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_snapshot_json(payload).encode("utf-8")).hexdigest()


def _encode_snapshot_event(payload: Mapping[str, Any], event_id: str) -> str:
    """Encode a default SSE message so browser ``onmessage`` receives it."""
    return f"id: {event_id}\ndata: {_canonical_snapshot_json(payload)}\n\n"


def _dry_run_watch_token(paths: Iterable[str] | None = None) -> tuple[tuple[Any, ...], ...]:
    """Return a cheap durable-change token, including SQLite WAL sidecars."""
    expanded: list[str] = []
    for path in paths or _dry_run_watch_paths():
        expanded.append(path)
        if path.endswith(".db"):
            expanded.extend((f"{path}-wal", f"{path}-shm"))
    token: list[tuple[Any, ...]] = []
    for path in expanded:
        try:
            stat = os.stat(path)
        except OSError:
            token.append((path, False, None, None))
        else:
            token.append((path, True, stat.st_mtime_ns, stat.st_size))
    return tuple(token)


def _unavailable_snapshot(exc: Exception) -> dict[str, Any]:
    return with_snapshot_metadata(
        {
            "source": "BACKEND_COMPUTED_SNAPSHOT",
            "read_only": True,
            "api_version": "0.1.0",
            "data_provenance": "BACKEND_UNAVAILABLE",
            "snapshot_present": False,
            "verdict": "UNAVAILABLE",
            "n_runs": 0,
            "latest": {},
            "runs": [],
            "safety_note": "L2_DRY_RUN_ONLY; event snapshot construction failed closed.",
            "stream_error": type(exc).__name__,
        }
    )


@router.get("/api/v1/console/stream/dry-run")
async def stream_dry_run(request: Request) -> StreamingResponse:
    """Push a fresh dry-run snapshot only when its retained evidence changes."""

    async def events():
        last_token: tuple[tuple[Any, ...], ...] | None = None
        next_heartbeat = time.monotonic() + 15.0
        while True:
            if await request.is_disconnected():
                break
            token = await asyncio.to_thread(_dry_run_watch_token)
            if token != last_token:
                try:
                    payload = await asyncio.to_thread(_dry_run_payload)
                    payload = with_snapshot_metadata(payload)
                except Exception as exc:  # fail closed at the presentation boundary
                    payload = _unavailable_snapshot(exc)
                fingerprint = _snapshot_fingerprint(payload)
                yield _encode_snapshot_event(payload, fingerprint)
                last_token = token
                next_heartbeat = time.monotonic() + 15.0
            elif time.monotonic() >= next_heartbeat:
                yield f": heartbeat {int(time.time() * 1000)}\n\n"
                next_heartbeat = time.monotonic() + 15.0
            await asyncio.sleep(1.0)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
