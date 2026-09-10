"""Clock-sensitive replay compilation from a retained public packet tape."""
from copy import deepcopy
import json

import pytest

from tests.data.test_binance_execution_tape import snapshot, depth, trade
from services.execution.passive_execution_input import analyze, compile_tape, stream_urls


def tape():
    packets = [
        ('public', 1030, 'depth@100ms', depth()),
        ('market', 1053, 'aggTrade', trade(at=1050, quantity='1')),
        ('market', 1057, 'markPrice@1s', {'e':'markPriceUpdate','s':'BTCUSDC',
            'E':1055,'T':120000,'p':'100','i':'100','r':'0.0001'}),
        ('public', 1085, 'depth@100ms', depth(first=102,last=103,previous=101,at=1080,bids=[['99','2']])),
        ('market', 1155, 'aggTrade', trade(aggregate=201,first=403,last=405,at=1150,quantity='4')),
        ('public', 1185, 'depth@100ms', depth(first=104,last=105,previous=103,at=1180,bids=[['99','1']])),
        ('market', 1190, 'markPrice@1s', {'e':'markPriceUpdate','s':'BTCUSDC',
            'E':1187,'T':120000,'p':'100','i':'100','r':'0.0001'}),
    ]
    return {'schema':'BINANCE_RAW_EXECUTION_TAPE_V1','symbol':'BTCUSDC',
        'started_at_ms':1000,'ended_at_ms':1200,'stream_urls':stream_urls('BTCUSDC'),
        'snapshot':{'received_at_ms':1040,'raw_utf8':json.dumps(snapshot())},
        'messages':[{'sequence':i,'route':route,'received_at_ms':received,
            'raw_utf8':json.dumps({'stream':'btcusdc@'+stream,'data':payload})}
            for i,(route,received,stream,payload) in enumerate(packets)],
        'connections':{route:{'status':'DURATION_REACHED','started_at_ms':1000,
            'connected_at_ms':1010,'closed_at_ms':1200,
            'messages':sum(r[0]==route for r in packets)} for route in ('public','market')},
        'failures':[],'economic_eligible':False}


def test_compilation_preserves_exchange_and_receive_time_without_exposing_future_book():
    source=tape();before=deepcopy(source);compiled=compile_tape(source)
    assert source==before
    assert compiled.start_ms==1057 and compiled.end_ms==1150
    assert compiled.books[0].transaction_ms==1025 and compiled.books[0].available_ms==1040
    assert compiled.visible_book(1084).bids[0][1]==3
    assert compiled.visible_book(1085).bids[0][1]==2
    with pytest.raises(ValueError,match='NOT_AVAILABLE'):compiled.visible_book(1039)
    assert compiled.trades[-1].transaction_time_ms==1150
    assert compiled.trades[-1].received_at_ms==1155


def test_empty_joint_window_is_not_a_replay_ready_input():
    source=tape();source['snapshot']['received_at_ms']=1199
    assert analyze(source)['status']=='PUBLIC_EXECUTION_INPUT_VALIDATED'
    with pytest.raises(ValueError,match='NO_JOINT_EXECUTION_WINDOW'):compile_tape(source)


@pytest.mark.parametrize('mutation', ['count','connection_time','missing','gap','seal'])
def test_bad_capture_cannot_be_compiled(mutation):
    source=tape()
    if mutation=='count':source['connections']['public']['messages']-=1
    elif mutation=='connection_time':source['connections']['public']['connected_at_ms']=1040
    elif mutation=='missing':source['connections']['public']['status']='FAILED'
    elif mutation=='seal':source['result_hash']='0'*64
    else:
        row=json.loads(source['messages'][3]['raw_utf8']);row['data']['pu']=102
        source['messages'][3]['raw_utf8']=json.dumps(row)
    with pytest.raises(ValueError):compile_tape(source)


def test_compiled_book_prefix_is_immutable_and_late_receipt_does_not_retime_venue_event():
    compiled=compile_tape(tape());old=compiled.visible_book(1084)
    assert old.bids[0][1]==3
    with pytest.raises((TypeError,AttributeError)):old.bids[0]=(1,2)
    assert compiled.books[-1].transaction_ms==1180
    assert compiled.books[-1].available_ms==1185


def test_capture_window_cannot_expand_a_bounded_packet_tape_into_unbounded_ui_history():
    source=tape();source['ended_at_ms']=source['started_at_ms']+150001
    with pytest.raises(ValueError):compile_tape(source)


def test_missing_normal_quantity_is_not_replaced_with_total_rpi_volume():
    source=tape();packet=json.loads(source['messages'][1]['raw_utf8'])
    del packet['data']['nq'];source['messages'][1]['raw_utf8']=json.dumps(packet)
    assert analyze(source)['status']=='PUBLIC_EXECUTION_INPUT_VALIDATED'
    with pytest.raises(ValueError,match='NORMAL_TRADE_QUANTITY_UNAVAILABLE'):compile_tape(source)


@pytest.mark.parametrize('symbol',[[],{}])
def test_malformed_contract_is_an_unusable_report_instead_of_a_parser_crash(symbol):
    source=tape();source['symbol']=symbol
    assert analyze(source)['status']=='UNUSABLE_EXECUTION_INPUT'
