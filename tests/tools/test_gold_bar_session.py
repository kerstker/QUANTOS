import asyncio
import hashlib
import json

import pytest

from tests.research.test_gold_bar_dataset import raw_rows, STEP


def fixture_history(tmp_path):
    history=tmp_path/'history'
    history.mkdir()
    values=raw_rows()
    raw=json.dumps(values).encode()
    end=values[-1][6]+1
    (history/'response.json').write_bytes(raw)
    (history/'receipt.json').write_text(json.dumps(dict(requested_at_ms=end,
        received_at_ms=end+10,response_sha256=hashlib.sha256(raw).hexdigest())))
    def message(index):
        now=end+(index+1)*STEP+20
        raw=json.dumps(dict(stream='xauusdt@kline_15m',data=dict(e='kline',E=now,s='XAUUSDT',
            k=dict(t=end+index*STEP,T=end+(index+1)*STEP-1,s='XAUUSDT',i='15m',
                x=True,o='100',h='110',l='99',c='109'))))
        return raw,now
    return history,message


def test_rotating_bar_session_retains_one_cursor_and_reconstructs(tmp_path,monkeypatch):
    from tools import gold_bar_session as tool
    history,message=fixture_history(tmp_path)
    monkeypatch.setattr(tool,'source_identity',lambda:{'fixture':True})
    monkeypatch.setattr(tool,'CHUNK_MESSAGES',1)
    async def stream(seconds,transport):
        transport['connected']=True
        yield message(0)
        yield message(1)
        await asyncio.sleep(10)
    monkeypatch.setattr(tool,'_messages',stream)
    result=asyncio.run(tool.observe(history,tmp_path/'session',seconds=1))
    assert result['status']=='OBSERVED' and result['closed_bars']==2
    assert result['chunks']==2 and result['missing_closed_bars']==0
    restored=tool.restore(tmp_path/'session')
    assert restored['closed_bars']==2 and restored['source']=={'fixture':True}
    assert restored['economic_eligible'] is False
    chunk=tmp_path/'session/messages-0000.jsonl'
    chunk.write_bytes(chunk.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='HASH'):
        tool.restore(tmp_path/'session')


def test_session_stream_failure_is_not_complete(tmp_path,monkeypatch):
    from tools import gold_bar_session as tool
    history,message=fixture_history(tmp_path)
    monkeypatch.setattr(tool,'source_identity',lambda:{'fixture':True})
    async def stream(seconds,transport):
        yield message(0)
        raise ConnectionError()
    monkeypatch.setattr(tool,'_messages',stream)
    result=asyncio.run(tool.observe(history,tmp_path/'session',seconds=1))
    assert result['status']=='INCOMPLETE' and result['closed_bars']==1
    with pytest.raises(ValueError,match='INCOMPLETE'):
        tool.restore(tmp_path/'session')


