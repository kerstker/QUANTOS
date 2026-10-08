"""Synthetic research/market/keys; real local adapters, signature and OMS replay."""
from copy import deepcopy
from pathlib import Path
import json
import hashlib
import shutil
import time
import pytest

from live.execution import demo_issuer, demo_prepared_execution as executor
from live.execution import demo_position_context as position, demo_oms_reconciliation as reconciliation
from live.execution.demo_batch_risk import _digest
from live.execution.demo_execution_session import ExecutionSession
from live.execution.demo_pre_submit import _retain, _state
from live.execution.demo_wire_protocol import outcome_for_query
from live.execution.order_event_store import SqliteOrderEventStore
from live.exchange import binance_demo_exact as transport
from services.research.demo_user_stream import UserStreamJournal, normalize_message
from services.research.demo_account_baseline import normalize_response
from services.validation.demo_submission import GOLD_BATCH_SCHEMA
from tests.live.test_gold_demo_request_session import case as case, request_inputs, NOW
from tests.tools.test_gold_forward_review import captured
from tests.tools.test_gold_strategy_entry import origin_at
from tests.research.test_binance_order_receipts import message
from tools import gold_strategy_request, gold_strategy_cycle


def held_origin(original, *, wallet):
    origin = deepcopy(original)
    origin['observed_at_ms'] += 2000
    origin['completed_at_ms'] += 2000
    from decimal import Decimal
    margin = str(Decimal(wallet)+Decimal('.59'))
    for row in origin['reads']:
        row['requested_at_ms'] += 2000; row['received_at_ms'] += 2000
        kind, data = row['kind'], row['data']
        if kind.startswith('clock'): data['serverTime'] += 2000
        elif kind == 'book': data.update(time=NOW+2000, bidPrice='106', askPrice='106.1')
        elif kind == 'mark': data.update(time=NOW+2000, markPrice='106', indexPrice='106')
        elif kind.startswith('account'):
            asset = next(a for a in data['assets'] if a['asset'] == 'USDT')
            asset.update(walletBalance=wallet, crossWalletBalance=wallet, availableBalance=wallet,
                         unrealizedProfit='0.59', marginBalance=margin)
            data['positions'].append(dict(symbol='XAUUSDT', positionSide='BOTH', positionAmt='0.1'))
            row['data'] = normalize_response(kind, data)
        elif kind == 'positions':
            data.append(dict(symbol='XAUUSDT', positionSide='BOTH', positionAmt='0.1', entryPrice='100.1',
                markPrice='106', unRealizedProfit='0.59', isolatedWallet='0', marginAsset='USDT', updateTime=NOW+13))
            row['data'] = normalize_response(kind, data)
    return origin


