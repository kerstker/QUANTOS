"""Native retained bar feedback, with only external identity/venue I/O isolated."""
from contextlib import contextmanager, closing
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
from types import SimpleNamespace

from live.execution.demo_batch_risk import _digest
# Bind native readers before original account fixtures install custody aliases.
from tools import demo_reported_recovery as reported
from tools import gold_bar_feedback
from tests.live.test_gold_delegated_admission import completed_bar_entry, retain_exit, exit_proposal_values, held_bar_origin


def _finish_exit(f, monkeypatch):
    from live.execution import demo_issuer, demo_pre_submit as prep, demo_prepared_execution as executor
    from live.execution import demo_position_context as position, demo_oms_reconciliation as reconciliation
    from live.execution.demo_execution_session import ExecutionSession
    from live.execution.demo_issuer_store import IssuerStore
    from live.execution.demo_wire_protocol import outcome_for_query
    from live.exchange import binance_demo_exact as transport
    from services.research.demo_user_stream import normalize_message
    from services.validation.demo_submission import GOLD_BATCH_SCHEMA
    from tests.research.test_binance_order_receipts import message
    from tests.live.test_demo_execution_session import request

    f.exit_result = (retain_exit(f, at=f.now_ms+2000, price='90') if f.base_runtime['schema'] == 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1'
        else _retain_auto_exit(f, at=f.now_ms+2000))
    values = exit_proposal_values(f, f.exit_result)
    at = values['now_ms']
    order, = values['orders']
    raw = json.loads(request(values))
    raw.update(schema='DEMO_GOLD_STRATEGY_SESSION_INPUT_V1', execution_constraints=values['execution_constraints'])
    policy = deepcopy(f.base_policy)
    policy['policy_id'] = 'isolated-exact-full-exit'
    policy['bindings']['execution_constraints_sha256'] = _digest(values['execution_constraints'])
    monkeypatch.setattr(executor, 'now_ms', lambda: at + 2)
    f.exit_posts = []
    def post(*args):
        f.exit_posts.append(args[0])
        return 200, json.dumps(dict(symbol='XAUUSDT', clientOrderId=order['newClientOrderId'], orderId=124)).encode()
    monkeypatch.setattr(transport, '_http_once', post)
    with closing(ExecutionSession(**f.stores, evidence_directory=f.root/'evidence')) as session:
        handoff = session.propose(json.dumps(raw).encode(), now_ms=at)
        with closing(IssuerStore(f.ledger)) as issuer:
            f.issuer_before_exit = issuer.snapshot()
            grant = demo_issuer.issue_submission(handoff, policy=policy, registry=f.registry,
                key=f.private, store=issuer, now_ms=at+1)
            f.issuer_after_exit = issuer.snapshot()
        submitted = session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
    assert len(f.exit_posts) == 1 and submitted['execution']['status'] == 'WIRE_ATTEMPTS_RECORDED'
    batch = submitted['prepared']
    f.exit_batch = batch
    fill = message(at+13, 'FILLED', '0.1', '0.1', trade_id=8, fee='0.00405', asset='USDT')
    fill['o'].update(s='XAUUSDT', c=order['newClientOrderId'], i=124, q='0.1', p='90', L='90',
        S='SELL', f='IOC', m=False, R=True, er='0', rp='-1.01')
    f.stream.record('MESSAGE', at_ms=at+14, message=normalize_message(json.dumps(fill)))
    query = dict(order, clientOrderId=order['newClientOrderId'], orderId=124, origQty='0.1',
        status='FILLED', executedQty='0.1', time=at+2, updateTime=at+13)
    for key in ('newClientOrderId', 'newOrderRespType', 'quantity'):
        query.pop(key)
    f.stores['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
        client_order_id=order['newClientOrderId'], requested_at_ms=at+30,
        outcome=outcome_for_query(order, 200, json.dumps(query).encode(), schema=GOLD_BATCH_SCHEMA), now_ms=at+31)
    area = f.root/'receipt-reconciliation'
    plan = reconciliation.prepare_resolution(batch, dispatch=f.stores['dispatch'], stream=f.stream,
        start_ms=at+1, end_ms=at+1000, source_commit=f.source_commit, output_directory=area, as_of_ms=at+1001)
    assert plan['status'] == 'READY_FOR_OMS_PROJECTION', plan
    outcome = reconciliation._project_resolution(plan, prepared=batch, **f.stores,
        event_store=f.events, now_ms=at+1001)
    prep._retain(area/'outcomes', outcome)
    f.context = position.load_context(prep._state(**f.stores), symbol='XAUUSDT',
        account_ref=f.account, as_of_ms=at+2000)
    assert f.context['quantity'] == '0' and f.context['canonical_wallet'] == '4998.9814455'


