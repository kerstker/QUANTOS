from tools import wikimedia_attention_forward_refresh as worker


def test_worker_routes_all_mutable_outputs_to_state_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "runtime_state_root", lambda: tmp_path)
    monkeypatch.setattr(worker, "fetch_main", lambda argv: calls.append(argv))

    assert worker.main() == 0
    assert calls == [[
        "--out", str(tmp_path / "data" / "cache" / "crypto_attention_pageviews.json"),
        "--archive-root", str(tmp_path / "data" / "cache" / "raw_market_data"),
        "--vintage-db", str(tmp_path / "data" / "cache" / "crypto_attention_vintages.db"),
    ]]
