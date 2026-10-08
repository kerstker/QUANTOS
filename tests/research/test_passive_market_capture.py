import asyncio
from copy import deepcopy
import json

import pytest

from tests.data.test_binance_execution_tape import snapshot, depth, trade
from tools.passive_market_capture import collect, analyze, capture, stream_urls


def messages():
    mark={'e':'markPriceUpdate','E':1055,'s':'BTCUSDC','p':'100.5','i':'100',
          'r':'-0.0001','T':120000}
    return {
        'public':[{'stream':'btcusdc@depth@100ms','data':depth()},
                  {'stream':'btcusdc@depth@100ms','data':depth(first=102,last=103,previous=101,at=1080,bids=[['99','1.2']])}],
        'market':[{'stream':'btcusdc@aggTrade','data':trade()},
                  {'stream':'btcusdc@markPrice@1s','data':mark},
                  {'stream':'btcusdc@aggTrade','data':trade(aggregate=201,first=403,last=405,at=1090)}],
    }


class Feed:
    def __init__(self,rows):
        self.rows=deepcopy(rows);self.closed=[];self.urls=[]

    def __call__(self,url,**kwargs):
        owner=self;route='public' if '/public/' in url else 'market'
        self.urls.append(url)
        class Socket:
            async def __aenter__(self):return self
            async def __aexit__(self,*exc):owner.closed.append(route)
            async def recv(self):
                await asyncio.sleep(.008)
                if owner.rows[route]:return json.dumps(owner.rows[route].pop(0))
                await asyncio.Future()
        return Socket()


async def snapshot_fetch(symbol,timeout_seconds):
    assert symbol=='BTCUSDC' and 0<timeout_seconds<=10
    return json.dumps(snapshot(),indent=1)+'\n'


def clock():
    value=2000
    def tick():
        nonlocal value
        value+=1
        return value
    return tick


def run_collect(**kwargs):
    feed=kwargs.pop('connector',Feed(messages()))
    data=asyncio.run(collect('BTCUSDC',duration_seconds=.05,connector=feed,
                            snapshot_fetch=kwargs.pop('snapshot_fetch',snapshot_fetch),clock=clock(),**kwargs))
    return data,feed


def test_capture_routes_and_preserves_raw_messages_and_snapshot():
    urls=stream_urls('BTCUSDC')
    assert '/public/' in urls['public'] and 'btcusdc@depth@100ms' in urls['public']
    assert '/market/' in urls['market'] and 'btcusdc@aggTrade' in urls['market']
    assert '@markPrice@1s' in urls['market']
    data,feed=run_collect()
    assert sorted(feed.closed)==['market','public']
    assert data['snapshot']['raw_utf8']==json.dumps(snapshot(),indent=1)+'\n'
    assert len(data['messages'])==5 and data['failures']==[]
    report=analyze(data)
    assert report['status']=='PUBLIC_EXECUTION_INPUT_VALIDATED'
    assert report['counts']=={'depth_updates':2,'aggregate_trades':2,'mark_updates':1,'duplicates':0}
    assert report['book']['bids'][0]==['99','1.2']
    assert report['symbol']=='BTCUSDC' and report['quote']=='USDC'
    assert report['venue_receipts_verified'] is False and report['queue_model_calibrated'] is False
    assert report['economic_eligible'] is False


def test_sequence_gap_is_visible_with_original_raw_events_retained():
    rows=messages();rows['public'][1]['data']['pu']=102
    data,_=run_collect(connector=Feed(rows))
    report=analyze(data)
    assert report['status']=='UNUSABLE_EXECUTION_INPUT'
    assert any('DEPTH_SEQUENCE_GAP' in row['reason'] for row in report['failures'])
    assert len(data['messages'])==5


def test_absent_trades_or_marks_do_not_produce_a_ready_input():
    rows=messages();rows['market']=[]
    data,_=run_collect(connector=Feed(rows))
    report=analyze(data)
    assert report['status']=='UNUSABLE_EXECUTION_INPUT'
    assert {'NO_TRADES','NO_MARK_PRICE'}<={row['reason'] for row in report['failures']}


