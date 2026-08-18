from __future__ import annotations

from io import BytesIO

import pytest

from tools.local_frontend_server import (
    FRONTEND_DIST_ENV,
    REQUIRED_LOCAL_API_MARKERS,
    SpaRequestHandler,
    _cache_control_for_path,
    _client_is_allowed,
    _client_request_is_allowed,
    _frontend_dist_root,
    _has_real_local_api_contract,
    _is_event_stream_response,
    _spa_target,
    _validated_listener,
)


def test_frontend_dist_root_defaults_and_requires_absolute_override(monkeypatch, tmp_path):
    root = tmp_path / "release"
    monkeypatch.delenv(FRONTEND_DIST_ENV, raising=False)
    assert _frontend_dist_root(root=root) == root / "apps" / "research-console" / "dist"

    artifact = tmp_path / "state" / "frontend" / "dist"
    monkeypatch.setenv(FRONTEND_DIST_ENV, str(artifact))
    assert _frontend_dist_root(root=root) == artifact.resolve()

    monkeypatch.setenv(FRONTEND_DIST_ENV, "relative/dist")
    with pytest.raises(RuntimeError, match="absolute path"):
        _frontend_dist_root(root=root)


def test_listener_defaults_to_loopback_and_rejects_unreviewed_lan_binding():
    host, network = _validated_listener("127.0.0.1", None)
    assert host == "127.0.0.1"
    assert network is None

    with pytest.raises(ValueError, match="allow-network"):
        _validated_listener("192.168.5.68", None)
    with pytest.raises(ValueError, match="specific interface"):
        _validated_listener("0.0.0.0", "192.168.5.0/24")
    with pytest.raises(ValueError, match="private"):
        _validated_listener("8.8.8.8", "8.8.8.0/24")


def test_listener_accepts_only_a_canonical_narrow_private_network():
    host, network = _validated_listener("192.168.5.68", "192.168.5.0/24")
    assert host == "192.168.5.68"
    assert str(network) == "192.168.5.0/24"

    with pytest.raises(ValueError, match="canonical"):
        _validated_listener("192.168.5.68", "192.168.5.68/24")
    with pytest.raises(ValueError, match="narrower"):
        _validated_listener("192.168.5.68", "192.168.0.0/16")
    with pytest.raises(ValueError, match="inside"):
        _validated_listener("192.168.6.68", "192.168.5.0/24")


def test_client_allowlist_keeps_loopback_and_rejects_other_subnets():
    _, network = _validated_listener("192.168.5.68", "192.168.5.0/24")
    assert _client_is_allowed("127.0.0.1", network) is True
    assert _client_is_allowed("192.168.5.5", network) is True
    assert _client_is_allowed("192.168.6.5", network) is False
    assert _client_is_allowed("not-an-ip", network) is False


def test_lan_clients_are_monitoring_only_and_cannot_reach_runner_or_post():
    assert _client_request_is_allowed("GET", "/workbench/market", "192.168.5.5") is True
    assert _client_request_is_allowed("GET", "/api/v1/console/dry-run", "192.168.5.5") is True
    assert _client_request_is_allowed("HEAD", "/assets/app.js", "192.168.5.5") is True
    assert _client_request_is_allowed("GET", "/__runner__/health", "192.168.5.5") is False
    assert _client_request_is_allowed("POST", "/api/v1/console/research", "192.168.5.5") is False


def test_loopback_keeps_existing_runner_and_post_access():
    assert _client_request_is_allowed("GET", "/__runner__/health", "127.0.0.1") is True
    assert _client_request_is_allowed("POST", "/__runner__/runs", "127.0.0.1") is True


class _FiniteEventStream:
    status = 200

    def __init__(self) -> None:
        self._lines = iter((b"event: snapshot\n", b"data: {}\n", b"\n", b""))

    def getheader(self, name: str, default: str = "") -> str:
        if name.lower() == "content-type":
            return "text/event-stream; charset=utf-8"
        return default

    def getheaders(self) -> list[tuple[str, str]]:
        return [
            ("Content-Type", "text/event-stream; charset=utf-8"),
            ("Transfer-Encoding", "chunked"),
        ]

    def readline(self) -> bytes:
        return next(self._lines)


def test_spa_target_serves_assets_and_falls_back_for_routes(tmp_path):
    (tmp_path / "index.html").write_text("app", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("js", encoding="utf-8")

    assert _spa_target("/", dist=tmp_path) == "/"
    assert _spa_target("/assets/app.js", dist=tmp_path) == "/assets/app.js"
    assert _spa_target("/research/data-catalog", dist=tmp_path) == "/index.html"
    assert _spa_target("/missing.js", dist=tmp_path) == "/missing.js"


def test_spa_target_rejects_path_traversal(tmp_path):
    assert _spa_target("/../secret", dist=tmp_path) is None


def test_spa_shell_is_never_cached_but_hashed_assets_may_cache():
    assert _cache_control_for_path("/") == "no-store, max-age=0"
    assert _cache_control_for_path("/index.html") == "no-store, max-age=0"
    assert _cache_control_for_path("/index.html?build=current") == "no-store, max-age=0"
    assert _cache_control_for_path("/assets/index-content-hash.js") is None


def test_real_local_api_contract_rejects_mock_bundle(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    entry = assets / "app.js"
    entry.write_bytes(b"frontend mock")
    assert _has_real_local_api_contract(dist=tmp_path) is False

    entry.write_bytes(REQUIRED_LOCAL_API_MARKERS[0])
    assert _has_real_local_api_contract(dist=tmp_path) is True


def test_real_local_api_contract_accepts_standard_production_marker(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/index-built.js"></script>',
        encoding="utf-8",
    )
    (assets / "index-built.js").write_bytes(
        b"runtime=" + REQUIRED_LOCAL_API_MARKERS[0]
    )

    assert _has_real_local_api_contract(dist=tmp_path) is True


def test_real_local_api_contract_rejects_unrelated_url_literal(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (assets / "app.js").write_bytes(b"frontend mock")
    (assets / "help-page.js").write_bytes(REQUIRED_LOCAL_API_MARKERS[0])

    assert _has_real_local_api_contract(dist=tmp_path) is False


def test_proxy_targets_are_fixed_to_local_read_and_runner_surfaces():
    handler = object.__new__(SpaRequestHandler)

    handler.path = "/api/v1/console/dry-run?x=1"
    assert handler._proxy_target() == (
        "127.0.0.1",
        8400,
        "/api/v1/console/dry-run?x=1",
    )

    handler.path = "/__runner__/health"
    assert handler._proxy_target() == ("127.0.0.1", 8420, "/health")

    handler.path = "/research/dry-run"
    assert handler._proxy_target() is None


def test_event_stream_proxy_relays_without_buffering_or_content_length():
    response = _FiniteEventStream()
    assert _is_event_stream_response(response) is True
    handler = object.__new__(SpaRequestHandler)
    handler.command = "GET"
    handler.wfile = BytesIO()
    statuses: list[int] = []
    headers: list[tuple[str, str]] = []
    handler.send_response = statuses.append
    handler.send_header = lambda key, value: headers.append((key, value))
    handler.end_headers = lambda: None

    handler._relay_response(response)

    assert statuses == [200]
    assert handler.wfile.getvalue() == b"event: snapshot\ndata: {}\n\n"
    assert ("Content-Type", "text/event-stream; charset=utf-8") in headers
    assert ("Cache-Control", "no-cache") in headers
    assert all(key.lower() != "content-length" for key, _ in headers)
    assert all(key.lower() != "transfer-encoding" for key, _ in headers)
    assert handler.close_connection is True
