from tools import hyperliquid_public_forward_refresh as worker


def test_worker_routes_incremental_public_refresh_to_state_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "runtime_state_root", lambda: tmp_path)
    monkeypatch.setattr(worker, "fetch_main", lambda argv: calls.append(argv))

    assert worker.main() == 0
    assert calls == [[
        "--days", "7",
        "--out", str(tmp_path / "data" / "cache" / "hyperliquid_public_market.json"),
        "--archive-root", str(tmp_path / "data" / "cache" / "raw_market_data"),
        "--max-pages", "5",
        "--merge-existing",
    ]]

