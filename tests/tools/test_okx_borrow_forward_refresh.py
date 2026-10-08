from tools import okx_borrow_forward_refresh as worker


def test_worker_roots_outputs_under_runtime_state(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_fetch(args):
        captured["args"] = args
        return {}

    monkeypatch.setattr(worker, "runtime_state_root", lambda: tmp_path)
    monkeypatch.setattr(worker, "fetch_main", fake_fetch)

    assert worker.main() == 0
    args = captured["args"]
    assert args[args.index("--out") + 1] == str(
        tmp_path / "data" / "cache" / "okx_public_borrow_rates.json"
    )
    assert args[args.index("--vintage-db") + 1] == str(
        tmp_path / "data" / "cache" / "okx_borrow_vintages.db"
    )
    assert args[args.index("--archive-root") + 1] == str(
        tmp_path / "data" / "cache" / "raw_market_data"
    )

