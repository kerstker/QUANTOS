"""Fixed-identity issuer administrative pipe; never an order-grant endpoint.

The root operator must durably retain its attempt before APPLY. Any existing
attempt is QUERY-only, including response loss. A missing original event is
UNKNOWN and never permission to initialize or sign again.
"""
import argparse
from contextlib import closing
import json
import os
import sys
import time

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_common import require
from live.execution import demo_issuer_group_continuity as continuity
from live.execution.demo_issuer_store import IssuerStore
from live.execution.demo_gold_continuity_materials import load_operation
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.validation import demo_submission as submission
from tools import demo_issuer as original


def configuration(source_commit):
    """QUERY needs source/issuer/store custody, not current policy or key."""
    original.verify_source(source_commit)
    registry=submission.validate_registry(decode(original._read_root_owned_json(original.REGISTRY)))
    original.require_identity(registry['issuer_uid'])
    original._state_custody(original.STATE,registry['issuer_uid'])
    return original.STATE,registry


def current_authorization(value,registry,at):
    from live.execution.demo_gold_future_template import SCHEMA,current_template_key
    from live.execution.demo_gold_budget_scope import is_scope,current_scope_key
    from live.execution.demo_issuer_policy import current_key,GOLD_BUDGET_POLICY_SCHEMAS
    if value.get('schema')==SCHEMA:return current_template_key(value,registry,at)
    if is_scope(value):return current_scope_key(value,registry,at)
    require(value.get('schema') in GOLD_BUDGET_POLICY_SCHEMAS,'ISSUER_ADMIN_INSTALLED_SCOPE')
    return current_key(value,registry,at)


def installed_authorization(registry,purpose,at):
    value=decode(original._read_root_owned_json(original.POLICY))
    current_authorization(value,registry,at)
    from live.execution.demo_gold_future_template import SCHEMA
    require((value['schema']==SCHEMA)==(purpose=='DELEGATION_V2_SIGN'),'ISSUER_ADMIN_INSTALLED_PURPOSE')
    return value


def load_admin_key(registry,authorization):
    return original._load_key_checked(registry,authorization,current_authorization)


def run(*,source_commit,purpose,input_fd=None,output=None):
    channel=sys.stdout if output is None else output;previous=os.umask(0o077)
    result=dict(schema='DEMO_GOLD_ISSUER_ADMIN_RESULT_V1',source_commit=source_commit,
        purpose=None,operation_sha256=None,status='REJECTED',record=None,event_sha256=None,anchor=None,artifact=None,
        submission_enabled=False,economic_authority=False)
    entered=False
    try:
        require(purpose in ('GROUP_CONTINUITY','DELEGATION_V2_SIGN'),'ISSUER_ADMIN_PURPOSE')
        fd=sys.stdin.fileno() if input_fd is None else input_fd
        original.private_pipe(fd);original.private_pipe(channel.fileno())
        request=decode(original.read_frame(fd,maximum=4096,deadline=time.monotonic()+15))
        _fields(request,'schema source_commit purpose operation_sha256 mode')
        require(request['schema']=='DEMO_GOLD_ISSUER_ADMIN_INPUT_V1' and request['source_commit']==source_commit
            and request['purpose']==purpose
            and request['mode'] in ('APPLY','QUERY'),'ISSUER_ADMIN_INPUT')
        submission._digest(request['operation_sha256'])
        result.update(purpose=request['purpose'],operation_sha256=request['operation_sha256'])
        query=request['mode']=='QUERY'
        entered=query
        root,registry=configuration(source_commit)
        with closing(IssuerStore(root/'issuance.sqlite3',read_only=query)) as store:
            if query:
                entered=True
                record=store.query_admin(request['operation_sha256'],purpose=request['purpose'])
                if record is None:
                    result['status']='UNKNOWN'
                else:result.update(status='FOUND',record=record)
            else:
                at=original.now_ms();authorization=installed_authorization(registry,request['purpose'],at)
                require(authorization['executor_release_commit']==source_commit,'ISSUER_ADMIN_INSTALLED_SOURCE')
                loaded=load_operation(request['operation_sha256'],source_commit=source_commit,
                    purpose=request['purpose'],registry=registry,role='issuer')
                require(_digest(loaded['operation'])==request['operation_sha256'],'ISSUER_ADMIN_OPERATION_CHANGED')
                rows=store.snapshot();arguments=dict(documents=loaded['documents'],registry=registry,rows=rows,now_ms=at)
                if request['purpose']=='GROUP_CONTINUITY':
                    continuity.authorize_group_change(loaded['operation'],installed_authorization=authorization,**arguments)
                else:continuity.authorize_delegation_sign(loaded['operation'],installed_template=authorization,**arguments)
                key=load_admin_key(registry,authorization)
                def recheck():
                    latest_root,latest_registry=configuration(source_commit)
                    require((latest_root,latest_registry)==(root,registry)
                        and installed_authorization(latest_registry,request['purpose'],original.now_ms())==authorization,
                        'ISSUER_ADMIN_CONFIGURATION_CHANGED')
                    require(load_operation(request['operation_sha256'],source_commit=source_commit,purpose=request['purpose'],
                        registry=latest_registry,role='issuer')==loaded,'ISSUER_ADMIN_PUBLIC_CHANGED')
                recheck();entered=True
                args=dict(documents=loaded['documents'],registry=registry,key=key,now_ms=at,clock_ms=original.now_ms,recheck=recheck)
                if request['purpose']=='GROUP_CONTINUITY':
                    record=store.group_change(loaded['operation'],installed_authorization=authorization,**args)
                else:record=store.sign_delegation(loaded['operation'],installed_template=authorization,**args)
                del key
                result.update(status='COMMITTED',record=record)
            if result['record'] is not None:
                record=result['record'];payload=record['payload']
                template=payload['future_template'] if record['kind'] in continuity.GROUP_KINDS else payload['artifact']['future_template']
                require(template['executor_release_commit']==source_commit,'ISSUER_ADMIN_QUERY_SOURCE')
                result.update(event_sha256=record['record_hash'],anchor=continuity.history_anchor(store.snapshot(),authorization=template),
                    artifact=payload['artifact'] if record['kind']=='DELEGATION_V2_SIGNED' else None)
        print(json.dumps(result,sort_keys=True,separators=(',',':')),file=channel,flush=True)
        return 0 if result['status'] in ('COMMITTED','FOUND') else 3
    except Exception:
        result.update(status='UNKNOWN' if entered else 'REJECTED',record=None,event_sha256=None,anchor=None,artifact=None)
        try:print(json.dumps(result,sort_keys=True,separators=(',',':')),file=channel,flush=True)
        except (OSError,ValueError):pass
        return 3
    finally:os.umask(previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source-commit',required=True)
    parser.add_argument('--purpose',required=True,choices=('GROUP_CONTINUITY','DELEGATION_V2_SIGN'))
    args=parser.parse_args(argv)
    return run(source_commit=args.source_commit,purpose=args.purpose)


if __name__=='__main__':raise SystemExit(main())