@pytest.mark.parametrize('empty_entry,pilot_expired', [(False,False), (True,False), (False,True)])
@pytest.mark.parametrize('reserved_exit', [False, True])
def test_research_signal_signed_entry_receipts_and_owned_exit(case, tmp_path, monkeypatch, empty_entry,pilot_expired,reserved_exit):
    private, values, _, registry, policy, issuer_log = case
    from tests.live.gold_feedback_cycle import start, resume
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(time, 'time_ns', lambda: (NOW-4011)*1000000)
        research = start(tmp_path/'research-cycle', monkeypatch, automatic=True)
        observation, trial, definition = captured(tmp_path, monkeypatch, protected_network=True,
            strategy_trial=research['base']/'gold/results/1/trial')
    # The live-shaped path must reuse the original completed observation on
    # every held/settled tick, including after process-local preparation is lost.
    from tools import gold_strategy_worker
    from tools.gold_execution_identity import PreparedStrategy
    identity = hashlib.sha256((trial/'manifest.json').read_bytes()).hexdigest()
    runtime = dict(schema='GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1', source_commit='c'*40,
        trial_manifest_sha256=identity, plan=json.loads((observation/'registration.json').read_bytes())['plan'])
    execution_root = tmp_path/'strategy-executor'
    retained_trial = execution_root/'strategy-trials'/identity
    retained_observation = execution_root/'strategy-observation-work'/_digest(runtime)
    shutil.copytree(trial, retained_trial)
    from tests.tools.test_binance_demo_cash_window import Clock
    from tools import gold_strategy_cash
    from types import SimpleNamespace
    cash_clock = Clock(); cash_clock.ms = NOW-6000
    cash_reads = []; cash_wallet = ['5000']; cash_income = []
    cash_account = hashlib.sha256(b'BINANCE_USDM_DEMO:synthetic-key').hexdigest()
    def cash_get(path, **kwargs):
        at = cash_clock.ms; cash_clock.ms += 100; cash_reads.append(path)
        if path == '/fapi/v1/time':
            body = dict(serverTime=at+50)
        elif path == '/fapi/v3/account':
            body = dict(assets=[dict(asset='USDT', walletBalance=cash_wallet[0],
                updateTime=NOW-7000 if cash_wallet[0] == '5000' else NOW+2010)])
        elif path == '/fapi/v1/income':
            body = cash_income
        else:
            raise AssertionError(path)
        return json.dumps(body).encode()
    def collect_cash(account, **kwargs):
        assert account == cash_account
        return gold_strategy_cash.collector.collect(credential_file=None, operator_read=True,
            observe_seconds=30, credential_reader=lambda _:('synthetic-key','synthetic-secret'),
            get=cash_get, clock_ms=cash_clock.now, sleep=cash_clock.sleep, **kwargs)
    monkeypatch.setattr(gold_strategy_cash, '_collect', collect_cash)
    monkeypatch.setattr(gold_strategy_cash, 'time', SimpleNamespace(time_ns=lambda:cash_clock.ms*1000000))
    cash_args = dict(bundle=runtime, account=cash_account, source_commit='c'*40)
    opening_cash = gold_strategy_cash.open_window(tmp_path, **cash_args, observation_exists=False)
    assert len(cash_reads) == 2
    shutil.copytree(observation, retained_observation)
    for path in (execution_root, *execution_root.rglob('*')):
        path.chmod(0o700 if path.is_dir() else 0o600)
    before = {p.name:p.read_bytes() for p in retained_observation.iterdir()}
    def next_step(_observation, *, strategy_trial, **kwargs):
        prepared = PreparedStrategy(retained_trial)
        original, selected, _ = gold_strategy_worker.observe_inputs(execution_root, runtime,
            source_commit='c'*40, uid=execution_root.stat().st_uid, prepared=prepared)
        assert {p.name:p.read_bytes() for p in original.iterdir()} == before
        return gold_strategy_cycle.prepare(original, strategy_trial=selected, prepared_strategy=prepared, **kwargs)
    args = request_inputs(case)
    args['origin'] = origin_at(NOW)
    rules = next(r['data'] for r in args['origin']['reads'] if r['kind'] == 'rules')
    # Explicit synthetic 100-price venue, including its finite price ceiling.
    for group in (rules['terms']['filters'], rules['venue_filters']):
        for item in group:
            if item['filterType'] == 'PRICE_FILTER': item['maxPrice'] = '200'
    stores = {k: values[k] for k in ('dispatch','recovery','oms')}
    step = next_step(observation, strategy_trial=trial,
        origin=args['origin'], **stores, now_ms=NOW+10)
    assert step['status'] == 'DECISION_REQUIRES_APPROVAL' and step['action'] == 'OPEN_LONG'
    draft = step['draft']
    assert draft['orders'][0]['quantity'] == '0.1'
    # Explicit synthetic approval declarations, not production enrollment.
    profile = args['profile']; sid = definition['research_identity']['strategy_id']
    previous = next(iter(profile['attestations'].values()))
    profile['attestations'] = {sid: dict(previous, strategy_id=sid)}
    profile['strategy']['strategy_id'] = sid
    profile['native_limits'].update(default_max_position_qty=0.2, fat_finger_qty=0.2)
    for name, key in (('native_limits','native_limits_sha256'), ('strategy','strategy_approval_sha256'),
                      ('mandate','mandate_sha256'), ('attestations','validation_sha256')):
        policy['bindings'][key] = _digest(profile[name])
    policy['bindings']['strategy_id'] = sid
    policy['scope']['max_quantity_per_order'] = '0.2'
    policy['schema'] = 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
    policy['budget_group'] = dict(group_id='synthetic-gold-roundtrip-budget',
        strategy_definition_sha256=definition['result_hash'], valid_from_ms=policy['valid_from_ms'],
        valid_until_ms=policy['valid_until_ms'], max_grants=2, max_orders=2,
        max_total_notional='30', max_cancel_grants=2, max_cancel_orders=2)
    if reserved_exit:
        policy['schema']='DEMO_GOLD_STRATEGY_EXIT_BUDGET_POLICY_V1'
        policy['budget_group']['exit_reserve']=dict(grants=1,orders=1,notional='19.99')
    constraints = gold_strategy_request.constraints_from_draft(draft, now_ms=NOW+10)
    policy['bindings']['execution_constraints_sha256'] = _digest(constraints)
    task = gold_strategy_request.task_from_draft(draft, profile=profile, issuer_policy=policy,
        kill_state=args['kill_state'], source_commit='c'*40, now_ms=NOW+10)
    assert task['orders'] == draft['orders'] and task['execution_constraints'] == constraints
    assert task['valid_until_ms'] == draft['valid_until_ms']
    args.pop('orders')
    built = gold_strategy_request.prepare(draft, **args)
    stores = {k: values[k] for k in ('dispatch','recovery','oms')}
    session = ExecutionSession(**stores, evidence_directory=tmp_path/'evidence')
    handoff = session.propose(json.dumps(built['request']).encode(), now_ms=NOW+10)
    grant = demo_issuer.issue_submission(handoff, policy=policy, registry=registry,
        key=private, store=issuer_log, now_ms=NOW+11)
    monkeypatch.setattr(executor, 'now_ms', lambda: NOW+12)
    monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **k: None)
    monkeypatch.setattr(transport, '_credentials', lambda _: ('synthetic-key','synthetic-secret'))
    order = draft['orders'][0]; venue = 123
    calls = []
    monkeypatch.setattr(transport, '_http_once', lambda *a: (calls.append(1) or 200,
        json.dumps(dict(symbol='XAUUSDT', clientOrderId=order['newClientOrderId'], orderId=venue)).encode()))
    submitted = session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
    assert calls == [1] and stores['oms'].fills() == []
    waiting = next_step(observation, strategy_trial=trial,
        origin=args['origin'], **stores, now_ms=NOW+12)
    assert waiting['status'] == 'RECOVERY_REQUIRED' and waiting['draft'] is None
    batch = submitted['prepared']
    stream = UserStreamJournal(tmp_path/'gold-stream.db', account_ref_sha256=batch['context']['account_ref_sha256'])
    events = SqliteOrderEventStore(str(tmp_path/'gold-events.db'), create=True)
    try:
        stream.start_session(source_sha256='a'*64, at_ms=NOW)
        fill = (message(NOW+13, 'EXPIRED', '0', '0', trade_id=0, asset='USDT') if empty_entry else
                message(NOW+13, 'FILLED', '0.1', '0.1', trade_id=7, fee='0.0045045', asset='USDT'))
        fill['o'].update(s='XAUUSDT', c=order['newClientOrderId'], i=venue, q='0.1', p='100.1',
                         L='100.1', f='IOC', m=False, R=False, er='0')
        if empty_entry: fill['o'].update(x='EXPIRED', L='0')
        stream.record('MESSAGE', at_ms=NOW+14, message=normalize_message(json.dumps(fill)))
        query = dict(order, clientOrderId=order['newClientOrderId'], orderId=venue, origQty='0.1',
                     status='EXPIRED' if empty_entry else 'FILLED', executedQty='0' if empty_entry else '0.1', time=NOW+12, updateTime=NOW+13)
        for key in ('newClientOrderId','newOrderRespType','quantity'): query.pop(key)
        stores['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
            client_order_id=order['newClientOrderId'], requested_at_ms=NOW+30,
            outcome=outcome_for_query(order, 200, json.dumps(query).encode(), schema=GOLD_BATCH_SCHEMA), now_ms=NOW+31)
        root = tmp_path/'receipt-reconciliation'
        plan = reconciliation.prepare_resolution(batch, dispatch=stores['dispatch'], stream=stream,
            start_ms=NOW+1, end_ms=NOW+1000, source_commit='a'*40, output_directory=root, as_of_ms=NOW+1001)
        assert plan['status'] == 'READY_FOR_OMS_PROJECTION', plan
        outcome = reconciliation._project_resolution(plan, prepared=batch, **stores, event_store=events, now_ms=NOW+1001)
        _retain(root/'outcomes', outcome)
        monkeypatch.setattr(position, '_ROOT', tmp_path)
        monkeypatch.setattr(position, '_custody', lambda *a, **k: None)
        context = position.load_context(_state(**stores), symbol='XAUUSDT',
            account_ref=batch['context']['account_ref_sha256'], as_of_ms=NOW+2000)
        if empty_entry:
            assert context['quantity']=='0' and context['canonical_wallet']=='5000'
            assert next_step(observation,strategy_trial=trial,origin=origin_at(NOW+2000),
                **stores,now_ms=NOW+2000)['status']=='STRATEGY_EPISODE_SETTLED'
            assert calls==[1] and stores['oms'].fills()==[]
            cash_clock.ms=NOW+4000
            cash=gold_strategy_cash.close_window(tmp_path,**cash_args)
            from tools.gold_strategy_feedback import build, export_retained
            cash_end=gold_strategy_cash.collector.assess(cash)['bounds']['query_end_ms']
            feedback=build(observation,strategy_trial=trial,batches=[dict(prepared=batch,
                start_ms=NOW+1,end_ms=cash_end)],dispatch=stores['dispatch'],stream=stream,
                capture=cash,source_commit='a'*40,output_directory=tmp_path/'empty-feedback',
                as_of_ms=cash['completed_at_ms'])
            assert feedback['schema']=='DEMO_EXECUTION_RESEARCH_FEEDBACK_V1'
            assert feedback['orders'][0]['filled_quantity']=='0' and feedback['position_flat']
            assert feedback['wallet_residual']=='0' and feedback['strategy_pnl'] is None
            from tools.demo_feedback_publish import validate_export
            for damage in ('partial_fill','unknown','nonflat','cash_residual','boolean_quantity'):
                bad=deepcopy(feedback)
                if damage=='partial_fill': bad['orders'][0]['filled_quantity']='0.01'
                elif damage=='unknown': bad['orders'][0]['terminal_status']='NEW'
                elif damage=='nonflat': bad['position_flat']=False
                elif damage=='boolean_quantity': bad['orders'][0]['filled_quantity']=False
                else: bad['wallet_residual']='0.01'
                bad['result_hash']=_digest({k:v for k,v in bad.items() if k!='result_hash'})
                with pytest.raises(ValueError,match='FEEDBACK_PUBLICATION_FIELDS'):
                    validate_export(bad,source_commit='a'*40,feedback_sha256=bad['result_hash'],
                                    now=cash['completed_at_ms'])
            for parent in ('strategy-cash','receipt-reconciliation','evidence'):
                area=tmp_path/parent
                for item in (area,*area.rglob('*')):item.chmod(0o700 if item.is_dir() else 0o600)
            exported=export_retained(tmp_path,observation_directory=observation,strategy_trial=trial,
                bundle_sha256=_digest(runtime),account=cash['account_ref_sha256'],source_commit='a'*40,
                stores=dict(stores,stream=stream),as_of_ms=cash['completed_at_ms'])
            retained=json.loads(Path(exported['path']).read_bytes())
            assert retained['orders'][0]['terminal_status']=='EXPIRED'
            resume(research,retained,monkeypatch)
            assert calls==[1]
            return
        assert context['quantity'] == '0.1' and context['canonical_wallet'] == '4999.9954955'
        assert context['position_episode']['opened_venue_at_ms'] == NOW+13
        if pilot_expired:
            area=tmp_path/'gold-pilot';area.mkdir()
            config=dict(schema='GOLD_DEMO_PILOT_CONTROL_V1',environment='BINANCE_USDM_DEMO',symbol='XAUUSDT',
                account_ref_sha256=args['origin']['account_ref_sha256'],opening_origin_sha256=_digest(args['origin']),
                initial_equity='5000',started_at_ms=NOW+1000-86400000,ends_at_ms=NOW+1000,
                max_loss_fraction='0.05',submission_enabled=False)
            (area/'configuration.json').write_text(json.dumps(config))
        exit_step = next_step(observation, strategy_trial=trial,
            origin=held_origin(args['origin'], wallet=context['canonical_wallet']), **stores, now_ms=NOW+2000)
        assert exit_step['status'] == 'DECISION_REQUIRES_APPROVAL' and exit_step['action'] == 'REDUCE_LONG'
        exit_draft = exit_step['draft']
        assert exit_draft['reason'] == ('PILOT_DURATION_COMPLETE' if pilot_expired else 'TARGET')
        assert exit_draft['orders'][0]['quantity'] == '0.1'
        assert exit_draft['orders'][0]['reduceOnly'] is True
        assert len(stores['oms'].fills()) == 1 and calls == [1]
        # Independently declared second synthetic decision; the prior entry
        # approval is never reused for the exit draft.
        exit_origin = held_origin(args['origin'], wallet=context['canonical_wallet'])
        exit_constraints = gold_strategy_request.constraints_from_draft(exit_draft, now_ms=NOW+2000)
        policy['policy_id'] = 'isolated-test-exit-policy'
        policy['max_grants'] = 1
        policy['bindings']['execution_constraints_sha256'] = _digest(exit_constraints)
        exit_args = dict(args, origin=exit_origin, now_ms=NOW+2000)
        exit_input = gold_strategy_request.prepare(exit_draft, **exit_args)
        exit_session = ExecutionSession(**stores, evidence_directory=tmp_path/'evidence')
        exit_handoff = exit_session.propose(json.dumps(exit_input['request']).encode(), now_ms=NOW+2000)
        exit_grant = demo_issuer.issue_submission(exit_handoff, policy=policy, registry=registry,
            key=private, store=issuer_log, now_ms=NOW+2001)
        reservations = [row['payload'] for row in issuer_log.snapshot() if row['kind'] == 'RESERVED']
        assert len(reservations) == 2
        assert reservations[0]['policy']['policy_id'] != reservations[1]['policy']['policy_id']
        assert reservations[0]['policy']['budget_group'] == reservations[1]['policy']['budget_group']
        monkeypatch.setattr(executor, 'now_ms', lambda: NOW+2002)
        exit_order = exit_draft['orders'][0]
        monkeypatch.setattr(transport, '_http_once', lambda *a: (calls.append(2) or 200,
            json.dumps(dict(symbol='XAUUSDT', clientOrderId=exit_order['newClientOrderId'], orderId=124)).encode()))
        exit_submitted = exit_session.submit(exit_grant['proposal_sha256'], json.dumps(exit_grant['envelope']).encode())
        assert calls == [1, 2] and len(stores['oms'].fills()) == 1
        exit_batch = exit_submitted['prepared']
        closed_fill = message(NOW+2010, 'FILLED', '0.1', '0.1', trade_id=8, fee='0.00477', asset='USDT')
        closed_fill['o'].update(s='XAUUSDT', c=exit_order['newClientOrderId'], i=124, q='0.1', p='106',
            L='106', S='SELL', f='IOC', m=False, R=True, er='0', rp='0.59')
        stream.record('MESSAGE', at_ms=NOW+2012, message=normalize_message(json.dumps(closed_fill)))
        closed_query = dict(query, clientOrderId=exit_order['newClientOrderId'], orderId=124,
                            side='SELL', price='106', reduceOnly=True, time=NOW+2002, updateTime=NOW+2010)
        stores['dispatch'].record_query_result(reservation_sha256=exit_batch['reservation_sha256'],
            client_order_id=exit_order['newClientOrderId'], requested_at_ms=NOW+2030,
            outcome=outcome_for_query(exit_order, 200, json.dumps(closed_query).encode(), schema=GOLD_BATCH_SCHEMA),
            now_ms=NOW+2031)
        exit_plan = reconciliation.prepare_resolution(exit_batch, dispatch=stores['dispatch'], stream=stream,
            start_ms=NOW+2001, end_ms=NOW+3000, source_commit='a'*40, output_directory=root, as_of_ms=NOW+3001)
        assert exit_plan['status'] == 'READY_FOR_OMS_PROJECTION', exit_plan
        exit_outcome = reconciliation._project_resolution(exit_plan, prepared=exit_batch,
            **stores, event_store=events, now_ms=NOW+3001)
        _retain(root/'outcomes', exit_outcome)
        final = position.load_context(_state(**stores), symbol='XAUUSDT',
            account_ref=batch['context']['account_ref_sha256'], as_of_ms=NOW+4000)
        assert final['quantity'] == '0' and final['position_episode'] is None
        assert final['canonical_wallet'] == '5000.5807255'
        assert len(stores['oms'].fills()) == 2 and len(final['sources']) == 2
        assert final['cash_reconciled'] is False and final['source_verified'] is False
        closing = deepcopy(exit_origin)
        closing['observed_at_ms'] += 2000; closing['completed_at_ms'] += 2000
        for row in closing['reads']:
            row['requested_at_ms'] += 2000; row['received_at_ms'] += 2000
            kind, data = row['kind'], row['data']
            if kind.startswith('clock'): data['serverTime'] += 2000
            elif kind in ('book','mark'): data['time'] += 2000
            elif kind.startswith('account'):
                asset = next(a for a in data['assets'] if a['asset'] == 'USDT')
                asset.update(walletBalance='5000.5807255', crossWalletBalance='5000.5807255',
                    availableBalance='5000.5807255', marginBalance='5000.5807255', unrealizedProfit='0')
                data['positions'] = [p for p in data['positions'] if p['symbol'] != 'XAUUSDT']
                row['data'] = normalize_response(kind, data)
            elif kind == 'positions':
                row['data'] = normalize_response(kind, [p for p in data if p['symbol'] != 'XAUUSDT'])
        observed = position.validate_origin(closing, final, as_of_ms=NOW+4000)
        assert observed['positions'] == [] and observed['equity'] == '5000.5807255'
        settled = next_step(observation, strategy_trial=trial,
            origin=closing, **stores, now_ms=NOW+4000)
        assert settled['status'] == 'STRATEGY_EPISODE_SETTLED'
        assert settled['draft'] is None and settled['cash_reconciled'] is False
        # A fresh tick reconstructs closure from the original stores without
        # an in-memory "done" flag or a new private cycle ledger.
        assert next_step(observation, strategy_trial=trial,
            origin=closing, **stores, now_ms=NOW+4000) == settled
        assert calls == [1, 2]
        # Reconcile this strategy-generated opening/target exit through the
        # original cash exporter, rather than a separate fixed-order smoke.
        from live.execution.demo_research_feedback import build_roundtrip_feedback
        from services.research.gold_execution_feedback import research_trigger
        cash_wallet[0] = final['canonical_wallet']
        cash_income.extend(dict(symbol='XAUUSDT', incomeType=kind, income=value, asset='USDT',
            time=at, tranId=str(transaction), tradeId=str(trade))
            for kind, value, at, transaction, trade in (
                ('COMMISSION', '-0.0045045', NOW+13, 107, 7),
                ('COMMISSION', '-0.00477', NOW+2010, 108, 8),
                ('REALIZED_PNL', '0.59', NOW+2010, 109, 8)))
        cash_clock.ms = NOW+4000
        cash = gold_strategy_cash.close_window(tmp_path, **cash_args)
        assert cash['status'] == 'CAPTURED' and len(cash_reads) == 6
        assert cash['reads'][:2] == opening_cash['reads']
        assert gold_strategy_cash.close_window(tmp_path, **cash_args) == cash
        assert len(cash_reads) == 6
        cash_end = gold_strategy_cash.collector.assess(cash)['bounds']['query_end_ms']
        from tools.gold_strategy_feedback import build as build_strategy_feedback
        feedback = build_strategy_feedback(observation, strategy_trial=trial, batches=[
            dict(prepared=batch, start_ms=NOW+1, end_ms=NOW+2001),
            dict(prepared=exit_batch, start_ms=NOW+2001, end_ms=cash_end)],
            dispatch=stores['dispatch'], stream=stream, capture=cash, source_commit='a'*40,
            output_directory=tmp_path/'direct-strategy-feedback', as_of_ms=cash['completed_at_ms'])
        assert feedback['position_flat'] and feedback['wallet_residual'] == '0'
        assert feedback['canonical_wallet'] == '5000.5807255'
        assert feedback['cash_reconciliation']['fee'] == '0.0092745'
        config = dict(input_sha256=definition['research_identity']['base_input_sha256'],
                      execution_feedback_sha256=feedback['result_hash'])
        assert research_trigger(config, feedback, now=cash['completed_at_ms'])['ready']
        assert feedback['strategy_pnl'] is None and feedback['economic_eligible'] is False
        from tools.demo_feedback_publish import validate_export, publish
        from services.research_runner.factory_campaign import _feedback
        validate_export(feedback, source_commit='a'*40, feedback_sha256=feedback['result_hash'],
                        now=cash['completed_at_ms'])
        research_root = tmp_path/'feedback-research'
        import os
        with monkeypatch.context() as research_uid:
            # Execution fixtures model the root controller; publication runs
            # under the research owner's distinct identity in production.
            research_uid.setattr(os, 'geteuid', os.getuid)
            publish(research_root, feedback)
            publish(research_root, feedback)
        assert _feedback(research_root, feedback['result_hash']) == feedback
        from tools.gold_strategy_feedback import export_retained
        runtime_id = _digest(runtime)
        exporter_args = dict(observation_directory=observation, strategy_trial=trial,
            bundle_sha256=runtime_id, account=cash['account_ref_sha256'], source_commit='a'*40,
            stores=dict(stores, stream=stream), as_of_ms=cash['completed_at_ms'])
        for parent in ('strategy-cash', 'receipt-reconciliation', 'evidence'):
            area = tmp_path/parent
            for item in (area, *area.rglob('*')):
                item.chmod(0o700 if item.is_dir() else 0o600)
        exported = export_retained(tmp_path, **exporter_args)
        assert exported['status'] == 'STRATEGY_FEEDBACK_RETAINED'
        retained = json.loads(Path(exported['path']).read_bytes())
        assert retained['canonical_wallet'] == feedback['canonical_wallet']
        assert retained['research_identity'] == feedback['research_identity']
        assert export_retained(tmp_path, **exporter_args) == exported
        resume(research, retained, monkeypatch)
        from tools import gold_strategy_feedback as strategy_feedback
        original_proof = strategy_feedback._load_proof
        for damage in ('missing_constraints', 'other_definition'):
            with monkeypatch.context() as altered:
                def different_proof(prepared):
                    proof = deepcopy(original_proof(prepared))
                    if damage == 'missing_constraints':
                        proof.pop('execution_constraints')
                    else:
                        proof['execution_constraints']['strategy_definition_sha256'] = 'f'*64
                        proof['context']['execution_constraints_sha256'] = _digest(proof['execution_constraints'])
                    return proof
                altered.setattr(strategy_feedback, '_load_proof', different_proof)
                with pytest.raises(ValueError, match='STRATEGY_CONSTRAINTS_REQUIRED|STRATEGY_BINDING'):
                    build_strategy_feedback(observation, strategy_trial=trial, batches=[
                        dict(prepared=batch, start_ms=NOW+1, end_ms=NOW+2001),
                        dict(prepared=exit_batch, start_ms=NOW+2001, end_ms=cash_end)],
                        dispatch=stores['dispatch'], stream=stream, capture=cash, source_commit='a'*40,
                        output_directory=tmp_path/'rejected-feedback', as_of_ms=cash['completed_at_ms'])
                assert not (tmp_path/'rejected-feedback').exists()
        changed_cash = deepcopy(cash)
        for row in changed_cash['reads']:
            if row['kind'] == 'wallet_after':
                row['data'][0]['wallet'] = '5000.5907255'
        with pytest.raises(ValueError, match='CASH_EVIDENCE_UNRESOLVED'):
            build_roundtrip_feedback([
                dict(prepared=batch, start_ms=NOW+1, end_ms=NOW+2001),
                dict(prepared=exit_batch, start_ms=NOW+2001, end_ms=cash_end)],
                dispatch=stores['dispatch'], stream=stream, capture=changed_cash, source_commit='a'*40,
                output_directory=tmp_path/'direct-strategy-feedback', as_of_ms=cash['completed_at_ms'],
                research_identity=definition['research_identity'])
        for row in closing['reads']:
            if row['kind'].startswith('account'):
                asset = next(a for a in row['data']['assets'] if a['asset'] == 'USDT')
                for key in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):
                    asset[key] = '5000.5907255'
        with pytest.raises(ValueError, match='UNEXPLAINED_CASH_CHANGE'):
            position.validate_origin(closing, final, as_of_ms=NOW+4000)
    finally:
        events.close(); stream.db.close()


