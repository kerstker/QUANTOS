import pytest
from services.research.backtest_engine.book import Book, Lot


def test_buy_then_buy_reaverages_cost():
    b = Book(cash=1000.0)
    b.apply_fill("X", 10, 10.0, 0.0)
    b.apply_fill("X", 10, 12.0, 0.0)
    assert b.positions["X"] == Lot(20, 11.0)
    assert b.cash == pytest.approx(1000.0 - 10 * 10 - 10 * 12)
    assert b.realized_pnl == 0.0


def test_partial_close_realizes_pnl_and_keeps_avg():
    b = Book(cash=0.0)
    b.apply_fill("X", 10, 10.0, 0.0)          # long 10 @ 10
    b.apply_fill("X", -4, 13.0, 0.0)          # sell 4 @ 13 -> realize (13-10)*4 = 12
    assert b.realized_pnl == pytest.approx(12.0)
    assert b.positions["X"] == Lot(6, 10.0)   # remainder keeps avg cost
    assert b.cash == pytest.approx(-10 * 10 + 4 * 13)


def test_flip_long_to_short_realizes_and_rebases():
    b = Book(cash=0.0)
    b.apply_fill("X", 10, 10.0, 0.0)          # long 10 @ 10
    b.apply_fill("X", -15, 12.0, 0.0)         # sell 15 @ 12 -> close 10 (realize 20), short 5 @ 12
    assert b.realized_pnl == pytest.approx((12.0 - 10.0) * 10)
    assert b.positions["X"] == Lot(-5, 12.0)  # remainder rebased at fill price


def test_short_cover_realizes_correct_sign():
    b = Book(cash=0.0)
    b.apply_fill("X", -10, 10.0, 0.0)         # short 10 @ 10
    b.apply_fill("X", 4, 8.0, 0.0)            # cover 4 @ 8 -> gain (10-8)*4 = 8
    assert b.realized_pnl == pytest.approx(8.0)
    assert b.positions["X"] == Lot(-6, 10.0)


def test_equity_and_unrealized_and_cost():
    b = Book(cash=1000.0)
    b.apply_fill("X", 10, 10.0, 5.0)          # cost 5
    assert b.total_cost == 5.0
    assert b.cash == pytest.approx(1000.0 - 100.0 - 5.0)
    assert b.equity({"X": 11.0}) == pytest.approx(b.cash + 10 * 11.0)
    assert b.unrealized({"X": 11.0}) == pytest.approx(10 * (11.0 - 10.0))
