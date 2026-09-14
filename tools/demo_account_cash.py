"""Match the completed canonical book to two fixed-account history reads.

Order recovery stays separate. This path requires an already completed OMS and
the original position context; it never repairs pending orders, submits orders,
or infers continuous private-stream coverage from a short receipt window.
"""
from contextlib import ExitStack
from decimal import Decimal
import hashlib
from pathlib import Path

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from live.execution.demo_position_context import load_context
from live.execution.demo_pre_submit import _state, _retain
from live.execution.demo_resident_recovery import _cycle_lease
from services.research.demo_account_recovery import reconcile
from services.research.demo_cash_chain_status import build_status, save
from services.research.demo_execution_journal import DemoExecutionJournal, REPLAY_SOURCE_SHA256, observations
from tools.demo_reported_recovery import capture_inputs, _read, now_ms
from tools.demo_resident_recovery import _open, require_custody, _check_path
from tools.demo_executor_stream import fixed_account


def compare(inputs,context,*,cycle_order_ids):
    report,ledger=reconcile(**inputs)
    require(report['status']=='REPORTED_ACCOUNT_MATCHED' and ledger is not None,'ACCOUNT_CASH_UNRESOLVED')
    require(report['ledger_state_hash']==context['ledger_state_hash'] and
        report['canonical_wallet']==context['canonical_wallet'] and report['final_quantity']==context['quantity']=='0',
        'ACCOUNT_CASH_CANONICAL_DIFFERENCE')
    historical=context.get('reported_account_prefix',{}).get('manual_order_ids',[])
    require(set(report['manual_order_ids'])==set(historical),'ACCOUNT_CASH_NEW_MANUAL_ORDER')
    require(2<=len(cycle_order_ids)<=3 and len(set(cycle_order_ids))==len(cycle_order_ids) and
        set(cycle_order_ids)<=set(report['system_order_ids']),'ACCOUNT_CASH_CYCLE_ORDER_SCOPE')
    current=[t for t in inputs['statements'][0]['trades'] if t['orderId'] in cycle_order_ids]
    require({t['side'] for t in current}=={'BUY','SELL'} and
        sum((Decimal(t['qty'])*(1 if t['side']=='BUY' else -1) for t in current),Decimal(0))==0,
        'ACCOUNT_CASH_CYCLE_NOT_ROUND_TRIP')
    return report,ledger,len(current)


