from copy import deepcopy

import pytest

from live.execution.demo_batch_risk import _digest


@pytest.fixture
def case(monkeypatch):
    from tools import gold_strategy_exit as tool
    decision = {'decision_id': 'a'*64}
    plan = dict(quantity='0.002', stop='95', target='105', holding_ms=100,
                max_quote_age_ms=20, entry_timeout_ms=50)
    checked = dict(strategy_definition_sha256='b'*64, research_identity={'trial_id': 'c'*64},
                   observation_status='COMPLETE_OBSERVATION',
                   signals=[dict(decision=decision, exit_and_size_plan=plan)])
    ref = 'd'*64
    client = 'QD'+_digest(dict(account_ref_sha256=ref, strategy_definition_sha256='b'*64,
                              decision_id='a'*64, action='OPEN_LONG'))[:32]
    origin = dict(schema='DEMO_GOLD_EXECUTION_ORIGIN_V1', symbol='XAUUSDT', account_ref_sha256=ref,
                  reads=[dict(kind=k, requested_at_ms=210, received_at_ms=210,
                              data={'serverTime':210} if k.startswith('clock') else
                              {'time':210, 'bidPrice':'100'}) for k in ('clock_before','book','clock_after')])
    context = dict(quantity='0.001', ledger_state_hash='e'*64,
                   position_episode=dict(status='REPORTED_FILL_START', opened_venue_at_ms=100,
                                         client_order_id=client, reservation_sha256='f'*64))
    state = dict(reservations=[dict(record_hash='f'*64, payload=dict(orders=[dict(
        symbol='XAUUSDT', side='BUY', reduceOnly=False, newClientOrderId=client, quantity='0.002')]))])
    calls = []
    monkeypatch.setattr(tool, 'review', lambda *a, **k: deepcopy(checked))
    monkeypatch.setattr(tool, '_state', lambda *a: deepcopy(state))
    monkeypatch.setattr(tool.position, 'load_context', lambda *a, **k: deepcopy(context))
    monkeypatch.setattr(tool.position, 'validate_origin', lambda *a, **k: calls.append('origin'))
    monkeypatch.setattr(tool, '_venue_rules', lambda *a: calls.append('venue'))
    monkeypatch.setattr(tool, '_venue_account_cap', lambda *a: calls.append('cap'))
    args = dict(strategy_trial='synthetic', origin=origin, dispatch=None, recovery=None, oms=None, now_ms=210)
    return tool, args, context, calls


@pytest.mark.parametrize('bid,opened,reason', [('94',150,'STOP'), ('106',150,'TARGET'),
                                             ('100',100,'TIMEOUT'), ('100',150,'NO_EXIT_TRIGGER')])
def test_original_exit_conditions_use_remaining_position(case, bid, opened, reason):
    tool, args, context, calls = case
    context['position_episode']['opened_venue_at_ms'] = opened
    args['origin']['reads'][1]['data']['bidPrice'] = bid
    result = tool.prepare('synthetic', **args)
    assert result['reason'] == reason
    assert result['submission_enabled'] is False
    if reason == 'NO_EXIT_TRIGGER':
        assert result['orders'] == []
    else:
        assert result['orders'][0]['quantity'] == '0.001'
        assert result['orders'][0]['side'] == 'SELL'
        assert result['orders'][0]['reduceOnly'] is True
        assert calls == ['origin', 'venue', 'cap']


@pytest.mark.parametrize('damage', ['other_entry', 'unknown_start', 'short', 'stale', 'future_quote'])
def test_unowned_unknown_or_stale_context_cannot_create_exit(case, damage):
    tool, args, context, _ = case
    if damage == 'other_entry': context['position_episode']['client_order_id'] = 'QDdifferent001'
    elif damage == 'unknown_start': context['position_episode'] = {'status':'START_UNAVAILABLE'}
    elif damage == 'short': context['quantity'] = '-0.001'
    elif damage == 'stale': args['now_ms'] += 21
    else: args['origin']['reads'][1]['data']['time'] += 1
    with pytest.raises(ValueError):
        tool.prepare('synthetic', **args)


def test_timeout_uses_venue_fill_and_book_clock_not_local_process_clock(case):
    tool, args, context, _ = case
    context['position_episode']['opened_venue_at_ms'] = 200
    for row in args['origin']['reads']:
        row['data']['serverTime' if row['kind'].startswith('clock') else 'time'] += 100
    result = tool.prepare('synthetic', **args)
    assert result['reason'] == 'TIMEOUT'
    assert result['valid_until_ms'] == 230


def test_repeated_draft_keeps_identity_partial_close_uses_new_remaining_state(case):
    tool, args, context, _ = case
    first = tool.prepare('synthetic', **args)
    assert tool.prepare('synthetic', **args) == first
    context['quantity'] = '0.0005'
    context['ledger_state_hash'] = '1'*64
    second = tool.prepare('synthetic', **args)
    assert second['orders'][0]['quantity'] == '0.0005'
    assert second['orders'][0]['newClientOrderId'] != first['orders'][0]['newClientOrderId']
    context['quantity'] = '0'
    context['position_episode'] = None
    assert tool.prepare('synthetic', **args)['orders'] == []


@pytest.mark.parametrize('trigger',['loss','expiry'])
def test_persisted_pilot_stop_drafts_owned_reduction_before_strategy_trigger(case,tmp_path,monkeypatch,trigger):
    import json
    from types import SimpleNamespace
    from live.execution import demo_gold_pilot as pilot
    tool,args,context,calls=case
    context['position_episode']['opened_venue_at_ms']=200
    monkeypatch.setattr(tool.position,'_ROOT',tmp_path)
    monkeypatch.setattr(tool.position,'_custody',lambda *a,**k:None)
    args['dispatch']=SimpleNamespace(path=tmp_path/'dispatch.sqlite')
    def account(*a,**k):
        calls.append('origin')
        return dict(equity='4750' if trigger=='loss' else '5000')
    monkeypatch.setattr(tool.position,'validate_origin',account)
    config=dict(schema='GOLD_DEMO_PILOT_CONTROL_V1',environment='BINANCE_USDM_DEMO',symbol='XAUUSDT',
        account_ref_sha256=args['origin']['account_ref_sha256'],opening_origin_sha256='a'*64,
        initial_equity='5000',started_at_ms=1,ends_at_ms=86400001,max_loss_fraction='0.05',submission_enabled=False)
    area=tmp_path/'gold-pilot';area.mkdir()
    (area/'configuration.json').write_text(json.dumps(config))
    if trigger=='expiry':
        # Persistent expiry was observed previously; use a fresh later book.
        args['now_ms']=86400002
        for row in args['origin']['reads']:
            row['requested_at_ms']=row['received_at_ms']=86400002
            row['data']['serverTime' if row['kind'].startswith('clock') else 'time']=86400002
        context['position_episode']['opened_venue_at_ms']=86400000
    result=tool.prepare('synthetic',**args)
    assert result['status']=='STRATEGY_EXIT_DRAFT'
    assert result['reason']==('PILOT_LOSS_LIMIT' if trigger=='loss' else 'PILOT_DURATION_COMPLETE')
    assert result['orders'][0]['reduceOnly'] is True and result['orders'][0]['quantity']=='0.001'
    assert result['pilot_control']['status']=='STOPPED'
    assert result['submission_enabled'] is False and result['risk_status']=='NOT_RUN'
