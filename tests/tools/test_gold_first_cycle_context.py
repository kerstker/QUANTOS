"""Native settled BTC prefix and synthetic Gold inputs exercise real consumers.

Protected host identity/custody and current source are isolated substitutes.
The V2 replay, SQLite stores, partition records and strategy reviews are real;
there is no production authority, credential access or network operation.
"""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import time

import pytest

from live.execution import demo_position_context as position
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state
from tests.live.test_demo_market_partition import book as book, NOW
from tests.live.test_demo_market_partition_runtime import installed as installed
from tests.research.test_gold_bar_dataset import raw_rows, STEP
from tests.tools.test_gold_bar_definition import archive
from tests.tools.test_gold_forward_review import captured
from tests.tools.test_gold_strategy_entry import origin_at


def synthetic_origin(value, at):
    origin = origin_at(at)
    origin['account_ref_sha256'] = value['origin']['account_ref_sha256']
    for read in origin['reads']:
        if read['account_ref_sha256'] is not None:
            read['account_ref_sha256'] = origin['account_ref_sha256']
        if read['kind'].startswith('account'):
            read['data']['positions'] = []
            read['data']['assets'] = [dict(asset=asset, walletBalance=amount,
                crossWalletBalance=amount, availableBalance=amount,
                marginBalance=amount, unrealizedProfit='0')
                for asset, amount in [('USDC', '4999.6801'), ('USDT', '4200')]]
        elif read['kind'] == 'positions':
            read['data'] = []
    return origin


def configure_pilot(value, origin, at):
    root = value['stores']['dispatch'].path.parent
    area = root/'gold-pilot'
    area.mkdir()
    config = dict(schema='GOLD_DEMO_PILOT_CONTROL_V1', environment='BINANCE_USDM_DEMO',
        symbol='XAUUSDT', account_ref_sha256=origin['account_ref_sha256'],
        opening_origin_sha256=_digest(origin), initial_equity='4200',
        started_at_ms=at-100, ends_at_ms=at-100+86400000,
        max_loss_fraction='0.05', submission_enabled=False)
    (area/'configuration.json').write_text(json.dumps(config))
    return area


def consumer(value, tmp_path, monkeypatch, family, side):
    stores = {name: value['stores'][name] for name in ('dispatch','recovery','oms')}
    if family == 'bar':
        from services.research.gold_bar_dataset import load_bars
        from tools.gold_bar_demo_entry import BarDemoEntry
        from tools.gold_bar_demo_cycle import prepare
        rows = raw_rows()
        end = NOW//STEP*STEP
        for row in rows:
            row[0] += end-1000*STEP
            row[6] += end-1000*STEP
        warmup = load_bars(json.dumps(rows).encode(), requested_at_ms=end,
            received_at_ms=end+10)
        trial = archive(tmp_path)
        engine = BarDemoEntry(trial, candidate='channel',
            source_directory=Path(__file__).resolve().parents[2],
            warmup=warmup, registered_at_ms=end+100)
        received = end+STEP+20
        packet = dict(stream='xauusdt@kline_15m', data=dict(e='kline', E=received,
            s='XAUUSDT', k=dict(t=end, T=end+STEP-1, s='XAUUSDT', i='15m', x=True,
                o='100', h='110' if side=='BUY' else '102',
                l='89' if side=='SELL' else '99',
                c='109' if side=='BUY' else '90' if side=='SELL' else '101')))
        engine.ingest(json.dumps(packet), received_at_ms=received)
        at = received+1
        # Explicit test-only new key scope, so the next actual closed bar is
        # inside the synthetic independent key's window; old keys stay intact.
        value['registry']['keys'][-1]['valid_until_ms'] = at+300000
        origin = synthetic_origin(value, at)
        kwargs = dict(engine=engine, entry_decision_ms=received, origin=origin,
            now_ms=at, **stores)
        invoke = lambda: prepare(**kwargs)
    else:
        from tools.gold_strategy_cycle import prepare
        at = NOW+140000
        with monkeypatch.context() as clock:
            clock.setattr(time, 'time_ns', lambda:(at-4011)*1000000)
            observation, trial, _ = captured(tmp_path, monkeypatch,
                sell_pressure=side is not None)
        origin = synthetic_origin(value, at)
        kwargs = dict(strategy_trial=trial, origin=origin, now_ms=at, **stores)
        invoke = lambda: prepare(observation, **kwargs)
    area = configure_pilot(value, origin, at)
    return invoke, kwargs, area


