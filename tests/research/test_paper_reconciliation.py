"""Phase 9 — paper-trading PnL reconciliation: real average-cost accounting with
an exact reconciliation identity, replacing the notional*0.001 placeholder."""
import pytest

from services.research.paper_trading.domain.models import OrderSide, PaperFill, PaperRun
from services.research.paper_trading.domain.pnl import PnLAttributor
from services.research.paper_trading.domain.reconciliation import reconcile


def _fill(iid, side, qty, price, ts, comm=0.0, slip=0.0):
    return PaperFill(fill_id=f"{iid}-{ts}", instrument_id=iid, side=side, quantity=qty,
                     price=price, timestamp=ts, commission=comm, slippage=slip)


def test_round_trip_realized_pnl_and_reconciles():
    fills = [_fill("AAA", OrderSide.BUY, 100, 10.0, 1, comm=1.0, slip=0.5),
             _fill("AAA", OrderSide.SELL, 100, 12.0, 2, comm=1.0, slip=0.5)]
    r = reconcile(fills, initial_capital=1000.0)
    assert r.by_instrument["AAA"].realized == pytest.approx(200.0)   # 100*(12-10)
    assert r.total_net_pnl == pytest.approx(197.0)                   # 200 - 3 costs
    assert r.positions == {}                                        # flat
    assert r.reconciliation_residual() < 1e-9                       # exact


def test_open_long_marks_to_market():
    r = reconcile([_fill("AAA", OrderSide.BUY, 100, 10.0, 1)], marks={"AAA": 11.0}, initial_capital=1000.0)
    assert r.by_instrument["AAA"].unrealized == pytest.approx(100.0)
    assert r.positions == {"AAA": 100} and r.reconciliation_residual() < 1e-9


def test_short_cover_realized():
    fills = [_fill("AAA", OrderSide.SELL, 50, 20.0, 1), _fill("AAA", OrderSide.BUY, 50, 18.0, 2)]
    r = reconcile(fills, initial_capital=1000.0)
    assert r.total_net_pnl == pytest.approx(100.0)                  # short 20, cover 18
    assert r.positions == {} and r.reconciliation_residual() < 1e-9


def test_long_to_short_flip_rebases_remainder():
    fills = [_fill("AAA", OrderSide.BUY, 100, 10.0, 1), _fill("AAA", OrderSide.SELL, 150, 12.0, 2)]
    r = reconcile(fills, marks={"AAA": 12.0}, initial_capital=1000.0)
    assert r.by_instrument["AAA"].realized == pytest.approx(200.0)  # closed 100 long @ +2
    assert r.positions == {"AAA": -50}                              # flipped to short 50
    assert r.reconciliation_residual() < 1e-9


def test_attribution_is_real_pnl_not_placeholder():
    run = PaperRun(run_id="r1", alpha_id="alpha-1")
    fills = [_fill("AAA", OrderSide.BUY, 100, 10.0, 1, comm=1.0, slip=1.0),
             _fill("AAA", OrderSide.SELL, 100, 12.0, 2, comm=1.0, slip=1.0)]
    attr = PnLAttributor.attribute(run, fills, sector_map={"AAA": "TECH"})
    assert attr.pnl_by_alpha["alpha-1"] == pytest.approx(196.0)     # 200 - 4 costs (REAL)
    assert attr.pnl_by_alpha["alpha-1"] != pytest.approx(2.2)       # NOT the old notional*0.001
    assert attr.pnl_by_sector["TECH"] == pytest.approx(196.0)
    assert attr.total_commission == pytest.approx(2.0)


def test_losing_trade_is_negative():
    run = PaperRun(run_id="r2", alpha_id="alpha-2")
    fills = [_fill("BBB", OrderSide.BUY, 10, 100.0, 1), _fill("BBB", OrderSide.SELL, 10, 90.0, 2)]
    attr = PnLAttributor.attribute(run, fills)
    assert attr.pnl_by_alpha["alpha-2"] == pytest.approx(-100.0)    # lost 10 per share


def test_reconcile_is_deterministic():
    fills = [_fill("AAA", OrderSide.BUY, 100, 10.0, 1), _fill("AAA", OrderSide.SELL, 40, 11.0, 2)]
    a = reconcile(fills, marks={"AAA": 11.0}, initial_capital=1000.0)
    b = reconcile(fills, marks={"AAA": 11.0}, initial_capital=1000.0)
    assert a.total_net_pnl == b.total_net_pnl and a.positions == b.positions
