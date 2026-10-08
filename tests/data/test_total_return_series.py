"""Rank #23 — PIT total-return series assembles the split + dividend primitives into the
total-return-adjusted close a backtest needs. Splits handled by adjusted_close; dividends
reinvested as a scale-free yield (no ex-date reference price); actions applied only if
knowable by as_of (no lookahead)."""
import pytest

from quantos.contracts.data import Bar, CorporateAction, CorporateActionType
from services.data.corporate_actions import total_return_series


def _bar(et, close):
    return Bar(instrument_id="X", event_time=et, knowledge_time=et,
               open=close, high=close, low=close, close=close, volume=1.0)


def _bars(closes):
    return [_bar(100 + i, c) for i, c in enumerate(closes)]


def test_no_actions_returns_the_raw_close_path():
    closes = [10.0, 11.0, 12.1]
    out = total_return_series(_bars(closes), [], as_of=10_000)
    assert out == pytest.approx(closes)                    # no splits/divs -> identity


def test_empty_bars_returns_empty():
    assert total_return_series([], [], as_of=10_000) == []


def test_split_is_absorbed_without_an_artificial_jump():
    # prices double, then a 2:1 split halves the quote at t=102; the RAW quote jumps down but the
    # total-return series must be smooth (the split is not a real loss)
    bars = _bars([50.0, 100.0, 50.0, 55.0])                # raw: doubles, splits (100->50), then +10%
    split = CorporateAction(instrument_id="X", action_type=CorporateActionType.SPLIT,
                            knowledge_time=101, event_time=102, ex_date="2024-01-03", ratio=2.0)
    out = total_return_series(bars, [split], as_of=10_000)
    # step returns: +100%, 0% (the 100->50 drop is the split, not a loss), +10%
    assert out[1] / out[0] == pytest.approx(2.0)
    assert out[2] / out[1] == pytest.approx(1.0, abs=1e-9)  # no phantom -50%
    assert out[3] / out[2] == pytest.approx(1.10)


def test_dividend_is_reinvested_above_the_price_only_path():
    bars = _bars([100.0, 100.0])                            # flat price
    div = CorporateAction(instrument_id="X", action_type=CorporateActionType.DIVIDEND,
                          knowledge_time=100, event_time=101, ex_date="2024-01-02", amount=2.0)   # 2% yield in the step
    out = total_return_series(bars, [div], as_of=10_000)
    assert out[1] / out[0] == pytest.approx(1.02)          # flat price + 2% dividend yield
    # without the dividend the path would be flat
    assert total_return_series(bars, [], as_of=10_000)[1] == pytest.approx(100.0)


def test_action_not_yet_knowable_is_excluded_pit():
    bars = _bars([100.0, 100.0])
    div = CorporateAction(instrument_id="X", action_type=CorporateActionType.DIVIDEND,
                          knowledge_time=500, event_time=101, ex_date="2024-01-02", amount=2.0)   # announced at kt=500
    # as_of before the announcement -> the dividend is not knowable -> excluded
    assert total_return_series(bars, [div], as_of=200)[1] == pytest.approx(100.0)
    # as_of after the announcement -> reinvested
    assert total_return_series(bars, [div], as_of=600)[1] == pytest.approx(102.0)