@pytest.mark.parametrize('retain_late_open', [False, True])
def test_late_open_wire_is_retained_without_weakening_original_decision_clock(
        tmp_path, monkeypatch, retain_late_open):
    from quantos.contracts.gold_collection_budget import SCHEMA, LIMITS
    from services.research.gold_bar_stream import GoldBarStream
    from tools import gold_bar_session as tool
    history, message = fixture_history(tmp_path)
    budget = dict(schema=SCHEMA, **LIMITS)
    original, first = message(0)
    late = json.loads(message(1)[0])
    late['data'].update(E=first + 10000)
    late['data']['k']['x'] = False
    late_wire = json.dumps(late)
    received = late['data']['E'] + 30756
    # The actual decision reader continues to reject this original wire.
    cursor = GoldBarStream(())
    with pytest.raises(ValueError, match='INVALID_BAR_EVENT_CLOCK_OR_SYMBOL'):
        cursor.ingest(late_wire, received_at_ms=received)
    monkeypatch.setattr(tool, 'source_identity', lambda: {'fixture': True})
    async def stream(seconds, transport, **options):
        assert options['maximum_connections'] == budget['maximum_bar_connections']
        transport['connected'] = True
        yield original, first
        yield late_wire, received
        yield message(1)
        await asyncio.sleep(10)
    monkeypatch.setattr(tool, '_messages', stream)
    options = dict(late_open_policy='RETAIN_LATE_OPEN_DIAGNOSTICS_V1') if retain_late_open else {}
    out = tmp_path / 'late-session'
    result = asyncio.run(tool.observe(history, out, seconds=1, collection_budget=budget, **options))
    if not retain_late_open:
        assert result['status'] == 'INCOMPLETE' and result['closed_bars'] == 1
        assert json.loads((out / 'rejected-message.json').read_text())['raw'] == late_wire
        with pytest.raises(ValueError, match='INCOMPLETE'):
            tool.restore(out)
    else:
        assert result['schema'] == 'GOLD_CONTINUOUS_BAR_SESSION_V3'
        assert result['status'] == 'OBSERVED' and result['closed_bars'] == 2
        assert result['rejected_late_open_messages'] == 1
        restored = tool.restore(out)
        assert restored['rejected_late_open_messages'] == 1
        assert restored['economic_eligible'] is restored['submission_enabled'] is False
        rows = [json.loads(line) for line in (out / 'messages-0000.jsonl').read_bytes().splitlines()]
        rejected, = [row for row in rows if row['decision']['status'] == 'REJECTED_LATE_OPEN_BAR']
        assert rejected['raw'] == late_wire and rejected['received_at_ms'] == received


def test_gap_survives_reconstruction_and_rehashed_decision_tamper_is_rejected(tmp_path,monkeypatch):
    from tools import gold_bar_session as tool
    history,message=fixture_history(tmp_path)
    monkeypatch.setattr(tool,'source_identity',lambda:{'fixture':True})
    async def stream(seconds,transport):
        transport['connected']=True
        yield message(0)
        yield message(2)
        await asyncio.sleep(10)
    monkeypatch.setattr(tool,'_messages',stream)
    out=tmp_path/'session'
    result=asyncio.run(tool.observe(history,out,seconds=1))
    assert result['missing_closed_bars']==1
    assert tool.restore(out)['missing_closed_bars']==1
    chunk=out/'messages-0000.jsonl'
    rows=[json.loads(line) for line in chunk.read_bytes().splitlines()]
    rows[0]['decision']['close_ms']+=STEP
    chunk.write_bytes(b''.join(tool._encode(row) for row in rows))
    manifest=json.loads((out/'manifest.json').read_text())
    manifest['chunks'][0].update(sha256=hashlib.sha256(chunk.read_bytes()).hexdigest(),bytes=chunk.stat().st_size)
    (out/'manifest.json').write_bytes(tool._encode(manifest))
    summary=json.loads((out/'summary.json').read_text())
    summary.update(manifest_sha256=hashlib.sha256((out/'manifest.json').read_bytes()).hexdigest(),bytes=chunk.stat().st_size)
    (out/'summary.json').write_bytes(tool._encode(summary))
    with pytest.raises(ValueError,match='DECISION_MISMATCH'):
        tool.restore(out)


