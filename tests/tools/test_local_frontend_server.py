from __future__ import annotations

from tools.local_frontend_server import (
    REQUIRED_LOCAL_API_MARKERS,
    SpaRequestHandler,
    _has_real_local_api_contract,
    _spa_target,
)


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