def _reported_io(f, monkeypatch):
    """Isolate venue GETs and subprocess transport; run both original collectors."""
    from tools import demo_executor_origin as origin, demo_executor_statement as statement
    from tools import demo_executor_stream as stream_tool, demo_resident_recovery as custody
    from live.exchange import demo_network
    from tests.tools.test_binance_demo_execution_origin import transport, NOW
    from tests.tools.test_binance_demo_statement import transport as statement_transport

    # Collect the actual closing observation after its existing five-second
    # cash boundary; never move the already signed opening or original fills.
    f.clock = [f.now_ms + 4000]
    f.read_calls = []
    f.trades = []
    f.income = []
    for trade_id, order_id, side, price, fee, pnl, at in (
        (7,123,'BUY','100.1','0.0045045','0',f.now_ms+13),
        (8,124,'SELL','90','0.00405','-1.01',f.now_ms+2013)):
        f.trades.append(dict(symbol='XAUUSDT',id=trade_id,orderId=order_id,side=side,positionSide='BOTH',
            price=price,qty='0.1',quoteQty=str(Decimal(price)*Decimal('0.1')),realizedPnl=pnl,
            commission=fee,commissionAsset='USDT',time=at,maker=False))
        f.income.append(dict(symbol='XAUUSDT',incomeType='COMMISSION',income='-'+fee,asset='USDT',
            time=at,tranId=trade_id,tradeId=str(trade_id)))
        if pnl != '0':
            f.income.append(dict(symbol='XAUUSDT',incomeType='REALIZED_PNL',income=pnl,asset='USDT',
                time=at,tranId=9,tradeId=str(trade_id)))

    def gold_response(raw):
        text = raw.decode().replace('USDC','TEMPQUOTE').replace('USDT','USDC')
        value = json.loads(text.replace('TEMPQUOTE','USDT').replace('BTCUSDT','XAUUSDT'))
        def change(item):
            if type(item) is int and NOW-86400000 <= item <= NOW+86400000:
                return item + f.clock[0] - NOW
            if type(item) is list:
                return [change(v) for v in item]
            if type(item) is dict:
                result = {k:change(v) for k,v in item.items()}
                if result.get('asset') == 'USDT':
                    for k in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):
                        if k in result:result[k] = f.context['canonical_wallet']
                if result.get('filterType') == 'MIN_NOTIONAL':result['notional'] = '5'
                if 'contractType' in result:result.update(baseAsset='XAU',contractType='TRADIFI_PERPETUAL')
                if 'markPrice' in result:result.update(markPrice='90',indexPrice='90')
                if 'bidPrice' in result:result.update(bidPrice='90',askPrice='90.1')
                return result
            return item
        return change(value)

    monkeypatch.setattr(custody, 'require_custody', lambda: f.root)
    for tool in (origin, statement, stream_tool):
        monkeypatch.setattr(tool, 'fixed_account', lambda: (f.root,f.account))
    monkeypatch.setattr(origin, 'now_ms', lambda: f.clock[0])
    monkeypatch.setattr(statement, 'now_ms', lambda: f.clock[0])
    monkeypatch.setattr(reported, 'now_ms', lambda: f.clock[0])
    monkeypatch.setattr(reported, 'time', SimpleNamespace(sleep=lambda seconds:f.clock.__setitem__(0,f.clock[0]+int(seconds*1000))))
    monkeypatch.setattr(demo_network, 'opener', lambda: object())
    def child(command, **kwargs):
        if command[3] == 'tools.demo_executor_origin':
            get, _ = transport()
            def origin_get(path, **args):
                args.pop('opener')
                f.read_calls.append(('origin',path))
                return json.dumps(gold_response(get(path,**args))).encode()
            with monkeypatch.context() as patch:
                patch.setattr(origin.original, 'get_response', origin_get)
                value = origin.capture_worker('XAUUSDT')
        else:
            assert command[3] == 'tools.demo_executor_statement'
            get = statement_transport([])
            def statement_get(path, **args):
                args.pop('opener')
                f.read_calls.append(('statement',path))
                if path == '/fapi/v1/userTrades':value = f.trades
                elif path == '/fapi/v1/income':value = f.income
                elif path == '/fapi/v1/time':value = {'serverTime':f.clock[0]}
                else:
                    # Statement metadata is the same synthetic venue declaration.
                    value = json.loads(get(path,**args))
                    value = json.loads(json.dumps(value).replace('BTCUSDT','XAUUSDT'))
                    if path == '/fapi/v1/exchangeInfo':
                        item=value['symbols'][0];item.update(contractType='TRADIFI_PERPETUAL',baseAsset='XAU')
                        for item in item['filters']:
                            if item['filterType']=='MIN_NOTIONAL':item['notional']='5'
                return json.dumps(value).encode()
            with monkeypatch.context() as patch:
                patch.setattr(statement.original, 'get_response', statement_get)
                value = statement.capture_worker('XAUUSDT',int(command[command.index('--start-ms')+1]),
                    int(command[command.index('--end-ms')+1]),day_replay=True)
        return SimpleNamespace(stdout=json.dumps(value).encode())
    # Replace only these modules' external child process boundary, not global subprocess.
    for tool in (origin,statement):
        monkeypatch.setattr(tool,'subprocess',SimpleNamespace(run=child,TimeoutExpired=TimeoutError))


