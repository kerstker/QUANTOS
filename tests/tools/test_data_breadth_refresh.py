import json

from tools import data_breadth_refresh as dr


def _liq(ticker, **kwargs):
    day = 1_700_000_000_000
    return {day: {"event_time": day, "knowledge_time": day + 1,
                  "liquidation_notional": 10.0}}


def _books(ticker, **kwargs):
    stamp = kwargs["observed_at_ms"]
    row = {"observed_at_ms": stamp, "venue": "fake", "instrument_id": ticker + "USDT"}
    return (row,)


def test_supplemental_refresh_keeps_per_channel_block_and_writes_status(tmp_path):
    status_path = str(tmp_path / "status.json")
    liq_path = str(tmp_path / "liq.json")
    book_path = str(tmp_path / "book.json")

    def blocked_books(ticker, **kwargs):
        if ticker == "ETH":
            raise RuntimeError("venue unavailable")
        return _books(ticker, **kwargs)

    status = dr.run_supplemental_refresh(
        tickers=("BTC", "ETH"), now_ms=1_700_000_000_000,
        liquidation_fetch=_liq, snapshot_fetch=blocked_books,
        status_path=status_path, liquidation_path=liq_path,
        snapshot_path=book_path, inventory_root=str(tmp_path), archive_root=None,
    )
    assert sum(row["status"] == "PASS" for row in status["reports"]) == 3
    assert any(row["channel"] == "cross_exchange_book" and row["status"] == "BLOCK"
               for row in status["reports"])
    assert json.loads(open(status_path, encoding="utf-8").read())["paper_live_submission"] is False
    assert json.loads(open(liq_path, encoding="utf-8").read())["data"]["BTC"]
