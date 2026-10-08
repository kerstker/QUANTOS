"""Read an existing issuer journal without opening signing or broker keys."""
import argparse
from contextlib import closing
from decimal import Decimal,localcontext
import json
import os
import stat

from live.execution.demo_authority import _read_root_owned_json
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import validate_policy,require
from live.execution.demo_issuer_store import IssuerStore
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research import demo_issuer_status as model
from services.validation import demo_submission
from tools import demo_issuer as boundary


@ledger_decimal_context()
def build_status(policy,registry,rows,*,source_commit,observed_at_ms):
    from live.execution.demo_gold_future_template import SCHEMA as FUTURE_SCHEMA
    if type(policy) is dict and policy.get('schema')==FUTURE_SCHEMA:
        return build_template_status(policy,registry,rows,source_commit=source_commit,observed_at_ms=observed_at_ms)
    validate_policy(policy);demo_submission.validate_registry(registry)
    require(policy['executor_release_commit']==source_commit and policy['registry_sha256']==demo_submission.registry_sha256(registry),
            'ISSUER_OBSERVATION_CONFIGURATION')
    keys=[k for k in registry['keys'] if (k['authority_id'],k['key_id'])==(policy['authority_id'],policy['key_id'])]
    require(len(keys)<=1,'ISSUER_OBSERVATION_KEYS');key=keys[0] if keys else None
    reservations=[r for r in rows if r['kind']=='RESERVED' and r['payload']['policy']['policy_id']==policy['policy_id']]
    require(all(r['payload']['policy']==policy for r in reservations),'ISSUER_OBSERVATION_POLICY_CHANGED')
    signed={r['proposal_sha256'] for r in rows if r['kind']=='SIGNED'}
    budgets=[]
    for purpose in ('submission','cancellation'):
        plans=[r['payload']['plan'] for r in reservations if r['payload']['plan']['purpose']==purpose]
        # Display aggregation retains every stored decimal digit, including
        # quantity × price products with more fractional digits than a limit.
        with localcontext() as context:
            context.prec=80
            total=sum((Decimal(p['notional']) for p in plans),Decimal(0))
            remaining=Decimal(policy['max_total_notional'])-total
        is_submit=purpose=='submission'
        budgets.append({'purpose':purpose,'reserved_grants':len(plans),
            'signed_grants':sum(p['proposal_sha256'] in signed for p in plans),
            'reserved_orders':sum(len(p['orders'] if is_submit else p['commands']) for p in plans),
            'max_grants':policy['max_grants' if is_submit else 'max_cancel_grants'],
            'max_orders':policy['max_orders' if is_submit else 'max_cancel_orders'],
            'reserved_notional':format(total,'f') if is_submit else None,
            'max_notional':policy['max_total_notional'] if is_submit else None,
            'remaining_notional':format(remaining,'f') if is_submit else None})
    raw={'schema':model.SCHEMA,'environment':'BINANCE_USDM_DEMO','scope':'TECHNICAL_VALIDATION_ONLY',
        'phase':'','observed_at_ms':observed_at_ms,'source_commit':source_commit,
        'account_ref_sha256':policy['account_ref_sha256'],'policy_id':policy['policy_id'],'policy_sha256':_digest(policy),
        'symbol':policy['scope']['symbol'],
        'valid_from_ms':max(policy['valid_from_ms'],key['valid_from_ms']) if key else policy['valid_from_ms'],
        'valid_until_ms':min(policy['valid_until_ms'],key['valid_until_ms']) if key else policy['valid_until_ms'],
        'key_status':key['status'] if key else 'MISSING','key_material_status':'NOT_CHECKED',
        'records':len(rows),'last_record_at_ms':rows[-1]['at_ms'] if rows else None,
        'journal_sha256':rows[-1]['record_hash'] if rows else None,'budgets':budgets,
        'source_verified':False,'submission_enabled':False,'cash_reconciled':False,'economic_authority':False}
    raw['phase']=model.phase(raw)
    return model.validate(raw)