@contextmanager
def completed_feedback(case, original_evidence, tmp_path, monkeypatch, *, automatic=True, source_commit="c"*40):
    observed = {}
    callback = _auto_episode(original_evidence, monkeypatch, observed) if automatic else None
    f = completed_bar_entry(case, original_evidence, tmp_path, monkeypatch, prepare_episode=callback,
        source_commit=source_commit)
    try:
        from tools.gold_bar_worker_inputs import load
        f.base_runtime = f.proof['runtime']
        f.source_commit = f.base_runtime['source_commit']
        f.account = f.base_policy['account_ref_sha256']
        if automatic:
            f.episode = observed['episode']; f.engine = f.episode.engine
            f.entry_decision_ms = f.episode.entry_decision_ms
            f.opening = f.episode.account_opening
        else:
            f.engine, _ = load(f.root, f.base_runtime, source_commit=f.source_commit, uid=os.getuid())
            f.entry_decision_ms = f.base_runtime['entry_decision_ms']
        _finish_exit(f, monkeypatch)
        _reported_io(f, monkeypatch)
        from tests.tools.test_gold_runtime_evidence import private_tree
        private_tree(f.root)
        import time
        monkeypatch.setattr(time, 'time_ns', lambda:f.clock[0]*1000000)
        monkeypatch.setattr(time, 'sleep', lambda secs:f.clock.__setitem__(0,f.clock[0]+round(secs*1000)))
        _settled_frame(f, monkeypatch)
        f.exported = gold_bar_feedback.export_completed(f.root, bundle=f.base_runtime,
            engine=f.engine, entry_decision_ms=f.entry_decision_ms,
            bundle_sha256=_digest(f.base_runtime), account=f.account, source_commit=f.source_commit,
            stores=dict(f.stores, stream=f.stream), as_of_ms=f.now_ms + 4000)
        _retain_settled_result(f)
        yield f
    finally:
        f.events.close()
        f.stream.__exit__(None, None, None)
        for store in f.stores.values():
            store.close()


