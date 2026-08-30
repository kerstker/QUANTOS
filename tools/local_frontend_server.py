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
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST_ENV = "QUANTOS_FRONTEND_DIST"


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
_HEAVY_SNAPSHOT_PATHS = frozenset(
    {
        "/api/v1/console/alpha-zoo",
        "/api/v1/console/system-completeness",
    }
)


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


def _client_request_is_allowed(method: str, path: str, client_host: str) -> bool:
    """Keep every non-loopback client on the read-only monitoring surface."""
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    request_path = urlsplit(path).path
    if method not in {"GET", "HEAD"}:
        return False
    return request_path != "/__runner__" and not request_path.startswith("/__runner__/")


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
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else None
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


def _has_real_local_api_contract(*, dist: Path = DIST) -> bool:
    """Reject bundles that would silently fall back to frontend mock data."""
    assets = dist / "assets"
    index_path = dist / "index.html"
    if not index_path.is_file() or not assets.is_dir():
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
