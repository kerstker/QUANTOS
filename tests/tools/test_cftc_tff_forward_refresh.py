from tools import cftc_tff_forward_refresh as worker


def test_worker_routes_official_history_to_state_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "runtime_state_root", lambda: tmp_path)
    monkeypatch.setattr(worker, "fetch_main", lambda argv: calls.append(argv))

    assert worker.main() == 0
    assert calls == [[
        "--out", str(tmp_path / "data" / "cache" / "cftc_tff_crypto.json"),
        "--archive-root", str(tmp_path / "data" / "cache" / "raw_market_data"),
    ]]

