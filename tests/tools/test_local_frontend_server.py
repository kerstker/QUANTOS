from __future__ import annotations

from tools.local_frontend_server import (
    REQUIRED_LOCAL_API_MARKERS,
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
    (tmp_path / "index.html").write_text("app", encoding="utf-8")
    script = assets / "app.js"
    script.write_bytes(b"frontend mock")
    assert _has_real_local_api_contract(dist=tmp_path) is False

    script.write_bytes(b" ".join(REQUIRED_LOCAL_API_MARKERS))
    assert _has_real_local_api_contract(dist=tmp_path) is True
