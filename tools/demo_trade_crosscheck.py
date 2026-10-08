"""Read-only offline comparison of retained Demo statement and private events."""
import argparse
import json
import os
from pathlib import Path
import time

from services.research.demo_statement import load_statement
from services.research.demo_trade_crosscheck import compare_order
from services.research.demo_user_stream import UserStreamJournal


def inspect_order(*, statement_directory, stream_path, symbol, venue_order_id, start_ms, end_ms, as_of_ms):
    if not 0 < start_ms < end_ms <= as_of_ms or end_ms-start_ms > 150000:
        raise ValueError('CROSSCHECK_WINDOW_INVALID')
    statement = load_statement(statement_directory)
    with UserStreamJournal(stream_path, read_only=True) as stream:
        exported = stream.export_order_messages(start_ms=start_ms, end_ms=end_ms)
    result = compare_order(statement, messages=exported['messages'], symbol=symbol,
        venue_order_id=venue_order_id, stream_gaps=exported['gaps'], as_of_ms=as_of_ms)
    result.update(local_stream_start_ms=start_ms, local_stream_end_ms=end_ms,
        stream_account_ref_sha256=exported['account_ref_sha256'],
        stream_integrity=exported['integrity'], stream_preceding_gap=exported['preceding_gap'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--statement-directory', type=Path, required=True)
    parser.add_argument('--stream-path', type=Path, required=True)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--venue-order-id', required=True)
    parser.add_argument('--start-ms', type=int, required=True)
    parser.add_argument('--end-ms', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    # Never replace retained observations or an earlier result with this report.
    if args.output.exists() or args.output.is_symlink():
        parser.error('output must be a new diagnostic file')
    result = inspect_order(statement_directory=args.statement_directory, stream_path=args.stream_path,
        symbol=args.symbol, venue_order_id=args.venue_order_id, start_ms=args.start_ms,
        end_ms=args.end_ms, as_of_ms=time.time_ns()//1_000_000)
    # Exclusive creation also protects against a concurrent checker choosing the
    # same name. No execution/OMS/credential path is opened by this tool.
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        output.write(json.dumps(result, sort_keys=True, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ('schema', 'status', 'symbol', 'venue_order_id',
        'reported_quantity', 'reported_commission', 'fee_residual', 'issues',
        'account_binding', 'canonical_ledger_reconciled', 'orders_submitted')}))
    return 0 if result['status'] == 'OBSERVATIONS_MATCHED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
