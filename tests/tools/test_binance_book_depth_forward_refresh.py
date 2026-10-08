from tools import binance_book_depth_forward_refresh as worker


def test_worker_routes_all_mutable_outputs_to_state_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "runtime_state_root", lambda: tmp_path)
    monkeypatch.setattr(worker, "fetch_main", lambda argv: calls.append(argv) or 0)

    assert worker.main() == 0
    assert calls == [
        [
            "--store",
            str(tmp_path / "data" / "cache" / "binance_book_depth_archive.db"),
            "--archive-root",
            str(tmp_path / "data" / "cache" / "raw_market_data"),
            "--status",
            str(
                tmp_path
                / "data"
                / "cache"
                / "binance_book_depth_archive_status.json"
            ),
            "--days",
            "1",
            "--workers",
            "4",
            "--availability-lookback-days",
            "3",
        ]
    ]


def test_worker_propagates_fail_closed_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "runtime_state_root", lambda: tmp_path)
    monkeypatch.setattr(worker, "fetch_main", lambda _argv: 2)

    assert worker.main() == 2