@pytest.mark.parametrize('delay', [30000, 30001, 30756])
def test_retained_receipt_never_moves_the_actual_decision_cursor(tmp_path, delay):
    from tools import gold_bar_session as tool
    history, message = fixture_history(tmp_path)
    cursor = tool._cursor((history / 'response.json').read_bytes(),
        json.loads((history / 'receipt.json').read_bytes()), late_open_policy=tool.LATE_OPEN_POLICY)
    raw, received = message(0)
    cursor.ingest(raw, received_at_ms=received)
    before_bars, before_received = list(cursor.bars), cursor._stream.last_received
    wire = json.loads(message(1)[0])
    wire['data']['k']['x'] = False
    wire['data']['E'] = received + 10000
    actual_received = wire['data']['E'] + delay
    result = cursor.ingest(json.dumps(wire), received_at_ms=actual_received)
    assert cursor.bars == before_bars and cursor.last_received == actual_received
    assert cursor._stream.blocked is cursor.blocked is False
    if delay == 30000:
        assert result['status'] == 'OPEN_BAR'
        assert cursor._stream.last_received == actual_received
    else:
        assert result['status'] == 'REJECTED_LATE_OPEN_BAR'
        assert cursor._stream.last_received == before_received
        # A later business message may not move behind the raw receipt high-water mark.
        with pytest.raises(ValueError, match='INVALID_BAR_STREAM_RECEIPT'):
            cursor.ingest(raw, received_at_ms=actual_received - 1)
        with pytest.raises(ValueError, match='BAR_STREAM_BLOCKED'):
            cursor.ingest(message(1)[0], received_at_ms=message(1)[1])


@pytest.mark.parametrize('damage', ['stream', 'symbol', 'interval', 'closed', 'finality',
                                    'future', 'negative', 'boolean-clock', 'duplicate-keys'])
def test_retained_mode_still_blocks_invalid_or_late_closed_originals(tmp_path, damage):
    from tools import gold_bar_session as tool
    history, message = fixture_history(tmp_path)
    cursor = tool._cursor((history / 'response.json').read_bytes(),
        json.loads((history / 'receipt.json').read_bytes()), late_open_policy=tool.LATE_OPEN_POLICY)
    wire, received = message(0)
    document = json.loads(wire)
    document['data']['E'] = received - 30756
    document['data']['k']['x'] = False
    if damage == 'stream':
        document['stream'] = 'btcusdt@kline_15m'
    elif damage == 'symbol':
        document['data']['s'] = 'BTCUSDT'
    elif damage == 'interval':
        document['data']['k']['i'] = '1m'
    elif damage == 'closed':
        document['data']['k']['x'] = True
    elif damage == 'finality':
        document['data']['k']['x'] = 0
    elif damage == 'future':
        document['data']['E'] = received + 1
    elif damage == 'negative':
        document['data']['E'] = -1
    elif damage == 'boolean-clock':
        document['data']['E'] = True
    raw = json.dumps(document)
    if damage == 'duplicate-keys':
        raw = raw.replace('"x": false', '"x": false, "x": false')
    before = list(cursor.bars)
    with pytest.raises(ValueError):
        cursor.ingest(raw, received_at_ms=received)
    assert cursor.bars == before and cursor.blocked is True
    with pytest.raises(ValueError, match='BAR_STREAM_BLOCKED'):
        cursor.ingest(wire, received_at_ms=received)


@pytest.mark.parametrize('damage', ['missing-policy', 'wrong-policy', 'downgrade', 'missing-budget'])
def test_retained_plan_requires_explicit_version_and_original_budget(damage):
    from tools import gold_bar_session as tool
    from quantos.contracts.gold_collection_budget import SCHEMA, LIMITS
    plan = dict(schema=tool.RETAINED_SCHEMA, late_open_policy=tool.LATE_OPEN_POLICY,
                seconds=30, collection_budget=dict(schema=SCHEMA, **LIMITS))
    if damage == 'missing-policy':
        plan.pop('late_open_policy')
    elif damage == 'wrong-policy':
        plan['late_open_policy'] = True
    elif damage == 'downgrade':
        plan['schema'] = tool.BUDGETED_SCHEMA
    else:
        plan.pop('collection_budget')
    with pytest.raises(ValueError):
        tool.bar_plan_budget(plan)


