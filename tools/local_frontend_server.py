"""Serve the built QUANTOS research console without a Windows console host.

This is a local-only L2 operational surface.  It serves the already-built
React bundle and falls back to ``index.html`` for client-side routes.  Building
the bundle remains an explicit IDE operation; the resident process requires no
Node/npm/cmd shell.
"""
from __future__ import annotations

import argparse
import http.server
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "apps" / "research-console" / "dist"
REQUIRED_LOCAL_API_MARKERS = (
    b"http://127.0.0.1:8400",
    b"http://127.0.0.1:8420",
)


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


class SpaRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Static-file handler with safe React client-route fallback."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(DIST), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        target = _spa_target(self.path)
        if target is None:
            self.send_error(404)
            return
        self.path = target
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler contract
        target = _spa_target(self.path)
        if target is None:
            self.send_error(404)
            return
        self.path = target
        super().do_HEAD()


class LocalThreadingServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _has_real_local_api_contract(*, dist: Path = DIST) -> bool:
    """Reject bundles that would silently fall back to frontend mock data."""
    assets = dist / "assets"
    if not (dist / "index.html").is_file() or not assets.is_dir():
        return False
    markers = set(REQUIRED_LOCAL_API_MARKERS)
    for script in assets.glob("*.js"):
        try:
            content = script.read_bytes()
        except OSError:
            return False
        markers = {marker for marker in markers if marker not in content}
        if not markers:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("the research console may bind only to the local loopback")
    if not _has_real_local_api_contract():
        parser.error(
            "real local frontend build is unavailable; run npm run build:localhost "
            "in apps/research-console"
        )
    with LocalThreadingServer((args.host, args.port), SpaRequestHandler) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
