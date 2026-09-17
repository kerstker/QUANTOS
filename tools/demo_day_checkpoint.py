"""Fixed-account accounting capture for a new UTC day; no order operations."""
from contextlib import ExitStack

from live.execution.demo_day_checkpoint import prepare
from live.execution.demo_issuer_policy import require
from live.execution.demo_position_context import load_context
from live.execution.demo_pre_submit import _state, _retain, _preparation_lease
from live.execution.demo_resident_recovery import _cycle_lease
from tools.demo_executor_stream import fixed_account
from tools.demo_reported_recovery import capture_inputs, _read, now_ms
from tools.demo_resident_recovery import _open, require_custody
from tools.demo_operator_setup import canonical


def run(*,source_commit):
    root=require_custody();fixed_root,account=fixed_account()
    require(root==fixed_root,'DAY_CAPTURE_CUSTODY')
    with _cycle_lease(root/'resident-recovery'),ExitStack() as stack:
        stores=_open(stack,root)
        with _preparation_lease(stores['dispatch']):
            state=_state(stores['dispatch'],stores['recovery'],stores['oms'])
            context=load_context(state,symbol='BTCUSDC',account_ref=account,as_of_ms=now_ms())
            require(context['quantity']=='0','DAY_CAPTURE_FLAT_REQUIRED')
            if context['day_id']==now_ms()//86400000:
                return dict(status='CANONICAL_CONTEXT',context=context,orders_submitted=False)
            proofs=[]
            for reservation,source in zip(state['reservations'],context['sources'],strict=True):
                require(source['reservation_sha256']==reservation['record_hash'],'DAY_CAPTURE_RESERVATION_SCOPE')
                plan=_read(root/'receipt-reconciliation/plans'/(source['plan_sha256']+'.json'),root,16_000_000)
                proof=_read(root/'evidence'/(plan['proposal_sha256']+'.json'),root,16_000_000)
                require(proof['orders']==reservation['payload']['orders'] and proof['context']==reservation['payload']['context'],
                        'DAY_CAPTURE_ORIGINAL_PROOF')
                proofs.append(proof)
            inputs=capture_inputs(root,account,source_commit,opening=proofs[0]['origin'],
                system_orders=[o for p in proofs for o in p['orders']],adv_notional=context['adv_notional'],
                participation_rate=context['participation_rate'],stream=stores['stream'],day_replay=True)
            record=prepare(context=context,proofs=proofs,inputs=inputs,created_at_ms=now_ms(),source_commit=source_commit)
            require(_state(stores['dispatch'],stores['recovery'],stores['oms'])==state,'DAY_CAPTURE_EXECUTOR_CHANGED')
            require(len(canonical(record))<=16_000_000,'DAY_CAPTURE_BYTE_BUDGET')
            _retain(root/'receipt-reconciliation/day-checkpoints',record)
            # Re-read through the exact path used by risk/proposal preparation.
            current=load_context(state,symbol='BTCUSDC',account_ref=account,as_of_ms=now_ms())
            require(current['day_id']==record['day_id'],'DAY_CAPTURE_CONTEXT_UNAVAILABLE')
            return dict(status='CANONICAL_CONTEXT',context=current,orders_submitted=False)
