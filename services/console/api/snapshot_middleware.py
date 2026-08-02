"""Read-only Console response provenance middleware.

The HTTP adapter is kept outside the composition root so the root remains a
small router assembly module.  The middleware only enriches successful JSON
GET snapshots; it never changes error responses or grants an action.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from services.console.api.common import with_snapshot_metadata


async def attach_console_snapshot_metadata(request: Request, call_next):
    """Attach common provenance fields to read-only Console JSON responses."""
    response = await call_next(request)
    if request.method != "GET" or not request.url.path.startswith("/api/v1/console/"):
        return response
    if response.status_code != 200 or "application/json" not in response.headers.get(
        "content-type", ""
    ):
        return response

    body = b"".join([chunk async for chunk in response.body_iterator])
    headers = dict(response.headers)
    headers.pop("content-length", None)
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            background=response.background,
        )
    if not isinstance(payload, dict):
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            background=response.background,
        )
    return JSONResponse(
        content=with_snapshot_metadata(payload),
        status_code=response.status_code,
        headers=headers,
        background=response.background,
    )


__all__ = ["attach_console_snapshot_metadata"]
