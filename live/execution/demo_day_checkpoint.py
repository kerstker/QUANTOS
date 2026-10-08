"""Accounting-only day checkpoints bound to an already recovered flat book.

Protected fixed-account capture and file custody belong to the caller. A
checkpoint does not enroll an operator, renew a mandate or reset risk budgets.
"""
from copy import deepcopy
from decimal import Decimal
import re

from services.research.ai_factory_store import digest as ledger_digest
from live.execution.demo_issuer_policy import require
from services.research.demo_account_recovery import reconcile_days, DAY_MS
from services.research.demo_origin_contract import contract_from_origin

SCHEMA = 'DEMO_DAY_CHECKPOINT_V1'


def prepare(*, context, proofs, inputs, created_at_ms, source_commit):
    require(type(created_at_ms) is int and type(source_commit) is str and
            re.fullmatch('[a-f0-9]{40}',source_commit),'DAY_CHECKPOINT_IDENTITY')
    require(proofs and inputs['opening']==proofs[0]['origin'] and
            inputs['system_orders']==[o for p in proofs for o in p['orders']], 'DAY_CHECKPOINT_ORIGINALS')
    require(context['quantity']=='0' and inputs['opening']['account_ref_sha256']==context['account_ref_sha256'] and
            inputs['opening']['symbol']==context['symbol'],'DAY_CHECKPOINT_FLAT_ACCOUNT')
    require(contract_from_origin(inputs['opening'])[0]==context['contract'] and
            Decimal(inputs['adv_notional'])==Decimal(context['adv_notional']) and
            Decimal(inputs['participation_rate'])==Decimal(context['participation_rate']), 'DAY_CHECKPOINT_TERMS')
    report,ledger=reconcile_days(**inputs)
    require(report['status']=='REPORTED_ACCOUNT_MATCHED' and ledger is not None,'DAY_CHECKPOINT_ACCOUNT_UNRESOLVED')
    require(ledger_digest(report['ledger_state'])==context['ledger_state_hash'] and
            report['canonical_wallet']==context['canonical_wallet'] and report['final_quantity']=='0',
            'DAY_CHECKPOINT_CANONICAL_DIFFERENCE')
    require(set(report['manual_order_ids'])==set(context.get('reported_account_prefix',{}).get('manual_order_ids',[])),
            'DAY_CHECKPOINT_NEW_MANUAL_ORDER')
    # No new deposit/funding is inferred or used to replenish a risk budget.
    require(not any(f['asset']==context['symbol'][3:] and f['incomeType'] in ('TRANSFER','FUNDING_FEE')
                    for f in inputs['statements'][0]['income']), 'DAY_CHECKPOINT_EXTERNAL_FLOW')
    day=inputs['closing']['completed_at_ms']//DAY_MS
    available=max(inputs['statements'][-1]['completed_at_ms'],inputs['closing']['completed_at_ms'],
                  max(w['end_ms'] for w in inputs['windows']),context['available_at_ms'])
    require(context['day_id']<day==created_at_ms//DAY_MS and available<=created_at_ms,
            'DAY_CHECKPOINT_TIME')
    return deepcopy(dict(schema=SCHEMA,day_id=day,created_at_ms=created_at_ms,source_commit=source_commit,
        sources=context['sources'],prefix_ledger_state_hash=context['ledger_state_hash'],
        account_inputs=inputs,account_report=report,risk_budget_reset=False,
        submission_enabled=False,economic_authority=False))


def validate(record, *, context, proofs, as_of_ms):
    require(record.get('schema')==SCHEMA and type(record.get('created_at_ms')) is int and
            record['created_at_ms']<=as_of_ms,'DAY_CHECKPOINT_FROM_FUTURE')
    expected=prepare(context=context,proofs=proofs,inputs=record['account_inputs'],
                     created_at_ms=record['created_at_ms'],source_commit=record['source_commit'])
    require(record==expected,'DAY_CHECKPOINT_REPLAY_CHANGED')
    return expected['account_report']
