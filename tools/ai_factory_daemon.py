"""Resident public-data observer and daily verified research checkpoint worker."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import threading
import time
from tools.ai_factory_forward import advance_factory
from tools.ai_factory_checkpoint import create_checkpoint,publish_checkpoint
from tools.cache_io import atomic_write_json
from tools.btc_factory_research import seal


def tick(root,*,now_ms):
    root=Path(root);reports=advance_factory(root,now_ms=now_ms)
    backup={'status':'NO_ACTIVE_MODELS'}
    if reports:
        day=time.strftime('%Y%m%d',time.gmtime(now_ms/1000))
        folder=root/'data/cache/ai_factory_checkpoints';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'daily-{day}.zip'
        try:
            if not path.exists():backup=create_checkpoint(root,path)
            else:backup={'status':'VERIFIED_DAILY_CHECKPOINT','day':day}
            publish_checkpoint(path,root/'data/cache/ai_factory_checkpoint.zip')
        except Exception as exc:backup={'status':'RETRY_REQUIRED','reason':type(exc).__name__}
    report=seal({'schema':'QUANTOS_AI_FACTORY_RESIDENT_V1','observed_at_ms':now_ms,'attempts':reports,
                 'checkpoint':backup,'status':'ERROR' if any(r['status']=='ERROR' for r in reports) else 'DEGRADED' if backup['status']=='RETRY_REQUIRED' else 'RUNNING',
                 'authoritative':False,'account_pnl_computed':False})
    path=root/'data/cache/ai_factory/resident.json';path.parent.mkdir(parents=True,exist_ok=True)
    atomic_write_json(str(path),report);return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root',required=True,type=Path)
    parser.add_argument('--interval',type=int,default=60)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args(argv)
    if not args.state_root.is_absolute() or args.state_root.is_symlink() or not 30<=args.interval<=300:
        parser.error('absolute state directory and interval 30..300 seconds required')
    os.environ['QUANTOS_STATE_ROOT']=str(args.state_root)
    stop=threading.Event()
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,lambda *_:stop.set())
    while not stop.is_set():
        try:
            report=tick(args.state_root,now_ms=time.time_ns()//1000000)
            print(json.dumps({'status':report['status'],'attempts':report['attempts'],'checkpoint':report['checkpoint']}),flush=True)
        except Exception as exc:
            print(json.dumps({'status':'ERROR','reason':type(exc).__name__}),flush=True)
            if args.once:return 1
        if args.once:return 0
        stop.wait(args.interval)
    return 0


if __name__=='__main__':raise SystemExit(main())