def _auto_episode(original_evidence, monkeypatch, observed, *, bundle=None, rows=None, context=None):
    """Return a before-sign callback using original AUTO history and forward capture."""
    def capture(*, root, stores, runtime, now_ms):
        from tools import gold_bar_capture as public, gold_bar_network, gold_bar_observe as observer
        from tools import gold_bar_cash as cash, gold_strategy_cash as wallet
        from tools.gold_bar_fresh_inputs import FreshEpisode
        from live.exchange import demo_network, binance_demo_exact as transport
        from tests.tools.test_binance_demo_cash_window import transport as wallet_transport, Clock
        from tests.tools.test_gold_strategy_entry import origin_at
        from tests.research.test_gold_bar_dataset import raw_rows, STEP
        from tests.tools.test_gold_runtime_evidence import private_tree
        import shutil
        # The runtime exists before original delegation, result, claim and
        # exact entry signing. The old unrelated fixture observation is not used.
        selected_bundle=deepcopy(bundle) if bundle is not None else dict(schema='GOLD_BAR_AUTO_EPISODE_INPUT_V1',source_commit=runtime['source_commit'],
            trial_manifest_sha256=runtime['trial_manifest_sha256'],archive_source_commit=runtime['archive_source_commit'],
            candidate='channel',seconds=20,episode_id='e'*64)
        for name in ('strategy-bar-observation-work','strategy-bar-observations','strategy-bar-episodes'):
            if (root/name).exists():shutil.rmtree(root/name)
        f=SimpleNamespace(root=root,account=origin_at(now_ms)['account_ref_sha256'],now_ms=now_ms,
            context=deepcopy(context) if context is not None else {'canonical_wallet':'5000'})
        _reported_io(f, monkeypatch)
        f.clock[0]=now_ms-9000
        monkeypatch.setattr(transport,'_credentials',lambda _:('synthetic-key','synthetic-secret'))
        # Fixed native stores remain original; helper-created .db filenames are
        # the only test path substitute (production opens fixed .sqlite3 read-only).
        monkeypatch.setattr(cash,'_opening_stores',lambda stack, actual:dict(zip(('dispatch','recovery','oms'),stores)))
        monkeypatch.setattr(cash.time,'time_ns',lambda:f.clock[0]*1000000)
        monkeypatch.setattr(cash.time,'sleep',lambda secs:f.clock.__setitem__(0,f.clock[0]+round(secs*1000)))
        class WalletClock(Clock):
            def now(self):return f.clock[0]
            def sleep(self,secs):f.clock[0]+=int(secs*1000)
        wc=WalletClock();wc.ms=f.clock[0]
        def collect(account, **kw):
            assert account==f.account
            wc.ms=f.clock[0]
            get = wallet_transport(wc,[])
            def wallet_get(path, **kwargs):
                value = json.loads(get(path, **kwargs))
                if path == '/fapi/v3/account':
                    for item in value['assets']:
                        if item['asset'] == 'USDT':
                            for name in ('walletBalance', 'balance', 'crossWalletBalance', 'availableBalance'):
                                if name in item:item[name] = f.context['canonical_wallet']
                return json.dumps(value).encode()
            return wallet.collector.collect(credential_file=None,operator_read=True,observe_seconds=30,
                credential_reader=lambda _:('synthetic-key','synthetic-secret'),get=wallet_get if context is not None else get,
                clock_ms=lambda:f.clock[0],sleep=wc.sleep,**kw)
        monkeypatch.setattr(wallet,'_collect',collect)
        history_rows = rows if rows is not None else raw_rows()
        raw=json.dumps(history_rows).encode()
        class Response:
            headers={}
            def __init__(self,url):self.url=url
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def geturl(self):return self.url
            def read(self,limit):assert len(raw)<limit;return raw
        class HTTP:
            def open(self,request,timeout):return Response(request.full_url)
        monkeypatch.setattr(gold_bar_network,'opener',lambda:HTTP())
        monkeypatch.setattr(demo_network,'proxy_url',lambda:None)
        monkeypatch.setattr(demo_network,'tls_context',lambda:object())
        end=history_rows[-1][6]+1
        message=json.dumps(dict(stream='xauusdt@kline_15m',data=dict(e='kline',E=now_ms-1,s='XAUUSDT',
            k=dict(t=end,T=end+STEP-1,s='XAUUSDT',i='15m',x=True,o='100',h='110',l='99',c='109'))))
        class Socket:
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def recv(self):f.clock[0]=now_ms-1;return message
        monkeypatch.setattr(observer,'connect',lambda *a,**kw:Socket())
        old=os.umask(0o077)
        try:
            private_tree(root)
            episode=FreshEpisode(root,selected_bundle,source_commit=selected_bundle['source_commit'],uid=os.getuid(),cash_account=f.account)
        finally:os.umask(old)
        observed['episode']=episode
        observed['capture']=f
        return episode.engine,episode.entry_decision_ms,selected_bundle
    return capture


