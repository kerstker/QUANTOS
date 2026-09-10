"""Hand-built public feed cases for passive execution input, with distinct clocks."""
from copy import deepcopy
from decimal import Decimal as D

import pytest

from services.data.binance_execution_tape import (
    BtcDepthBook, PublicTradeCursor, ExecutionTapeError,
)


def snapshot():
    return {'lastUpdateId': 100, 'E': 1020, 'T': 1010,
            'bids': [['99', '2'], ['98', '4']], 'asks': [['101', '3'], ['102', '5']]}


def depth(*, first=99, last=101, previous=98, at=1025, bids=None, asks=None):
    return {'e': 'depthUpdate', 'E': at+1, 'T': at, 's': 'BTCUSDC',
            'U': first, 'u': last, 'pu': previous,
            'b': [['99', '3']] if bids is None else bids, 'a': [] if asks is None else asks}


def trade(*, aggregate=200, first=400, last=402, at=1050, maker=True, quantity='0.012345678901234567'):
    return {'e': 'aggTrade', 'E': at+1, 'T': at, 's': 'BTCUSDC', 'a': aggregate,
            'p': '99', 'q': quantity, 'f': first, 'l': last, 'm': maker}


def book():
    return BtcDepthBook('BTCUSDC', snapshot(), received_at_ms=1040)


def ready_book():
    result=book(); result.apply(depth(), received_at_ms=1030)
    return result


def test_snapshot_requires_overlapping_stream_and_knowledge_waits_for_snapshot_receipt():
    result=book()
    with pytest.raises(ExecutionTapeError, match='NOT_SYNCHRONIZED'):
        result.view(as_of_ms=1040)
    receipt=result.apply(depth(), received_at_ms=1030)
    assert receipt.status=='APPLIED'
    with pytest.raises(ExecutionTapeError, match='NOT_AVAILABLE'):
        result.view(as_of_ms=1039)
    view=result.view(as_of_ms=1040)
    assert view.symbol=='BTCUSDC' and view.last_update_id==101
    assert view.available_at_ms==1040 and view.transaction_time_ms==1025
    assert view.bids[0]==(D('99'), D('3'))
    assert view.source_hash and view.snapshot_hash


def test_absolute_depth_zero_deletion_and_immutable_prefix():
    result=ready_book(); old=result.view(as_of_ms=1040)
    result.apply(depth(first=102, last=104, previous=101, at=1050,
                       bids=[['99','0'],['97','0'],['98','1.2']]), received_at_ms=1060)
    new=result.view(as_of_ms=1060)
    assert old.bids==((D('99'), D('3')), (D('98'), D('4')))
    assert new.bids==((D('98'), D('1.2')),)
    assert old.source_hash!=new.source_hash
    with pytest.raises(ExecutionTapeError, match='NOT_AVAILABLE'):
        result.view(as_of_ms=1059)


def test_pre_snapshot_old_update_is_ignored_and_first_overlap_does_not_require_pu_match():
    result=book()
    old=depth(first=96,last=99,previous=95,at=1000)
    assert result.apply(old, received_at_ms=1010).status=='BEFORE_SNAPSHOT'
    assert result.apply(depth(previous=97), received_at_ms=1030).status=='APPLIED'
    assert result.view(as_of_ms=1040).last_update_id==101


def test_missing_bootstrap_or_later_gap_blocks_the_book_until_a_new_snapshot():
    result=book()
    with pytest.raises(ExecutionTapeError, match='BOOTSTRAP_GAP'):
        result.apply(depth(first=101,last=102), received_at_ms=1030)
    with pytest.raises(ExecutionTapeError, match='BLOCKED'):
        result.view(as_of_ms=2000)
    result=ready_book()
    with pytest.raises(ExecutionTapeError, match='DEPTH_SEQUENCE_GAP'):
        result.apply(depth(first=105,last=107,previous=104,at=1050), received_at_ms=1060)
    with pytest.raises(ExecutionTapeError, match='BLOCKED'):
        result.apply(depth(first=102,last=104,previous=101,at=1045), received_at_ms=1070)


def test_identical_retransmission_does_not_replenish_or_retime_depth():
    result=ready_book(); previous=result.view(as_of_ms=1040)
    receipt=result.apply(depth(), received_at_ms=1100)
    assert receipt.status=='DUPLICATE'
    assert result.view(as_of_ms=1100)==previous
    conflict=depth(bids=[['99','30']])
    with pytest.raises(ExecutionTapeError, match='CONFLICTING_DEPTH_ID'):
        result.apply(conflict, received_at_ms=1110)


