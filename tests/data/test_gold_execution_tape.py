"""Golden-contract research input must stay isolated from BTC execution scope."""
from decimal import Decimal

import pytest

from services.data.binance_execution_tape import BtcDepthBook, PublicTradeCursor, SYMBOLS, ExecutionTapeError
from services.data.gold_execution_tape import GoldDepthBook, GoldTradeCursor
from tests.data.test_binance_execution_tape import snapshot, depth, trade


def gold_depth(**kwargs):
    return dict(depth(**kwargs), s='XAUUSDT')


def test_gold_is_separate_scope_and_existing_btc_classes_still_reject_it():
    assert SYMBOLS == frozenset({'BTCUSDC', 'BTCUSDT'})
    with pytest.raises(ExecutionTapeError, match='UNSUPPORTED_CONTRACT'):
        BtcDepthBook('XAUUSDT', snapshot(), received_at_ms=1040)
    with pytest.raises(ExecutionTapeError, match='UNSUPPORTED_CONTRACT'):
        PublicTradeCursor('XAUUSDT')
    for symbol in ('XAUTUSDT','BTCUSDT','GC','MGC'):
        with pytest.raises(ExecutionTapeError, match='UNSUPPORTED_CONTRACT'):
            GoldDepthBook(symbol, snapshot(), received_at_ms=1040)
        with pytest.raises(ExecutionTapeError, match='UNSUPPORTED_CONTRACT'):
            GoldTradeCursor(symbol)


def test_gold_waits_for_snapshot_receipt_and_rejects_stale_market_time():
    b = GoldDepthBook('XAUUSDT', snapshot(), received_at_ms=1040)
    b.apply(gold_depth(), received_at_ms=1030)
    with pytest.raises(ExecutionTapeError, match='NOT_AVAILABLE'):
        b.view(as_of_ms=1039, max_age_ms=100)
    v = b.view(as_of_ms=1040, max_age_ms=100)
    assert v.symbol == 'XAUUSDT' and v.bids[0][0] == Decimal('99')
    with pytest.raises(ExecutionTapeError, match='STALE_GOLD_BOOK'):
        b.view(as_of_ms=1200, max_age_ms=100)
    # Re-receiving an old event does not renew quote freshness.
    b.apply(gold_depth(), received_at_ms=1190)
    with pytest.raises(ExecutionTapeError, match='STALE_GOLD_BOOK'):
        b.view(as_of_ms=1200, max_age_ms=100)


def test_gold_gap_or_wrong_contract_poison_epoch():
    b = GoldDepthBook('XAUUSDT', snapshot(), received_at_ms=1040)
    b.apply(gold_depth(), received_at_ms=1030)
    with pytest.raises(ExecutionTapeError, match='DEPTH_SEQUENCE_GAP'):
        b.apply(gold_depth(first=105,last=107,previous=104,at=1050), received_at_ms=1060)
    with pytest.raises(ExecutionTapeError, match='BLOCKED'):
        b.view(as_of_ms=1070, max_age_ms=100)
    c = GoldTradeCursor('XAUUSDT')
    with pytest.raises(ExecutionTapeError, match='CONTRACT_MISMATCH'):
        c.apply(trade(), received_at_ms=1060)
    with pytest.raises(ExecutionTapeError, match='BLOCKED'):
        c.apply(dict(trade(),s='XAUUSDT'), received_at_ms=1061)


def test_trade_direction_deduplication_and_gap_are_preserved():
    c = GoldTradeCursor('XAUUSDT')
    t = dict(trade(maker=False), s='XAUUSDT')
    result = c.apply(t, received_at_ms=1060)
    assert result.aggressor_side == 'BUY' and result.symbol == 'XAUUSDT'
    assert c.apply(t, received_at_ms=1061) is None
    with pytest.raises(ExecutionTapeError, match='TRADE_SEQUENCE_GAP'):
        c.apply(dict(trade(aggregate=202,first=405,last=406,at=1070),s='XAUUSDT'),
                received_at_ms=1080)


@pytest.mark.parametrize('age', [True, 0, -1, 1.5])
def test_invalid_freshness_budget_rejected(age):
    b = GoldDepthBook('XAUUSDT', snapshot(), received_at_ms=1040)
    with pytest.raises(ExecutionTapeError, match='INVALID_GOLD_MAX_AGE'):
        b.view(as_of_ms=1040, max_age_ms=age)
