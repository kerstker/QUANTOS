"""console_api's `/architecture` and `/system-completeness` endpoints build a payload that calls
`_repo_inventory()` — a synchronous full-repo filesystem walk (every .py file under services/ +
tests/, line-counted). 2026-07-06 security review punch-list: this ran inline inside `async def`
handlers, blocking the whole event loop for the walk's duration on every request (measured
~0.11s — not severe, but any concurrent request during that window would stall). Fix: offload the
payload build via `asyncio.to_thread` so the event loop stays free while the walk runs on a
worker thread. Response shape must stay byte-identical (guarded separately by
test_console_api_contract.py); this test only asserts the offload wiring itself.
"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from services.console.main import app

client = TestClient(app)


def test_architecture_endpoint_offloads_payload_build_to_a_thread():
    with patch("services.console.api.architecture_views.asyncio.to_thread",
               new=AsyncMock(return_value={"inventory": {}})) as mocked:
        resp = client.get("/api/v1/console/architecture")
    assert resp.status_code == 200
    assert mocked.await_count == 1


def test_system_completeness_endpoint_offloads_payload_build_to_a_thread():
    with patch("services.console.api.architecture_views.asyncio.to_thread",
               new=AsyncMock(return_value={"inventory": {}})) as mocked:
        resp = client.get("/api/v1/console/system-completeness")
    assert resp.status_code == 200
    assert mocked.await_count == 1