def test_retained_rejections_consume_the_original_message_budget(tmp_path, monkeypatch):
    from tools import gold_bar_session as tool
    from quantos.contracts.gold_collection_budget import SCHEMA, LIMITS
    history, message = fixture_history(tmp_path)
    raw, received = message(0)
    wire = json.loads(raw)
    wire['data'].update(E=received - 30756)
    wire['data']['k']['x'] = False
    late_wire = json.dumps(wire)
    monkeypatch.setattr(tool, 'source_identity', lambda: {'fixture': True})
    async def stream(seconds, state, **kwargs):
        state['connected'] = True
        yield late_wire, received
        yield message(1)
    monkeypatch.setattr(tool, '_messages', stream)
    budget = dict(schema=SCHEMA, **LIMITS)
    budget['maximum_bar_messages'] = 1
    out = tmp_path / 'budgeted-rejections'
    result = asyncio.run(tool.observe(history, out, seconds=1, collection_budget=budget,
                                     late_open_policy=tool.LATE_OPEN_POLICY))
    assert result['status'] == 'INCOMPLETE' and result['messages'] == 1
    assert result['closed_bars'] == 0 and result['rejected_late_open_messages'] == 1
    assert json.loads((out / 'rejected-message.json').read_bytes())['error'] == 'BAR_SESSION_BUDGET'
    with pytest.raises(ValueError, match='INCOMPLETE'):
        tool.restore(out)


@pytest.mark.parametrize('damage', ['counter', 'boolean-counter', 'schema-downgrade',
                                    'forged-open', 'forged-closed', 'wrong-delay'])
def test_cold_retained_restore_rejects_rehashed_false_diagnostics(tmp_path, monkeypatch, damage):
    from tools import gold_bar_session as tool
    from quantos.contracts.gold_collection_budget import SCHEMA, LIMITS
    history, message = fixture_history(tmp_path)
    wire, received = message(0)
    late = json.loads(wire)
    late['data']['E'] = received - 30756
    late['data']['k']['x'] = False
    monkeypatch.setattr(tool, 'source_identity', lambda: {'fixture': True})
    async def stream(seconds, state, **kwargs):
        state['connected'] = True
        yield json.dumps(late), received
        yield message(1)
        await asyncio.sleep(10)
    monkeypatch.setattr(tool, '_messages', stream)
    original_timeout = asyncio.timeout
    monkeypatch.setattr(tool.asyncio, 'timeout', lambda seconds: original_timeout(.01))
    out = tmp_path / 'cold-retained'
    asyncio.run(tool.observe(history, out, seconds=1, collection_budget=dict(schema=SCHEMA, **LIMITS),
                             late_open_policy=tool.LATE_OPEN_POLICY))
    assert tool.restore(out)['rejected_late_open_messages'] == 1
    summary, manifest, plan = [json.loads((out / name).read_bytes())
                               for name in ('summary.json', 'manifest.json', 'plan.json')]
    if damage == 'counter':
        summary['rejected_late_open_messages'] = 0
    elif damage == 'boolean-counter':
        summary['rejected_late_open_messages'] = True
    elif damage == 'schema-downgrade':
        for document in (summary, manifest, plan):
            document['schema'] = tool.BUDGETED_SCHEMA
    else:
        path = out / 'messages-0000.jsonl'
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
        if damage == 'wrong-delay':
            rows[0]['decision']['delay_ms'] = 30000
        else:
            rows[0]['decision']['status'] = 'OPEN_BAR' if damage == 'forged-open' else 'CLOSED_BAR'
        raw = b''.join(tool._encode(row) for row in rows)
        path.write_bytes(raw)
        manifest['chunks'][0].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        (out / 'chunk-0000.json').write_bytes(tool._encode(manifest['chunks'][0]))
        summary['bytes'] = len(raw)
    (out / 'plan.json').write_bytes(tool._encode(plan))
    manifest['plan_sha256'] = hashlib.sha256((out / 'plan.json').read_bytes()).hexdigest()
    (out / 'manifest.json').write_bytes(tool._encode(manifest))
    summary['manifest_sha256'] = hashlib.sha256((out / 'manifest.json').read_bytes()).hexdigest()
    (out / 'summary.json').write_bytes(tool._encode(summary))
    with pytest.raises(ValueError, match='COUNTS|DOWNGRADE|DECISION_MISMATCH'):
        tool.restore(out)
