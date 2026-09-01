"""Serve the built QUANTOS research console without a Windows console host.

This is an L2 read-only operational surface.  It serves the already-built React
bundle and falls back to ``index.html`` for client-side routes.  Loopback is the
default and a LAN listener requires both a specific private interface address
and an explicit narrow private client network.  Building the bundle remains an
explicit IDE operation; the resident process requires no Node/npm/cmd shell.
"""
from __future__ import annotations

import argparse
import http.client
import http.server
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST_ENV = "QUANTOS_FRONTEND_DIST"
RELEASE_COMMIT_ENV = "QUANTOS_RELEASE_COMMIT"
FRONTEND_RUNTIME_CONTRACT = "quantos-runtime-contract.json"


def _frontend_dist_root(*, root: Path = ROOT) -> Path:
    configured = os.getenv(FRONTEND_DIST_ENV, "").strip()
    if not configured:
        return root / "apps" / "research-console" / "dist"
    candidate = Path(configured)
    if not candidate.is_absolute():
        raise RuntimeError(f"{FRONTEND_DIST_ENV} must be an absolute path")
    return candidate.resolve()


DIST = _frontend_dist_root()
REQUIRED_LOCAL_API_MARKERS = (
    b"__QUANTOS_SAME_ORIGIN__",
)
_ENTRY_SCRIPT_RE = re.compile(
    rb"""<script[^>]+type=["']module["'][^>]+src=["']/assets/([^"']+\.js)["']"""
)
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
AllowedNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
_DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 15
_HEAVY_SNAPSHOT_TIMEOUT_SECONDS = 60
_MAX_PROXY_BODY_BYTES = 64 * 1024
_PROXY_BODY_READ_TIMEOUT_SECONDS = 5
_HEAVY_SNAPSHOT_PATHS = frozenset(
    {
        "/api/v1/console/alpha-zoo",
        "/api/v1/console/system-completeness",
    }
)