@pytest.mark.parametrize('family,side', [('bar','BUY'), ('bar','SELL'),
    ('bar',None), ('absorption','BUY'), ('absorption',None)])
def test_first_gold_cycle_preserves_native_legacy_history_without_gold_context(
        installed, tmp_path, monkeypatch, family, side):
    value = installed
    invoke, kwargs, area = consumer(value, tmp_path, monkeypatch, family, side)
    before = deepcopy(value['state']), value['events'].row_count(), deepcopy(value['registry'])
    retained = (area/'configuration.json').read_bytes()
    context, _ = value['runtime'].resolve_context(state=value['state'],
        origin=kwargs['origin'], as_of_ms=kwargs['now_ms'])
    assert context is None and value['state']['reservations']
    result = invoke()
    assert result['status'] == ('DECISION_REQUIRES_APPROVAL' if side else 'NO_ENTRY_SIGNAL')
    assert result['position_flat'] is True and result['execution_history_empty'] is False
    assert result['execution_state_sha256'] == _digest(before[0])
    assert 'position_context_sha256' not in result
    assert result['submission_enabled'] is False and result['economic_authority'] is False
    assert result['cash_reconciled'] is False and result['pilot_control']['status'] == 'ACTIVE'
    if side:
        assert result['action'] == ('OPEN_SHORT' if side=='SELL' else 'OPEN_LONG')
        assert result['draft']['orders'][0]['reduceOnly'] is False
        assert result['execution_constraints']['decision_sha256'] == result['draft']['result_hash']
    else:
        assert result['draft'] is None and result['execution_constraints'] is None
    assert _state(**{name:value['stores'][name] for name in ('dispatch','recovery','oms')}) == before[0]
    assert value['events'].row_count() == before[1]
    assert value['registry'] == before[2]
    assert (area/'configuration.json').read_bytes() == retained
    old = position.load_context(value['state'], symbol='BTCUSDC',
        account_ref=kwargs['origin']['account_ref_sha256'], as_of_ms=kwargs['now_ms'])
    assert old['canonical_wallet'] == '4999.6801' and old['initial_equity'] == '5000'
    assert old['capacity_consumed'] == value['context']['capacity_consumed']
    assert sum(map(Decimal, old['capacity_consumed'].values())) == Decimal('239.8')


@pytest.mark.parametrize('family', ['bar','absorption'])
@pytest.mark.parametrize('damage', ['activation', 'no_new_key', 'source',
    'legacy_state', 'legacy_wallet', 'gold_wallet', 'other_position', 'stale'])
def test_first_cycle_rejects_changed_partition_or_account_before_pilot_and_draft(
        installed, tmp_path, monkeypatch, family, damage):
    from live.execution import demo_gold_pilot as pilot
    value = installed
    invoke, kwargs, area = consumer(value, tmp_path, monkeypatch, family, None)
    if damage == 'activation':
        value['activation'].unlink()
    elif damage == 'no_new_key':
        value['registry']['keys'].pop()
    elif damage == 'source':
        record = json.loads(value['executor'].read_bytes())
        record['source_commit'] = 'd'*40
        value['executor'].write_text(json.dumps(record))
    elif damage == 'legacy_state':
        state = deepcopy(value['state'])
        state['reservations'][0]['approval_id'] = 'tampered-old-prefix'
        module = __import__('tools.gold_bar_demo_cycle' if family=='bar'
            else 'tools.gold_strategy_cycle', fromlist=['_state'])
        monkeypatch.setattr(module, '_state', lambda *a:state)
    elif damage in ('legacy_wallet','gold_wallet'):
        asset = 'USDC' if damage=='legacy_wallet' else 'USDT'
        for read in kwargs['origin']['reads']:
            if read['kind'].startswith('account'):
                row = next(row for row in read['data']['assets'] if row['asset']==asset)
                for name in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):
                    row[name] = '4199'
    elif damage == 'other_position':
        for read in kwargs['origin']['reads']:
            if read['kind'] == 'positions':
                read['data'].append(dict(symbol='BTCUSDC', positionAmt='0.002'))
    else:
        kwargs['now_ms'] += 30001
    before = deepcopy(value['state']), value['events'].row_count()
    records = {path.name:path.read_bytes() for path in area.iterdir()}
    monkeypatch.setattr(pilot, 'observe', lambda *a,**k:pytest.fail('invalid cut reached pilot'))
    with pytest.raises(ValueError):
        invoke()
    assert _state(**{name:value['stores'][name] for name in ('dispatch','recovery','oms')}) == before[0]
    assert value['events'].row_count() == before[1]
    assert {path.name:path.read_bytes() for path in area.iterdir()} == records