def test_snapshot_timeout_closes_both_sockets_and_keeps_partial_capture():
    async def never(*args):await asyncio.Future()
    data,feed=run_collect(snapshot_fetch=never)
    assert sorted(feed.closed)==['market','public']
    assert data['messages'] and data['snapshot'] is None
    assert any(row['reason']=='SNAPSHOT_TIMEOUT' for row in data['failures'])
    assert analyze(data)['status']=='UNUSABLE_EXECUTION_INPUT'


def test_message_budget_stops_capture_and_marks_the_incomplete_window():
    data,feed=run_collect(max_events=2)
    assert len(data['messages'])==2 and sorted(feed.closed)==['market','public']
    assert any(row['reason']=='MESSAGE_BUDGET_EXHAUSTED' for row in data['failures'])
    assert analyze(data)['status']=='UNUSABLE_EXECUTION_INPUT'


def test_capture_failure_does_not_echo_transport_configuration():
    class Broken:
        def __call__(self,*args,**kwargs):raise OSError('sensitive proxy configuration')
    data,_=run_collect(connector=Broken())
    assert data['failures'] and 'sensitive' not in json.dumps(data)
    assert analyze(data)['status']=='UNUSABLE_EXECUTION_INPUT'


def test_raw_tape_rejects_duplicate_json_keys_and_wrong_stream_identity():
    data,_=run_collect()
    data['messages'][0]['raw_utf8']='{"stream":"a","stream":"b","data":{}}'
    assert analyze(data)['status']=='UNUSABLE_EXECUTION_INPUT'
    data,_=run_collect()
    raw=json.loads(data['messages'][0]['raw_utf8']);raw['stream']='btcusdt@depth@100ms'
    data['messages'][0]['raw_utf8']=json.dumps(raw)
    assert analyze(data)['status']=='UNUSABLE_EXECUTION_INPUT'


def test_capture_writes_preregistration_and_never_overwrites_an_attempt(tmp_path):
    output=tmp_path/'capture'
    async def check_plan(symbol,timeout):
        assert (output/'preregistration.json').exists()
        return await snapshot_fetch(symbol,timeout)
    result=capture(output,'BTCUSDC',duration_seconds=.05,connector=Feed(messages()),snapshot_fetch=check_plan,clock=clock())
    assert result['status']=='PUBLIC_EXECUTION_INPUT_VALIDATED'
    assert (output/'raw-tape.json').exists() and (output/'result.json').exists()
    old=(output/'raw-tape.json').read_bytes()
    with pytest.raises(FileExistsError):capture(output,'BTCUSDC',duration_seconds=.05)
    assert (output/'raw-tape.json').read_bytes()==old


def test_future_receive_clock_changes_neither_old_raw_receipts_nor_old_summary():
    data,_=run_collect();before=deepcopy(data);report=analyze(data)
    assert data==before
    again=analyze(deepcopy(data))
    assert report==again


def test_missing_or_failed_connection_record_cannot_be_hidden_by_an_empty_error_list():
    data,_=run_collect()
    data['connections']['market']['status']='FAILED'
    report=analyze(data)
    assert report['status']=='UNUSABLE_EXECUTION_INPUT'
    assert any(row['reason']=='INCOMPLETE_STREAM_COVERAGE' for row in report['failures'])


def test_raw_journal_retains_packets_when_capture_is_interrupted(tmp_path):
    from tools import passive_market_capture as module
    out=tmp_path/'interrupted'
    def fail_after_partial(*args,**kwargs):
        async def interrupted():
            kwargs['journal']({'kind':'MESSAGE','event':{'raw_utf8':'retained public packet','sequence':0}})
            raise asyncio.CancelledError()
        return interrupted()
    original=module.collect
    module.collect=fail_after_partial
    try:
        with pytest.raises(asyncio.CancelledError):module.capture(out,'BTCUSDC',duration_seconds=.05)
    finally:module.collect=original
    assert 'retained public packet' in (out/'raw-events.jsonl').read_text()
    assert json.loads((out/'failure.json').read_text())['reason']=='CancelledError'