def run(root,area,account,source_commit,*,strategy_id):
    require(require_custody()==root and fixed_account()==(root,account),'ACCOUNT_CASH_CUSTODY')
    require(type(strategy_id) is str and strategy_id.startswith('demo-functional-'),'ACCOUNT_CASH_CYCLE_SCOPE')
    with _cycle_lease(root/'resident-recovery'),ExitStack() as stack:
        stores=_open(stack,root);state=_state(stores['dispatch'],stores['recovery'],stores['oms'])
        context=load_context(state,symbol='BTCUSDC',account_ref=account,as_of_ms=now_ms())
        require(context['quantity']=='0','ACCOUNT_CASH_FLAT_REQUIRED')
        proofs=[]
        for reservation,source in zip(state['reservations'],context['sources'],strict=True):
            require(source['reservation_sha256']==reservation['record_hash'],'ACCOUNT_CASH_RESERVATION_SCOPE')
            plan=_read(root/'receipt-reconciliation/plans'/(source['plan_sha256']+'.json'),root,16_000_000)
            proof=_read(root/'evidence'/(plan['proposal_sha256']+'.json'),root,16_000_000)
            require(proof['orders']==reservation['payload']['orders'] and proof['context']==reservation['payload']['context'],
                    'ACCOUNT_CASH_ORIGINAL_PROOF')
            proofs.append(proof)
        orders=stores['oms'].orders(strategy_id=strategy_id)
        cycle_order_ids=[o['venue_order_id'] for o in orders]
        require(2<=len(orders)<=3 and all(o['status'] in ('FILLED','EXPIRED','CANCELED') for o in orders),
                'ACCOUNT_CASH_CYCLE_NOT_COMPLETE')
        inputs=capture_inputs(root,account,source_commit,opening=proofs[0]['origin'],
            system_orders=[o for p in proofs for o in p['orders']],adv_notional=context['adv_notional'],
            participation_rate=context['participation_rate'],stream=stores['stream'])
        _retain(area/'account-cash-inputs',dict(schema='DEMO_ACCOUNT_CASH_INPUTS_V1',inputs=inputs,
            source_commit=source_commit,cycle_sha256=area.name,context_sha256=_digest(context),orders_submitted=False))
        report,ledger,count=compare(inputs,context,cycle_order_ids=cycle_order_ids)
        require(_state(stores['dispatch'],stores['recovery'],stores['oms'])==state,'ACCOUNT_CASH_EXECUTOR_CHANGED')
        require(count==len(stores['oms'].fills(strategy_id=strategy_id)),'ACCOUNT_CASH_CYCLE_FILL_COUNT')
        batches=[]
        cycle_reservations={s['reservation_sha256'] for s,p in zip(context['sources'],proofs,strict=True) if p['strategy_id']==strategy_id}
        for source in context['sources']:
            if source['reservation_sha256'] not in cycle_reservations:continue
            require(source['manifest_hash'] is not None,'ACCOUNT_CASH_CYCLE_NATIVE_REQUIRED')
            path=root/'receipt-reconciliation/receipts/journals'/(source['manifest_hash']+'.sqlite3')
            _check_path(path,root.stat().st_uid,directory=False)
            with DemoExecutionJournal(path,read_only=True) as j:
                records,tail,_=j.read()
                require(tail==source['journal_tail'],'ACCOUNT_CASH_NATIVE_CHANGED')
                batches.append(dict(manifest_hash=j.manifest_hash,journal_tail=tail,source_commit=j.manifest['source_commit'],
                    orders_count=len(j.manifest['actions']),
                    trades_count=observations(j.manifest,records,j.parsed)['native_observations']['trades_count'],
                    coverage=j.manifest['native']['coverage']))
        require(len(batches)==len(cycle_reservations),'ACCOUNT_CASH_NATIVE_SCOPE')
        # This view shows cumulative accounting from the original collateral;
        # the included cycle batches identify just the finite test being closed.
        view=dict(schema='DEMO_CANONICAL_CASH_CHAIN_V1',environment='BINANCE_USDM_DEMO',scope='TECHNICAL_VALIDATION_ONLY',
            status='MATCHED_REPORTED_INPUTS',account_ref_sha256=account,symbol='BTCUSDC',quote_asset='USDC',
            observed_at_ms=now_ms(),cash_capture_sha256=_digest(inputs),replay_source_sha256=REPLAY_SOURCE_SHA256,
            projection_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            cash_input_status='REPORTED_ACCOUNT_MATCHED',cash_input_duplicates=0,issues=[],batches=batches,
            continuations=[dict(index=i,manifest_hash=b['manifest_hash'],status='MATCHED_CANONICAL_PREFIX') for i,b in enumerate(batches[1:],1)],
            canonical_wallet=report['canonical_wallet'],reported_wallet=report['reported_wallet'],wallet_residual='0',
            reconciliation=ledger.reconciliation_snapshot().as_canonical_dict(),positions=report['ledger_state']['positions'],
            ledger_state_hash=report['ledger_state_hash'],capacity_consumed=report['ledger_state']['capacity_consumed'],
            fee_comparison='MATCHED_REPORTED_TRADES',realized_pnl_comparison='MATCHED_REPORTED_TOTAL',
            source_verified=False,coverage_verified=False,authoritative=False,submission_enabled=False,strategy_pnl=None,
            account_reconciliation=report,limitations=['FIXED_ACCOUNT_TWO_REST_SWEEPS','PRIVATE_STREAM_GAPS_RETAINED',
                'CUMULATIVE_ORIGINAL_LEDGER_WITH_CYCLE_BATCH_SCOPE','TECHNICAL_VALIDATION_ONLY'])
        _retain(area/'cash-chain-reports',view)
        save(root/'resident-recovery/functional-cash/views',build_status(view))
        return dict(status='MATCHED_REPORTED_INPUTS',issues=[],final_quantity='0',fill_count=count,
                    wallet_residual='0',report_sha256=_digest(view),economic_authority=False)
