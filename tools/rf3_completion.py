"""One bounded RF3 maturity handoff: public outcomes -> existing V2 evaluator.

No account access. No forecast backfill. An interrupted/failed claimed job is
retained for review, never silently replayed or overwritten by the next timer.
"""
import argparse
import fcntl
import hashlib
import json
import time
from pathlib import Path
from services.research import rf3_daily_forward as rf3
from services.research.rf3_forward_account import account_window, _verify_bundles
from tools.rf3_daily_forward import status as observation_status
from tools.rf3_forward_account import _load_plan, _load_bundles, run as evaluate_account
from tools.profitability_forward_market import capture_market
from tools.cache_io import atomic_write_json


def readiness(plan, observation, now):
    start,end=account_window(plan)
    mature=end+60001
    status=('BLOCKED_IDENTITY_DRIFT' if observation['identity_matches'] is not True else
            'BLOCKED_MISSING_PUBLICATIONS' if observation['gaps'] else
            'WAITING_MATURITY' if now<mature else
            'READY' if observation['published']==list(range(14)) else 'BLOCKED_MISSING_PUBLICATIONS')
    return dict(status=status,start_ms=start,end_ms=end,mature_at_ms=mature,authority=False,
                economic_eligible=False,order_submission=False)


def _exclusive(path,value):
    import os
    with path.open('x') as f:
        json.dump(value,f,sort_keys=True,allow_nan=False);f.flush();os.fsync(f.fileno())


def _files(root):
    result={}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():raise ValueError('linked job artifact')
        if p.is_file():result[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def advance(state_root, work_root, *, clock=lambda:time.time_ns()//1000000,
            capture=capture_market, evaluate=evaluate_account):
    state=Path(state_root);work=Path(work_root)
    for p in (state,work):
        if not p.is_absolute() or p.resolve()!=p or str(p).startswith(('/srv/','/opt/')):
            raise ValueError('isolated absolute paths required')
    if state==work or state in work.parents or work in state.parents:
        raise ValueError('non-overlapping observer and evaluation paths required')
    work.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (work/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'BUSY','order_submission':False}
        plan=_load_plan(state)
        report=readiness(plan,observation_status(state),clock())
        report['plan_hash']=plan['plan_hash']
        def publish():
            atomic_write_json(str(work/'status.json'),report)
            return report
        if report['status'].startswith('BLOCKED'):return publish()
        if (work/'claim.json').exists():
            claim=json.loads((work/'claim.json').read_text())
            if claim['plan_hash']!=plan['plan_hash']:raise ValueError('changed job identity')
            # A successful marker was written only after the existing evaluator
            # checked all cases. This is operational status, never new evidence.
            report['status']='BLOCKED_CLAIM_REQUIRES_REVIEW'
            if (work/'completed.json').is_file():
                done=json.loads((work/'completed.json').read_text())
                if (done['plan_hash']!=plan['plan_hash'] or done['evaluation_files']!=_files(work/'evaluation')
                        or done['state_files']!=_files(state)):
                    raise ValueError('completed input or output changed')
                report['status']='COMPLETED_TECHNICAL_ONLY'
            return publish()
        if report['status']!='READY':return publish()
        bundles=_load_bundles(state,plan)
        _verify_bundles(plan,bundles)
        _exclusive(work/'claim.json',dict(plan_hash=plan['plan_hash'],claimed_ms=clock(),order_submission=False))
        try:
            capture(work/'market',start=report['start_ms'],end=report['end_ms'],quote='USDC')
            result=evaluate(state,work/'market',work/'evaluation')
            if (result.get('status')!='TECHNICAL_VALIDATION_ONLY' or result.get('case_count')!=9
                    or any(result.get(k) is not False for k in ('authority','economic_eligible','order_submission'))):
                raise ValueError('incomplete evaluation')
            _exclusive(work/'completed.json',dict(plan_hash=plan['plan_hash'],completed_ms=clock(),case_count=9,authority=False,
                evaluation_files=_files(work/'evaluation'),state_files=_files(state)))
            report['status']='COMPLETED_TECHNICAL_ONLY'
        except Exception as exc:
            _exclusive(work/'failure.json',dict(reason=type(exc).__name__,plan_hash=plan['plan_hash'],order_submission=False))
            report['status']='BLOCKED_EVALUATION_FAILED'
        return publish()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-root',required=True);p.add_argument('--work-root',required=True)
    a=p.parse_args()
    try:r=advance(a.state_root,a.work_root)
    except Exception as exc:r={'status':'BLOCKED_INVALID_INPUT','reason':type(exc).__name__,'order_submission':False}
    print(json.dumps(r,sort_keys=True))
    return 1 if r['status'].startswith('BLOCKED') else 0

if __name__=='__main__':raise SystemExit(main())
