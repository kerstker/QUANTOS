"""Serve the built QUANTOS research console without a Windows console host.

This is a local-only L2 operational surface.  It serves the already-built
React bundle and falls back to ``index.html`` for client-side routes.  Building
the bundle remains an explicit IDE operation; the resident process requires no
Node/npm/cmd shell.
"""
from __future__ import annotations

import argparse
import http.client
import http.server
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "apps" / "research-console" / "dist"
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
        if self._proxy_target() is None:
            self.send_error(405)
            return
        self._proxy()

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
        connection = http.client.HTTPConnection(host, port, timeout=15)
        try:
            connection.request(self.command, upstream_path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in _HOP_BY_HOP_HEADERS | {"content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except OSError as exc:
            self.send_error(502, f"local upstream unavailable: {type(exc).__name__}")
        finally:
            connection.close()


class LocalThreadingServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


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
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("the research console may bind only to the local loopback")
    if not _has_real_local_api_contract():
        parser.error(
            "real local frontend build is unavailable; run npm run build "
            "in apps/research-console"
        )
    with LocalThreadingServer((args.host, args.port), SpaRequestHandler) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