def _retain_auto_exit(f, *, at):
    from tools.gold_bar_episode_tail import EpisodeTail
    from tools.gold_bar_demo_cycle import prepare
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_strategy_worker import original_pilot_window
    runtime=f.base_runtime
    tail=EpisodeTail(f.root,bundle_sha256=_digest(runtime),engine=f.episode.engine,
        entry_decision_ms=f.entry_decision_ms)
    engine,continuation,_=tail.snapshot()
    origin=held_bar_origin(f,at,price='90')
    decision=prepare(engine=engine,entry_decision_ms=f.entry_decision_ms,origin=origin,**f.stores,now_ms=at)
    assert decision['status']=='DECISION_REQUIRES_APPROVAL' and decision['action']=='REDUCE_LONG'
    decision.update(source_commit=f.source_commit,bundle_sha256=_digest(runtime),
        account_ref_sha256=f.account,origin_sha256=_digest(origin),bar_continuation_sha256=continuation)
    original=original_context(f.root,runtime,uid=os.getuid(),account=f.account)
    with Retention(f.root,original=original,base_runtime=runtime,uid=os.getuid(),role='executor') as store:
        claim=store.claim(bundle=runtime,now_ms=at);store.put(claim,'origin',origin);store.put(claim,'decision',decision)
        result=dict(f.proof['result'],decision=decision,decision_sha256=_digest(decision),
            original_pilot_window=original_pilot_window(f.root,decision=decision,source_commit=f.source_commit,account=f.account))
        store.put(claim,'result',result)
        f.exit_claim=claim
    return result


def _settled_frame(f, monkeypatch):
    """Real third observation and native cycle, before cash feedback is built."""
    from tools.gold_bar_demo_cycle import prepare
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention
    from tools.demo_executor_origin import capture_for_pilot
    from tools import demo_executor_origin as origin_capture
    # Original account fixture virtualizes executor UID globally; this retained
    # SQLite fixture is owned by the real temporary-directory UID.
    monkeypatch.setattr(origin_capture, 'os', SimpleNamespace(geteuid=os.getuid))
    from tools.gold_bar_episode_tail import EpisodeTail
    from tools.gold_strategy_worker import require_current_decision
    original=original_context(f.root,f.base_runtime,uid=os.getuid(),account=f.account)
    with Retention(f.root,original=original,base_runtime=f.base_runtime,uid=os.getuid(),role='executor') as retained:
        f.claim=retained.claim(bundle=f.base_runtime,now_ms=f.clock[0])
        f.settled_origin=capture_for_pilot(symbol='XAUUSDT',source_commit=f.source_commit,
            retention=retained,claim=f.claim)
        if f.base_runtime['schema']=='GOLD_BAR_AUTO_EPISODE_INPUT_V1':
            f.engine,continuation,_=EpisodeTail(f.root,bundle_sha256=_digest(f.base_runtime),engine=f.episode.engine,
                entry_decision_ms=f.entry_decision_ms).snapshot()
        else:continuation=None
        decision=prepare(engine=f.engine,entry_decision_ms=f.entry_decision_ms,origin=f.settled_origin,
            **f.stores,now_ms=f.clock[0])
        assert decision['status']=='STRATEGY_EPISODE_SETTLED',decision
        decision.update(source_commit=f.source_commit,bundle_sha256=_digest(f.base_runtime),
            account_ref_sha256=f.account,origin_sha256=_digest(f.settled_origin))
        if continuation is not None:decision['bar_continuation_sha256']=continuation
        require_current_decision(decision)
        retained.put(f.claim,'decision',decision)
        f.settled_decision=decision


def _retain_settled_result(f):
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_strategy_worker import original_pilot_window,require_current_decision
    from live.execution.demo_issuer_store import IssuerStore
    decision=f.settled_decision
    require_current_decision(decision)
    f.result=dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1',status='DECISION_RETAINED',source_commit=f.source_commit,
        bundle_sha256=_digest(f.base_runtime),decision_sha256=_digest(decision),decision=decision,
        submission_enabled=False,economic_authority=False,feedback=f.exported,
        original_pilot_window=original_pilot_window(f.root,decision=decision,source_commit=f.source_commit,account=f.account))
    original=original_context(f.root,f.base_runtime,uid=os.getuid(),account=f.account)
    with Retention(f.root,original=original,base_runtime=f.base_runtime,uid=os.getuid(),role='executor') as retained:
        retained.put(f.claim,'result',f.result)
        f.result_bytes=retained.read_claim(f.claim,'result')
        f.executor_checkpoint=retained.metadata()
    f.executor_root=f.root
    f.controller_root=f.root.parent/'controller';f.controller_root.mkdir(mode=0o700)
    f.current_runtime=f.base_runtime
    f.executor_uid=os.getuid()
    f.artifact=json.loads(Path(f.exported['path']).read_bytes())
    f.artifact_bytes=Path(f.exported['path']).read_bytes()
    with closing(IssuerStore(f.ledger,read_only=True)) as issuer:f.issuer_rows=issuer.snapshot()