@ledger_decimal_context()
def build_template_status(template,registry,rows,*,source_commit,observed_at_ms):
    from live.execution.demo_gold_future_template import validate_template,template_group_binding
    from live.execution.demo_issuer_group_continuity import reduce_groups
    validate_template(template);demo_submission.validate_registry(registry)
    require(template['executor_release_commit']==source_commit
        and template['registry_sha256']==demo_submission.registry_sha256(registry),'ISSUER_OBSERVATION_CONFIGURATION')
    group=template['budget_group'];state=reduce_groups(rows).get(group['group_id'])
    require(state is not None and state.get('template')==template and state['binding']==template_group_binding(template),
        'ISSUER_OBSERVATION_ACTIVE_GROUP')
    keys=[k for k in registry['keys'] if (k['authority_id'],k['key_id'])==(template['authority_id'],template['key_id'])]
    require(len(keys)<=1,'ISSUER_OBSERVATION_KEYS');key=keys[0] if keys else None
    reservations=[r for r in rows if r['kind']=='RESERVED'
        and r['payload']['policy'].get('budget_group',{}).get('group_id')==group['group_id']]
    signed={r['proposal_sha256'] for r in rows if r['kind']=='SIGNED'}
    budgets=[]
    for purpose in ('submission','cancellation'):
        is_submit=purpose=='submission';used=state[purpose]
        plans=[r['payload']['plan'] for r in reservations if r['payload']['plan']['purpose']==purpose]
        with localcontext() as context:
            context.prec=80
            remaining=Decimal(group['max_total_notional'])-used[2]
        budgets.append(dict(purpose=purpose,reserved_grants=used[0],reserved_orders=used[1],
            signed_grants=sum(p['proposal_sha256'] in signed for p in plans),
            max_grants=group['max_grants' if is_submit else 'max_cancel_grants'],
            max_orders=group['max_orders' if is_submit else 'max_cancel_orders'],
            reserved_notional=format(used[2],'f') if is_submit else None,
            max_notional=group['max_total_notional'] if is_submit else None,
            remaining_notional=format(remaining,'f') if is_submit else None))
    reserve=group['exit_reserve'];used=state['submission']
    with localcontext() as context:
        context.prec=80
        opening= max(Decimal(0),Decimal(group['max_total_notional'])-used[2]-Decimal(reserve['notional']))
    raw=dict(schema=model.SCHEMA_V2,environment='BINANCE_USDM_DEMO',scope='TECHNICAL_VALIDATION_ONLY',phase='',
        observed_at_ms=observed_at_ms,source_commit=source_commit,account_ref_sha256=template['account_ref_sha256'],
        template_id=template['template_id'],template_sha256=_digest(template),budget_basis='SHARED_GROUP',
        group_id=group['group_id'],group_binding_sha256=state['binding'],continuity_event_sha256=state['continuity_event_sha256'],
        exit_reserve=dict(reserve),opening_remaining=dict(grants=max(0,group['max_grants']-used[0]-reserve['grants']),
            orders=max(0,group['max_orders']-used[1]-reserve['orders']),notional=format(opening,'f')),
        symbol=template['scope']['symbol'],valid_from_ms=max(template['valid_from_ms'],key['valid_from_ms']) if key else template['valid_from_ms'],
        valid_until_ms=min(template['valid_until_ms'],key['valid_until_ms']) if key else template['valid_until_ms'],
        key_status=key['status'] if key else 'MISSING',key_material_status='NOT_CHECKED',records=len(rows),
        last_record_at_ms=rows[-1]['at_ms'] if rows else None,journal_sha256=rows[-1]['record_hash'] if rows else None,
        budgets=budgets,source_verified=False,submission_enabled=False,cash_reconciled=False,economic_authority=False)
    raw['phase']=model.phase(raw)
    return model.validate(raw)


def run(*,source_commit):
    boundary.verify_source(source_commit)
    registry=demo_submission.validate_registry(load_json_without_duplicate_keys(_read_root_owned_json(boundary.REGISTRY)))
    uid=registry['issuer_uid'];boundary.require_identity(uid)
    policy=load_json_without_duplicate_keys(_read_root_owned_json(boundary.POLICY))
    from live.execution.demo_gold_future_template import SCHEMA as FUTURE_SCHEMA,validate_template
    (validate_template if policy.get('schema')==FUTURE_SCHEMA else validate_policy)(policy)
    root=boundary.STATE;boundary._state_custody(root,uid)
    # Preinstalled private output only. Inspection never initializes a journal.
    destination=root/'views';info=destination.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==info.st_gid==uid and not info.st_mode&0o077,'ISSUER_OBSERVATION_CUSTODY')
    count=size=0
    for path in destination.iterdir():
        info=path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==info.st_gid==uid and not info.st_mode&0o077,'ISSUER_OBSERVATION_CUSTODY')
        count+=1;size+=info.st_size
    require(count<16000 and size<64*1024*1024,'ISSUER_OBSERVATION_CAPACITY')
    with closing(IssuerStore(root/'issuance.sqlite3',read_only=True)) as store:rows=store.snapshot()
    raw=build_status(policy,registry,rows,source_commit=source_commit,observed_at_ms=boundary.now_ms())
    model.save(destination,raw)
    return raw


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source-commit',required=True)
    args=parser.parse_args(argv);previous=os.umask(0o077)
    try:
        raw=run(source_commit=args.source_commit)
        print(json.dumps({k:raw[k] for k in ('schema','phase','observed_at_ms','submission_enabled')}));return 0
    except Exception:
        print(json.dumps({'status':'INPUTS_UNRESOLVED','reason':'ISSUER_OBSERVATION_UNAVAILABLE','submission_enabled':False}));return 3
    finally:os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