@pytest.mark.parametrize('family', ['bar','absorption'])
@pytest.mark.parametrize('damage', ['no_partition','empty_prefix'])
def test_no_cut_or_erased_prefix_cannot_be_used_as_a_new_empty_account(
        installed, tmp_path, monkeypatch, family, damage):
    from live.execution import demo_gold_pilot as pilot
    value = installed
    invoke, _, area = consumer(value, tmp_path, monkeypatch, family, None)
    if damage == 'no_partition':
        for name in ('executor','issuer','activation'):
            value[name].unlink()
    else:
        module = __import__('tools.gold_bar_demo_cycle' if family=='bar'
            else 'tools.gold_strategy_cycle', fromlist=['_state'])
        monkeypatch.setattr(module, '_state', lambda *a:dict(
            reservations=[],recovery=[],oms=[],wire=[]))
    before = deepcopy(value['state']), value['events'].row_count()
    records = {path.name:path.read_bytes() for path in area.iterdir()}
    monkeypatch.setattr(pilot, 'observe', lambda *a,**k:pytest.fail('erased history reached pilot'))
    with pytest.raises(ValueError):
        invoke()
    assert _state(**{name:value['stores'][name] for name in ('dispatch','recovery','oms')}) == before[0]
    assert value['events'].row_count() == before[1]
    assert {path.name:path.read_bytes() for path in area.iterdir()} == records


@pytest.mark.parametrize('family', ['bar','absorption'])
def test_existing_stop_blocks_first_draft_after_equity_rebounds(
        installed, tmp_path, monkeypatch, family):
    value = installed
    invoke, kwargs, area = consumer(value, tmp_path, monkeypatch, family, 'BUY')
    config = json.loads((area/'configuration.json').read_bytes())
    (area/'binding.json').write_text(json.dumps(dict(schema='GOLD_DEMO_PILOT_BINDING_V1',
        configuration_sha256=_digest(config))))
    stopped = dict(schema='GOLD_DEMO_PILOT_STOP_V1',status='STOPPED',reason='LOSS_LIMIT',
        configuration_sha256=_digest(config),account_ref_sha256=config['account_ref_sha256'],
        observed_at_ms=config['started_at_ms']+1,observed_equity='3990',
        origin_sha256=_digest(kwargs['origin']),submission_enabled=False)
    (area/'stopped.json').write_text(json.dumps(stopped))
    before = deepcopy(value['state']), value['events'].row_count()
    records = {path.name:path.read_bytes() for path in area.iterdir()}
    result = invoke()
    assert result['status'] == 'PILOT_STOPPED' and result['reason'] == 'LOSS_LIMIT'
    assert result['draft'] is None and result['execution_constraints'] is None
    assert result['position_flat'] is True and result['execution_history_empty'] is False
    assert result['submission_enabled'] is False
    assert _state(**{name:value['stores'][name] for name in ('dispatch','recovery','oms')}) == before[0]
    assert value['events'].row_count() == before[1]
    assert {path.name:path.read_bytes() for path in area.iterdir()} == records
