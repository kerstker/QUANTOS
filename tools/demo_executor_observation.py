"""Export a small read-only inventory from the fixed protected executor stores.

No keys, account payloads, orders, signing or research-source copying. The
research console receives only a separately copied unverified display record.
"""
import argparse
import json
import os
import re
import time

from services.research import demo_executor_observation as model
from services.research import demo_execution_origin, demo_oms_status, demo_cancel_status, demo_resident_status
from services.research.demo_user_stream import UserStreamJournal
from services.research.demo_oms_status import resolution_digest
from tools.demo_resident_recovery import require_custody, _check_path
from tools.demo_executor_stream import _budget
from tools.binance_demo_user_stream import writer_lock


def now_ms():return time.time_ns()//1_000_000


def _directory(root,relative):
    path=root
    for name in relative.split('/'):
        path=path/name
        _check_path(path,root.stat().st_uid,directory=True)
    count=size=0
    for entry in path.iterdir():
        _check_path(entry,root.stat().st_uid,directory=False)
        count+=1;size+=entry.stat().st_size
        if count>1000 or size>64*1024*1024:raise ValueError('EXECUTOR_OBSERVATION_INPUT_BUDGET')
    return path


def _row(name,raw,phase,at,counts=None):
    return {'name':name,'status':'AVAILABLE','phase':phase,'observed_at_ms':at,
        'report_sha256':resolution_digest(raw),'counts':counts}


def _display(root,name,relative,loader,at,account):
    empty={'name':name,'status':'NO_SNAPSHOT','phase':None,'observed_at_ms':None,'report_sha256':None,'counts':None}
    try:
        directory=_directory(root,relative)
        pointer=directory/'latest.json'
        if not pointer.exists() and not pointer.is_symlink():return empty
        try:raw=loader(directory)
        except FileNotFoundError:raise ValueError('EXECUTOR_OBSERVATION_DANGLING_POINTER') from None
        if name.startswith('origin_'):
            if raw['account_ref_sha256']!=account or raw['symbol']!=name[7:]:raise ValueError('EXECUTOR_ORIGIN_ACCOUNT')
            view=demo_execution_origin.assess(raw,as_of_ms=at)
            return _row(name,raw,view['status'],raw['observed_at_ms'])
        if raw['observed_at_ms']>at:raise ValueError('EXECUTOR_OBSERVATION_FUTURE')
        return _row(name,raw,raw['phase'],raw['observed_at_ms'])
    except FileNotFoundError:return empty
    except Exception:return dict(empty,status='CORRUPT_OR_UNREADABLE')


def run(*,source_commit):
    if type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}',source_commit) is None:
        raise ValueError('EXECUTOR_OBSERVATION_SOURCE')
    root=require_custody()
    with writer_lock(root/'observation.lock'):
        # Verify the original full chain and snapshot in the same read transaction.
        # The original stream remains private and is never copied to research.
        with UserStreamJournal(root/'user-stream.sqlite3',read_only=True) as journal:
            _budget(journal,for_write=False)
            snapshot=journal.snapshot(verify_full=True)
        at=now_ms();account=snapshot['account_ref_sha256']
        rows=[_row('private_stream',snapshot,snapshot['state'],snapshot['observed_at_ms'],
            {k:snapshot[k] for k in ('records','messages','unique_messages','gaps')})]
        for symbol in ('BTCUSDC','BTCUSDT'):
            rows.append(_display(root,'origin_'+symbol,'execution-origins/'+symbol,demo_execution_origin.load_latest,at,account))
        for name,relative,loader in (
            ('resident','resident-recovery/views',demo_resident_status.load_latest),
            ('oms','receipt-reconciliation/views',demo_oms_status.load_latest),
            ('cancellation','evidence/cancellations/views',demo_cancel_status.load_latest)):
            rows.append(_display(root,name,relative,loader,at,account))
        report=model.validate({'schema':model.SCHEMA,'environment':'BINANCE_USDM_DEMO','scope':'TECHNICAL_VALIDATION_ONLY',
            'phase':'RECORDS_OBSERVED' if all(r['status']=='AVAILABLE' for r in rows) else 'PARTIAL_RECORDS',
            'observed_at_ms':at,'account_ref_sha256':account,'source_commit':source_commit,'components':rows,
            'source_verified':False,'submission_enabled':False,'cash_reconciled':False,'economic_authority':False})
        destination=root/'observation-views';destination.mkdir(mode=0o700,exist_ok=True)
        _check_path(destination,root.stat().st_uid,directory=True)
        count=size=0
        for entry in destination.iterdir():
            _check_path(entry,root.stat().st_uid,directory=False)
            count+=1;size+=entry.stat().st_size
            if count>=1000 or size>=64*1024*1024:raise ValueError('EXECUTOR_OBSERVATION_OUTPUT_BUDGET')
        model.save(destination,report)
        return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source-commit',required=True)
    args=parser.parse_args(argv);previous=os.umask(0o077)
    try:
        report=run(source_commit=args.source_commit)
        print(json.dumps({k:report[k] for k in ('schema','phase','observed_at_ms','submission_enabled','cash_reconciled')}));return 0
    except Exception:
        print(json.dumps({'status':'INPUTS_UNRESOLVED','reason':'EXECUTOR_OBSERVATION_UNAVAILABLE','submission_enabled':False}));return 3
    finally:os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
