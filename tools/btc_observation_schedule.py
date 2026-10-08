"""Advance only explicitly frozen BTC research observations; never freeze or backfill."""
from __future__ import annotations
import time
import threading
from pathlib import Path
from quantos.runtime_paths import runtime_state_root
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.btc_forward_observation import run_forward
from tools.btc_factory_research import seal
from tools.cache_io import atomic_write_json


_SCHEDULE_MUTEX = threading.Lock()

def advance(root: Path, *, now_ms: int) -> dict:
    output=root/'data/cache/btc_forward_schedule'
    output.mkdir(parents=True,exist_ok=True)
    report=dict(schema='QUANTOS_BTC_OBSERVATION_SCHEDULE_V1',observed_at_ms=now_ms,
                status='NOT_CONFIGURED',attempts=[],authoritative=False,account_pnl_computed=False)
    with acquire_runtime_lock(output/'worker.lock', process_lock=_SCHEDULE_MUTEX) as acquired:
        if not acquired:
            return seal({**report,'status':'BUSY'})
        try:
            base=root/'data/cache/btc_forward'
            if base.is_symlink():
                raise ValueError('observation root must not be a symlink')
            attempts=[]
            if base.exists():
                for number in range(1,1001):
                    path=base/str(number)
                    if path.is_symlink() or (path/'freeze.json').is_symlink():
                        raise ValueError('observation attempt must not be a symlink')
                    if (path/'freeze.json').is_file():
                        attempts.append(number)
            if len(attempts)>8:
                raise ValueError('scheduled observation exceeds eight frozen experiments')
            for attempt in attempts:
                try:
                    result=run_forward({'attempt':attempt,'action':2})
                    report['attempts'].append({'attempt':attempt,'status':result['status'],
                                               'observed_at_ms':result['observed_at_ms']})
                except Exception as exc:
                    report['attempts'].append({'attempt':attempt,'status':'ERROR','error':str(exc)})
            from tools.ai_factory_forward import advance_factory
            report['factory_attempts']=advance_factory(root,now_ms=now_ms)
            if report['factory_attempts']:
                report['status']='ERROR' if any(r['status']=='ERROR' for r in report['factory_attempts']) else 'OBSERVING'
            if attempts:
                report['status']='ERROR' if report['status']=='ERROR' or any(r['status']=='ERROR' for r in report['attempts']) else 'OBSERVING'
        except (OSError,ValueError) as exc:
            report.update(status='ERROR',error=str(exc))
        report['finished_at_ms']=time.time_ns()//1000000
        document=seal(report)
        history=output/'history';history.mkdir(exist_ok=True)
        atomic_write_json(str(history/(document['result_hash']+'.json')),document,indent=2)
        atomic_write_json(str(output/'status.json'),document,indent=2)
        return document


def main() -> int:
    report=advance(runtime_state_root(),now_ms=time.time_ns()//1000000)
    print('BTC observation scheduler: '+report['status'])
    return 1 if report['status']=='ERROR' else 2 if report['status'] in ('NOT_CONFIGURED','BUSY') else 0


if __name__=='__main__':
    raise SystemExit(main())
