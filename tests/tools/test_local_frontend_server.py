from __future__ import annotations

from http.client import HTTPMessage
from io import BytesIO
import json

import pytest

from tools.local_frontend_server import (
    FRONTEND_DIST_ENV,
    REQUIRED_LOCAL_API_MARKERS,
    SpaRequestHandler,
    _MAX_PROXY_BODY_BYTES,
    _ProxyRequestError,
    _cache_control_for_path,
    _client_is_allowed,
    _client_request_is_allowed,
    _frontend_dist_root,
    _has_real_local_api_contract,
    _is_event_stream_response,
    _spa_target,
    _upstream_timeout_for_path,
    _validated_proxy_body_length,
    _validated_listener,
)


_RELEASE_COMMIT = "a" * 40


def _write_runtime_contract(root, *, mock_mode=False, release_commit=_RELEASE_COMMIT):
    (root / "quantos-runtime-contract.json").write_text(
        json.dumps(
            {
                "schema": "quantos.frontend_runtime.v1",
                "release_commit": release_commit,
                "api_url": "__QUANTOS_SAME_ORIGIN__",
                "runner_url": "__QUANTOS_SAME_ORIGIN__",
                "mock_mode": mock_mode,
            }
        ),
        encoding="utf-8",
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


def test_lan_clients_can_reach_only_the_token_guarded_runner_surface():
    assert _client_request_is_allowed("GET", "/workbench/market", "192.168.5.5") is True
    assert _client_request_is_allowed("GET", "/api/v1/console/dry-run", "192.168.5.5") is True
    assert _client_request_is_allowed("HEAD", "/assets/app.js", "192.168.5.5") is True
    assert _client_request_is_allowed("GET", "/__runner__/health", "192.168.5.5") is True
    assert _client_request_is_allowed("POST", "/__runner__/run", "192.168.5.5") is True
    assert _client_request_is_allowed("POST", "/__runner__/cancel", "192.168.5.5") is True
    assert _client_request_is_allowed("GET", "/__runner__/run", "192.168.5.5") is False
    assert _client_request_is_allowed("POST", "/__runner__/health", "192.168.5.5") is False
    assert _client_request_is_allowed("GET", "/__runner__/unknown", "192.168.5.5") is False
    assert _client_request_is_allowed("POST", "/api/v1/console/research", "192.168.5.5") is False


def test_loopback_keeps_only_the_existing_runner_contract():
    assert _client_request_is_allowed("GET", "/__runner__/health", "127.0.0.1") is True
    assert _client_request_is_allowed("POST", "/__runner__/run", "127.0.0.1") is True
    assert _client_request_is_allowed("POST", "/__runner__/runs", "127.0.0.1") is False
    assert _client_request_is_allowed("POST", "/api/v1/console/system-health", "127.0.0.1") is False


def _headers(*rows: tuple[str, str]) -> HTTPMessage:
    headers = HTTPMessage()
    for name, value in rows:
        headers[name] = value
    return headers


def test_proxy_body_contract_accepts_only_small_authenticated_json_mutations():
    body = b'{"kind":"ashare_technical_search","params":{}}'
    headers = _headers(
        ("Content-Length", str(len(body))),
        ("Content-Type", "application/json; charset=utf-8"),
        ("X-Runner-Token", "test-token"),
        ("X-Operator", "test-operator"),
    )
    assert _validated_proxy_body_length(
        "POST", "/__runner__/run", headers
    ) == len(body)
    assert _validated_proxy_body_length(
        "GET", "/api/v1/console/system-health", _headers()
    ) == 0


@pytest.mark.parametrize(
    ("headers", "status_code"),
    [
        (
            _headers(
                ("Content-Type", "application/json"),
                ("X-Runner-Token", "token"),
                ("X-Operator", "operator"),
            ),
            411,
        ),
        (
            _headers(
                ("Content-Length", "-1"),
                ("Content-Type", "application/json"),
                ("X-Runner-Token", "token"),
                ("X-Operator", "operator"),
            ),
            400,
        ),
        (
            _headers(
                ("Content-Length", str(_MAX_PROXY_BODY_BYTES + 1)),
                ("Content-Type", "application/json"),
                ("X-Runner-Token", "token"),
                ("X-Operator", "operator"),
            ),
            413,
        ),
        (
            _headers(
                ("Content-Length", "2"),
                ("Content-Type", "text/plain"),
                ("X-Runner-Token", "token"),
                ("X-Operator", "operator"),
            ),
            415,
        ),
        (
            _headers(
                ("Content-Length", "2"),
                ("Content-Type", "application/json"),
                ("X-Operator", "operator"),
            ),
            401,
        ),
        (
            _headers(
                ("Content-Length", "2"),
                ("Content-Type", "application/json"),
                ("X-Runner-Token", "token"),
            ),
            400,
        ),
        (
            _headers(
                ("Content-Length", "2"),
                ("Content-Type", "application/json"),
                ("Transfer-Encoding", "chunked"),
                ("X-Runner-Token", "token"),
                ("X-Operator", "operator"),
            ),
            400,
        ),
    ],
)
def test_proxy_body_contract_rejects_invalid_envelopes_before_read(
    headers,
    status_code,
):
    with pytest.raises(_ProxyRequestError) as raised:
        _validated_proxy_body_length("POST", "/__runner__/run", headers)
    assert raised.value.status_code == status_code


def test_proxy_body_contract_rejects_duplicate_lengths_and_unknown_surfaces():
    duplicate = _headers(
        ("Content-Length", "2"),
        ("Content-Length", "2"),
        ("Content-Type", "application/json"),
        ("X-Runner-Token", "token"),
        ("X-Operator", "operator"),
    )
    with pytest.raises(_ProxyRequestError) as duplicate_error:
        _validated_proxy_body_length("POST", "/__runner__/run", duplicate)
    assert duplicate_error.value.status_code == 400

    with pytest.raises(_ProxyRequestError) as method_error:
        _validated_proxy_body_length(
            "POST", "/api/v1/console/system-health", _headers()
        )
    assert method_error.value.status_code == 405

    with pytest.raises(_ProxyRequestError) as path_error:
        _validated_proxy_body_length("POST", "/__runner__/unknown", _headers())
    assert path_error.value.status_code == 405


def test_proxy_rejects_missing_length_without_reading_request_body():
    class _NeverRead:
        def read(self, _size):
            raise AssertionError("request body was read before validation")

    handler = object.__new__(SpaRequestHandler)
    handler.command = "POST"
    handler.path = "/__runner__/run"
    handler.headers = _headers(
        ("Content-Type", "application/json"),
        ("X-Runner-Token", "token"),
        ("X-Operator", "operator"),
    )
    handler.rfile = _NeverRead()
    errors: list[tuple[int, str]] = []
    handler.send_error = lambda code, message="": errors.append((code, message))

    handler._proxy()

    assert errors and errors[0][0] == 411


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
    _write_runtime_contract(tmp_path)
    entry.write_bytes(b"frontend mock")
    assert _has_real_local_api_contract(
        dist=tmp_path, release_commit=_RELEASE_COMMIT
    ) is False

    entry.write_bytes(REQUIRED_LOCAL_API_MARKERS[0])
    assert _has_real_local_api_contract(
        dist=tmp_path, release_commit=_RELEASE_COMMIT
    ) is True


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
    _write_runtime_contract(tmp_path)

    assert _has_real_local_api_contract(
        dist=tmp_path, release_commit=_RELEASE_COMMIT
    ) is True


def test_real_local_api_contract_rejects_unrelated_url_literal(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (assets / "app.js").write_bytes(b"frontend mock")
    (assets / "help-page.js").write_bytes(REQUIRED_LOCAL_API_MARKERS[0])
    _write_runtime_contract(tmp_path)

    assert _has_real_local_api_contract(
        dist=tmp_path, release_commit=_RELEASE_COMMIT
    ) is False


def test_real_local_api_contract_rejects_mock_or_wrong_release_manifest(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (assets / "app.js").write_bytes(REQUIRED_LOCAL_API_MARKERS[0])
    _write_runtime_contract(tmp_path, mock_mode=True)
    assert _has_real_local_api_contract(
        dist=tmp_path, release_commit=_RELEASE_COMMIT
    ) is False
    _write_runtime_contract(tmp_path, release_commit="b" * 40)
    assert _has_real_local_api_contract(
        dist=tmp_path, release_commit=_RELEASE_COMMIT
    ) is False


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


def test_proxy_gives_only_known_heavy_read_only_snapshots_a_wider_timeout():
    assert _upstream_timeout_for_path("/api/v1/console/alpha-zoo") == 60
    assert _upstream_timeout_for_path("/api/v1/console/system-completeness?refresh=1") == 60
    assert _upstream_timeout_for_path("/api/v1/console/runtime-contract") == 15
    assert _upstream_timeout_for_path("/__runner__/runs") == 15


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