@pytest.mark.parametrize('pilot_expired',[False,True])
def test_cycle_with_no_signal_keeps_original_execution_stores_empty(case, tmp_path, monkeypatch,pilot_expired):
    _, values, _, _, _, _ = case
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(time, 'time_ns', lambda: (NOW-4011)*1000000)
        observation, trial, _ = captured(tmp_path, monkeypatch, sell_pressure=False)
    stores = {k: values[k] for k in ('dispatch','recovery','oms')}
    origin=origin_at(NOW)
    if pilot_expired:
        root=stores['dispatch'].path.parent
        monkeypatch.setattr(position,'_ROOT',root)
        monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
        area=root/'gold-pilot';area.mkdir()
        (area/'configuration.json').write_text(json.dumps(dict(
            schema='GOLD_DEMO_PILOT_CONTROL_V1',environment='BINANCE_USDM_DEMO',symbol='XAUUSDT',
            account_ref_sha256=origin['account_ref_sha256'],opening_origin_sha256=_digest(origin),
            initial_equity='5000',started_at_ms=NOW-86400000,ends_at_ms=NOW,
            max_loss_fraction='0.05',submission_enabled=False)))
    result = gold_strategy_cycle.prepare(observation, strategy_trial=trial,
        origin=origin, **stores, now_ms=NOW+10)
    assert result['status'] == ('PILOT_STOPPED' if pilot_expired else 'NO_ENTRY_SIGNAL') and result['draft'] is None
    if pilot_expired:assert json.loads((area/'stopped.json').read_bytes())['reason']=='DURATION_COMPLETE'
    assert stores['dispatch'].snapshot() == [] and stores['oms'].orders() == []