class _ProxyRequestError(ValueError):
    """A proxy request that must be rejected before reading its body."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _upstream_timeout_for_path(path: str) -> int:
    """Allow bounded cold scans without weakening ordinary failure detection."""
    return (
        _HEAVY_SNAPSHOT_TIMEOUT_SECONDS
        if urlsplit(path).path in _HEAVY_SNAPSHOT_PATHS
        else _DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    )


def _validated_listener(
    host: str,
    allow_network: str | None,
) -> tuple[str, AllowedNetwork | None]:
    """Validate the exact bind address and optional LAN client allowlist."""
    normalized_host = "127.0.0.1" if host == "localhost" else host
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError as exc:
        raise ValueError("host must be a literal IP address") from exc
    if address.is_loopback:
        if allow_network is not None:
            raise ValueError("allow-network is invalid for a loopback listener")
        return str(address), None
    if address.is_unspecified:
        raise ValueError("LAN binding requires a specific interface address")
    if allow_network is None:
        raise ValueError("LAN binding requires --allow-network")
    try:
        network = ipaddress.ip_network(allow_network, strict=True)
    except ValueError as exc:
        raise ValueError("allow-network must be a canonical network") from exc
    if network.version != address.version or not network.is_private:
        raise ValueError("allow-network must be a private network matching the host")
    minimum_prefix = 24 if network.version == 4 else 64
    if network.prefixlen < minimum_prefix:
        raise ValueError("allow-network must be /24 or narrower for IPv4, /64 or narrower for IPv6")
    if address not in network:
        raise ValueError("host must be inside allow-network")
    return str(address), network


def _client_is_allowed(client_host: str, network: AllowedNetwork | None) -> bool:
    """Allow local health checks and, when configured, the exact private LAN."""
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return network is not None and address.version == network.version and address in network


def _request_surface_is_allowed(method: str, path: str) -> bool:
    request_path = urlsplit(path).path
    if request_path.startswith("/api/v1/console/"):
        return method in {"GET", "HEAD"}
    if request_path == "/__runner__/health":
        return method in {"GET", "HEAD"}
    if request_path in {"/__runner__/run", "/__runner__/cancel"}:
        return method == "POST"
    if request_path == "/__runner__" or request_path.startswith("/__runner__/"):
        return False
    return method in {"GET", "HEAD"}


def _client_request_is_allowed(method: str, path: str, client_host: str) -> bool:
    """Keep LAN writes confined to the token-guarded research runner.

    The listener already admits only the explicitly configured private network.
    Console API routes remain GET/HEAD-only, while the research runner exposes
    its narrow health/run/cancel surface through the same-origin proxy.  The
    runner independently requires ``X-Runner-Token`` and an operator tag for
    every mutation and has no broker, credential, promotion, or order path.
    """
    try:
        ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return _request_surface_is_allowed(method, path)


def _single_header(headers: http.client.HTTPMessage, name: str) -> str | None:
    values = headers.get_all(name, [])
    if len(values) > 1:
        raise _ProxyRequestError(400, f"duplicate {name} is not allowed")
    return values[0].strip() if values else None


def _validated_proxy_body_length(
    method: str,
    path: str,
    headers: http.client.HTTPMessage,
) -> int:
    """Validate the complete proxy request envelope before touching ``rfile``."""

    if not _request_surface_is_allowed(method, path):
        raise _ProxyRequestError(405, "method or proxy path is not allowed")
    target_path = urlsplit(path).path
    is_json_mutation = method == "POST" and target_path in {
        "/__runner__/run",
        "/__runner__/cancel",
    }
    if headers.get_all("Transfer-Encoding", []):
        raise _ProxyRequestError(400, "Transfer-Encoding request bodies are not allowed")

    length_value = _single_header(headers, "Content-Length")
    if is_json_mutation and length_value is None:
        raise _ProxyRequestError(411, "Content-Length is required")
    if length_value is None:
        content_length = 0
    elif not re.fullmatch(r"0|[1-9][0-9]*", length_value):
        raise _ProxyRequestError(400, "Content-Length must be a non-negative decimal")
    else:
        if len(length_value) > len(str(_MAX_PROXY_BODY_BYTES)):
            raise _ProxyRequestError(413, "request body exceeds the proxy limit")
        content_length = int(length_value)
    if content_length > _MAX_PROXY_BODY_BYTES:
        raise _ProxyRequestError(413, "request body exceeds the proxy limit")

    content_type = _single_header(headers, "Content-Type")
    if is_json_mutation:
        media_type = (content_type or "").partition(";")[0].strip().lower()
        if media_type != "application/json":
            raise _ProxyRequestError(415, "runner mutations require application/json")
        if not _single_header(headers, "X-Runner-Token"):
            raise _ProxyRequestError(401, "X-Runner-Token is required")
        if not _single_header(headers, "X-Operator"):
            raise _ProxyRequestError(400, "X-Operator is required")
    elif content_length != 0 or content_type is not None:
        raise _ProxyRequestError(400, "read-only proxy requests must not carry a body")
    return content_length


def _is_event_stream_response(response: http.client.HTTPResponse) -> bool:
    content_type = str(response.getheader("Content-Type", ""))
    return content_type.partition(";")[0].strip().lower() == "text/event-stream"


def _spa_target(request_path: str, *, dist: Path = DIST) -> str | None:
    """Return an index fallback for a missing extensionless local route."""
    path = unquote(urlsplit(request_path).path)
    relative = path.lstrip("/")
    candidate = (dist / relative).resolve()
    try:
        candidate.relative_to(dist.resolve())
    except ValueError:
        return None
    if candidate.is_file() or (candidate.is_dir() and (candidate / "index.html").is_file()):
        return path
    if not Path(relative).suffix:
        return "/index.html"
    return path


def _cache_control_for_path(path: str) -> str | None:
    """Keep the SPA shell current while allowing hashed assets to cache."""
    request_path = urlsplit(path).path
    if request_path == "/" or request_path.endswith(".html"):
        return "no-store, max-age=0"
    return None


class SpaRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Static-file handler with safe React client-route fallback."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(DIST), **kwargs)

    def end_headers(self) -> None:
        cache_control = _cache_control_for_path(self.path)
        if cache_control is not None:
            self.send_header("Cache-Control", cache_control)
            self.send_header("Pragma", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if not self._request_is_allowed():
            return
        if self._proxy_target() is not None:
            self._proxy()
            return
        target = _spa_target(self.path)
        if target is None:
            self.send_error(404)
            return
        self.path = target
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler contract
        if not self._request_is_allowed():
            return
        if self._proxy_target() is not None:
            self._proxy()
            return
        target = _spa_target(self.path)
        if target is None:
            self.send_error(404)
            return
        self.path = target
        super().do_HEAD()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        if not self._request_is_allowed():
            return
        if self._proxy_target() is None:
            self.send_error(405)
            return
        self._proxy()

    def _request_is_allowed(self) -> bool:
        network = getattr(self.server, "allowed_network", None)
        client_host = str(self.client_address[0])
        if _client_is_allowed(client_host, network) and _client_request_is_allowed(
            self.command,
            self.path,
            client_host,
        ):
            return True
        self.send_error(403, "client network or request surface is not allowed")
        return False

    def _proxy_target(self) -> tuple[str, int, str] | None:
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/api/v1/console/"):
            return ("127.0.0.1", 8400, parsed.path + (f"?{parsed.query}" if parsed.query else ""))
        if parsed.path == "/__runner__" or parsed.path.startswith("/__runner__/"):
            upstream_path = parsed.path.removeprefix("/__runner__") or "/"
            return ("127.0.0.1", 8420, upstream_path + (f"?{parsed.query}" if parsed.query else ""))
        return None

    def _proxy(self) -> None:
        target = self._proxy_target()
        if target is None:
            self.send_error(404)
            return
        host, port, upstream_path = target
        try:
            content_length = _validated_proxy_body_length(
                self.command,
                self.path,
                self.headers,
            )
        except _ProxyRequestError as exc:
            self.send_error(exc.status_code, str(exc))
            return
        body: bytes | None = b"" if self.command == "POST" else None
        if content_length:
            connection_socket = getattr(self, "connection", None)
            original_timeout = (
                connection_socket.gettimeout()
                if connection_socket is not None
                else None
            )
            try:
                if connection_socket is not None:
                    connection_socket.settimeout(_PROXY_BODY_READ_TIMEOUT_SECONDS)
                body = self.rfile.read(content_length)
            except (OSError, TimeoutError):
                self.send_error(408, "request body read timed out")
                return
            finally:
                if connection_socket is not None:
                    connection_socket.settimeout(original_timeout)
            if len(body) != content_length:
                self.send_error(400, "request body ended before Content-Length")
                return
        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS | {"host", "content-length"}
        }
        connection = http.client.HTTPConnection(
            host,
            port,
            timeout=_upstream_timeout_for_path(upstream_path),
        )
        try:
            connection.request(self.command, upstream_path, body=body, headers=headers)
            response = connection.getresponse()
            if _is_event_stream_response(response) and connection.sock is not None:
                # The upstream emits a heartbeat every 15 seconds.  Keep a
                # wider read timeout so the proxy never races the heartbeat.
                connection.sock.settimeout(45)
            self._relay_response(response)
        except OSError as exc:
            self.send_error(502, f"local upstream unavailable: {type(exc).__name__}")
        finally:
            connection.close()

    def _relay_response(self, response: http.client.HTTPResponse) -> None:
        """Relay ordinary payloads eagerly and SSE payloads line by line."""
        is_event_stream = _is_event_stream_response(response)
        if not is_event_stream:
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in _HOP_BY_HOP_HEADERS | {"content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
            return

        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in _HOP_BY_HOP_HEADERS | {"content-length"}:
                self.send_header(key, value)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.close_connection = True
        if self.command == "HEAD":
            return
        while True:
            try:
                line = response.readline()
                if not line:
                    return
                self.wfile.write(line)
                self.wfile.flush()
            except OSError:
                # Browser disconnects and idle upstreams are normal for SSE;
                # EventSource reconnects and receives a fresh verified snapshot.
                return


class LocalThreadingServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler: type[http.server.BaseHTTPRequestHandler],
        *,
        allowed_network: AllowedNetwork | None = None,
    ) -> None:
        self.allowed_network = allowed_network
        super().__init__(server_address, request_handler)


def _has_real_local_api_contract(
    *,
    dist: Path = DIST,
    release_commit: str | None = None,
) -> bool:
    """Reject bundles that would silently fall back to frontend mock data."""
    assets = dist / "assets"
    index_path = dist / "index.html"
    if not index_path.is_file() or not assets.is_dir():
        return False
    expected_release = (
        release_commit
        if release_commit is not None
        else os.getenv(RELEASE_COMMIT_ENV, "").strip().lower()
    )
    if re.fullmatch(r"[0-9a-f]{40}", expected_release) is None:
        return False
    contract_path = dist / FRONTEND_RUNTIME_CONTRACT
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate runtime contract key")
                value[key] = item
            return value

        contract = json.loads(
            contract_path.read_text(encoding="utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if contract != {
        "schema": "quantos.frontend_runtime.v1",
        "release_commit": expected_release,
        "api_url": "__QUANTOS_SAME_ORIGIN__",
        "runner_url": "__QUANTOS_SAME_ORIGIN__",
        "mock_mode": False,
    }:
        return False
    try:
        index = index_path.read_bytes()
    except OSError:
        return False
    entry_match = _ENTRY_SCRIPT_RE.search(index)
    if entry_match is None:
        return False
    entry_name = entry_match.group(1).decode("ascii", errors="strict")
    try:
        entry = (assets / entry_name).read_bytes()
    except (OSError, UnicodeError):
        return False

    # Vite places the same-origin runtime marker in the active entry chunk.
    # Searching every file under dist/assets is unsafe: an unrelated UI help
    # string can make a mock build look connected.
    if REQUIRED_LOCAL_API_MARKERS[0] not in entry:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--allow-network")
    args = parser.parse_args(argv)
    try:
        host, allowed_network = _validated_listener(args.host, args.allow_network)
    except ValueError as exc:
        parser.error(str(exc))
    if not _has_real_local_api_contract():
        parser.error(
            "real local frontend build is unavailable; run npm run build "
            "in apps/research-console"
        )
    with LocalThreadingServer(
        (host, args.port),
        SpaRequestHandler,
        allowed_network=allowed_network,
    ) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