@pytest.mark.parametrize('field,value', [('u',True),('s','BTCUSDT'),('E',9999),('T',1035),
                                      ('b',[['99','NaN']]),('b',[['99','1'],['99','2']]),
                                      ('b',[['102','2']]),('a',[['101','-1']])])
def test_malformed_or_uncertain_depth_cannot_leave_a_ready_stale_book(field,value):
    result=ready_book(); event=depth(first=102,last=103,previous=101,at=1030)
    event[field]=value
    with pytest.raises(ExecutionTapeError):result.apply(event, received_at_ms=1050)
    with pytest.raises(ExecutionTapeError, match='BLOCKED'):result.view(as_of_ms=1100)


@pytest.mark.parametrize('mutation', [lambda x:x.update(lastUpdateId=True),
                                    lambda x:x.update(bids=[['99','2'],['100','1']]),
                                    lambda x:x.update(asks=[['99','1']]),
                                    lambda x:x.update(bids=[['99','0']])])
def test_snapshot_rejects_invalid_ordered_positive_price_levels(mutation):
    value=snapshot(); mutation(value)
    with pytest.raises(ExecutionTapeError):BtcDepthBook('BTCUSDC', value, received_at_ms=1040)


def test_aggregated_trade_preserves_decimal_and_both_clocks_with_correct_aggressor():
    cursor=PublicTradeCursor('BTCUSDC')
    event=cursor.apply(trade(), received_at_ms=1070)
    assert event.quantity==D('0.012345678901234567')
    assert event.aggressor_side=='SELL' and event.transaction_time_ms==1050
    assert event.event_time_ms==1051 and event.received_at_ms==1070
    assert cursor.apply(trade(), received_at_ms=1080) is None
    next_event=cursor.apply(trade(aggregate=201,first=403,last=407,at=1085,maker=False),received_at_ms=1090)
    assert next_event.aggressor_side=='BUY'


def test_trade_id_gap_and_conflicting_duplicates_remain_blocked():
    cursor=PublicTradeCursor('BTCUSDC');cursor.apply(trade(),received_at_ms=1070)
    with pytest.raises(ExecutionTapeError,match='TRADE_SEQUENCE_GAP'):
        cursor.apply(trade(aggregate=202,first=404,last=406,at=1080),received_at_ms=1090)
    with pytest.raises(ExecutionTapeError,match='BLOCKED'):
        cursor.apply(trade(aggregate=201,first=403,last=403,at=1075),received_at_ms=1100)
    cursor=PublicTradeCursor('BTCUSDC');cursor.apply(trade(),received_at_ms=1070)
    with pytest.raises(ExecutionTapeError,match='CONFLICTING_TRADE_ID'):
        cursor.apply(trade(quantity='10'),received_at_ms=1080)


@pytest.mark.parametrize('field,value',[('m','false'),('a',True),('q','Infinity'),('q','0'),
                                     ('E',9999),('T',1060),('l',399),('s','BTCUSDT')])
def test_trade_validation_rejects_lossy_or_ambiguous_fields(field,value):
    value_dict=deepcopy(trade());value_dict[field]=value
    with pytest.raises(ExecutionTapeError):
        PublicTradeCursor('BTCUSDC').apply(value_dict,received_at_ms=1070)


def test_raw_trade_ids_may_have_gaps_but_must_not_overlap_or_reverse_time():
    cursor=PublicTradeCursor('BTCUSDC');cursor.apply(trade(),received_at_ms=1070)
    next_event=cursor.apply(trade(aggregate=201,first=410,last=411,at=1080),received_at_ms=1090)
    assert next_event.first_trade_id==410
    with pytest.raises(ExecutionTapeError,match='TRADE_RANGE_OVERLAP'):
        cursor.apply(trade(aggregate=202,first=411,last=414,at=1090),received_at_ms=1100)


def test_usdt_and_usdc_tapes_keep_separate_contract_identity():
    payload=depth();payload['s']='BTCUSDT'
    result=BtcDepthBook('BTCUSDT',snapshot(),received_at_ms=1040)
    result.apply(payload,received_at_ms=1030)
    assert result.view(as_of_ms=1040).symbol=='BTCUSDT'
