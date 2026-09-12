"""Replay existing batch journals against one cash window, without network.

Original SQLite inputs stay read-only. Full diagnostic reports are immutable;
optional display publication never grants submission or cash source authority.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import stat

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_execution_journal import DemoExecutionJournal
from services.research.demo_cash_chain import project_cash_chain, MAX_BATCHES
from services.research.demo_cash_chain_status import build_status, save, load_latest
from services.research.demo_cash_window import MAX_BYTES
from tools.cache_io import atomic_write_json


def _read(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):raise ValueError('REGULAR_INPUT_REQUIRED')
        body=f.read(MAX_BYTES+1)
    if len(body)>MAX_BYTES:raise ValueError('BOUNDED_INPUT_REQUIRED')
    return load_json_without_duplicate_keys(body)


def _retain(directory,report):
    body=(json.dumps(report,sort_keys=True,indent=2)+'\n').encode()
    if len(body)>MAX_BYTES:raise ValueError('BOUNDED_REPORT_REQUIRED')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/(hashlib.sha256(body).hexdigest()+'.json')
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(body);f.flush();os.fsync(f.fileno())
    except FileExistsError:
        if _read(path)!=report:raise ValueError('REPORT_IDENTITY_CONFLICT') from None
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal',type=Path,action='append',required=True)
    parser.add_argument('--cash-window',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--publish-dir',type=Path)
    args=parser.parse_args(argv)
    try:
        if not 1<=len(args.journal)<=MAX_BATCHES:raise ValueError('BOUNDED_BATCH_CHAIN_REQUIRED')
        inputs=[p.resolve(strict=True) for p in [*args.journal,args.cash_window]]
        if len(set(inputs))!=len(inputs):raise ValueError('UNIQUE_INPUTS_REQUIRED')
        for directory in (args.output_dir,args.publish_dir):
            if directory is not None and any(directory.resolve()==p or directory.resolve() in p.parents for p in inputs):
                raise ValueError('SEPARATE_OUTPUT_REQUIRED')
        cash=_read(args.cash_window)
        with ExitStack() as stack:
            journals=[stack.enter_context(DemoExecutionJournal(p,read_only=True)) for p in args.journal]
            report=project_cash_chain(journals,cash)
        path=_retain(args.output_dir/'reports',report)
        display=build_status(report)
        save(args.output_dir/'views',display)
        if args.publish_dir:
            if (args.publish_dir/'latest.json').exists() or (args.publish_dir/'latest.json').is_symlink():
                old=load_latest(args.publish_dir)
                if old['observed_at_ms']>display['observed_at_ms']:raise ValueError('DISPLAY_TIME_REVERSED')
            save(args.publish_dir,display)
        atomic_write_json(args.output_dir/'latest.json',{'report_sha256':display['report_sha256'],'report_file_sha256':path.stem})
        print(json.dumps({'status':report['status'],'issues':report['issues'],'batches':len(report['batches']),
            'submission_enabled':False,'source_verified':False,'cash_reconciled':False,'strategy_pnl':None}))
        return 0 if report['status']=='MATCHED_REPORTED_INPUTS' else 2
    except Exception:
        print(json.dumps({'status':'FAILED','reason':'CASH_CHAIN_INPUT_OR_OUTPUT_INVALID','submission_enabled':False}))
        return 3


if __name__=='__main__':raise SystemExit(main())
