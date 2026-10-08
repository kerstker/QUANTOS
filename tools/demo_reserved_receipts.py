"""Bind/import existing Demo reservation and private-stream facts, offline.

No network, credentials, submission, or executor state mutation is performed.
All writable output is a separate technical input/report directory.
"""
import argparse
import json
from pathlib import Path

from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.demo_receipt_binding import import_reserved_receipts
from live.execution.demo_pre_submit import _retain
from services.research.demo_user_stream import UserStreamJournal
from services.research.demo_execution_journal import DemoExecutionJournal
from services.research.demo_cash_projection import project_cash_window
from tools.demo_execution_replay import _read
from tools.cache_io import atomic_write_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True)
    parser.add_argument('--dispatch',type=Path,required=True)
    parser.add_argument('--stream',type=Path,required=True)
    parser.add_argument('--start-ms',type=int,required=True)
    parser.add_argument('--end-ms',type=int,required=True)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--cash-window',type=Path)
    args=parser.parse_args(argv)
    latest=args.output_dir/'latest.json'
    if latest.resolve() in {p.resolve() for p in (args.prepared,args.dispatch,args.stream,args.cash_window) if p is not None}:
        parser.error('output must not overwrite source inputs')
    try:
        prepared=_read(args.prepared)
        if latest.resolve()==Path(prepared['proposal_path']).resolve():
            raise ValueError('output must not overwrite original proposal')
        with DemoDispatchStore(args.dispatch,read_only=True) as dispatch, UserStreamJournal(args.stream,read_only=True) as stream:
            report=import_reserved_receipts(prepared,dispatch=dispatch,stream=stream,start_ms=args.start_ms,end_ms=args.end_ms,
                source_commit=args.source_commit,output_directory=args.output_dir)
        report['canonical_cash']=None
        if args.cash_window and report['journal_path']:
            cash=_read(args.cash_window)
            with DemoExecutionJournal(report['journal_path'],read_only=True) as journal:
                report['canonical_cash']=project_cash_window(journal,cash)
            _retain(args.output_dir/'cash-inputs',cash)
        _retain(args.output_dir/'view-reports',report)
        atomic_write_json(latest,report)
        print(json.dumps({'status':report['status'],'issues':report['issues'],
            'receipt_records':report['replay']['journal_records'] if report['replay'] else 0,
            'cash_status':report['canonical_cash']['status'] if report['canonical_cash'] else 'NOT_EVALUATED',
            'submission_enabled':False,'strategy_pnl':None}))
        ready=report['replay'] is not None and report['replay']['projection_status']=='REPLAYED_TECHNICAL_ONLY'
        cash_ready=not args.cash_window or report['canonical_cash'] is not None and report['canonical_cash']['status']=='MATCHED_REPORTED_INPUTS'
        return 0 if ready and cash_ready else 2
    except Exception:
        print(json.dumps({'status':'FAILED','reason':'DEMO_RESERVED_INPUT_OR_STORE_INVALID','submission_enabled':False}))
        return 3


if __name__=='__main__':raise SystemExit(main())
