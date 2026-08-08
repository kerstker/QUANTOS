"""Read-only server-sent events for dynamic operator console snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from services.console.api.artifact_paper_views import (
    _dry_run_payload,
    _dry_run_watch_paths,
)
from services.console.api.common import with_snapshot_metadata
from services.console.api.mft_execution_views import build_mft_execution_snapshot


router = APIRouter()
_MFT_STREAM_SCHEMA = "quantos.mft-stream-notice.v1"
_MFT_DEFAULT_PATH = "data/cache/mft_read_model.db"


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


def _mft_stream_notice(
    snapshot: Mapping[str, Any], *, client_cursor: str | None
) -> dict[str, Any]:
    available = snapshot.get("status") == "AVAILABLE"
    cursor = snapshot.get("cursor") if isinstance(snapshot.get("cursor"), str) else None
    watermark = snapshot.get("watermark")
    freshness = snapshot.get("freshness")
    if not isinstance(watermark, Mapping):
        watermark = None
    if not isinstance(freshness, Mapping):
        freshness = {"status": "UNAVAILABLE", "age_ms": None}
    status = "UNAVAILABLE"
    if available:
        status = "CURRENT" if cursor == client_cursor else "CHANGED"
    reason = snapshot.get("reason")
    return {
        "source": "MFT_READ_MODEL_SQLITE_AUTHORITY",
        "read_only": True,
        "schema_version": _MFT_STREAM_SCHEMA,
        "status": status,
        "as_of_ms": int(snapshot.get("as_of_ms", 0)),
        "cursor": cursor,
        "watermark": dict(watermark) if watermark is not None else None,
        "freshness": {
            "status": str(freshness.get("status", "UNAVAILABLE")),
            "age_ms": freshness.get("age_ms"),
        },
        "reason": str(reason) if reason is not None else None,
    }


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


@router.get("/api/v1/console/stream/mft-execution")
async def stream_mft_execution(
    request: Request,
    cursor: str | None = Query(default=None, min_length=66, max_length=96),
) -> StreamingResponse:
    """Notify browsers that the verified MFT watermark changed.

    The notice deliberately carries no section state.  Browsers must fetch the
    Task 37 REST delta, which remains the sole catch-up authority.
    """
    initial_cursor = request.headers.get("last-event-id") or cursor
    path = Path(os.environ.get("QUANTOS_MFT_READ_MODEL_DB", _MFT_DEFAULT_PATH))

    async def events():
        last_token: tuple[tuple[Any, ...], ...] | None = None
        client_cursor = initial_cursor
        next_heartbeat = time.monotonic() + 15.0
        while True:
            if await request.is_disconnected():
                break
            token = await asyncio.to_thread(_dry_run_watch_token, (str(path),))
            if token != last_token or time.monotonic() >= next_heartbeat:
                as_of_ms = time.time_ns() // 1_000_000
                snapshot = await asyncio.to_thread(
                    build_mft_execution_snapshot, path=path, as_of_ms=as_of_ms
                )
                notice = _mft_stream_notice(snapshot, client_cursor=client_cursor)
                fingerprint = _snapshot_fingerprint(notice)
                event_id = notice["cursor"] or f"unavailable-{fingerprint[:16]}"
                yield _encode_snapshot_event(notice, str(event_id))
                if notice["cursor"] is not None:
                    client_cursor = str(notice["cursor"])
                last_token = token
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
