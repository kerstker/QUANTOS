"""Read normalized cash facts and optionally cross-check declared fill totals.

No account access, network, credentials, ledger writes or trading permissions.
Output contains account references; keep it with the private source documents.
"""
import argparse
import hashlib
import json
from pathlib import Path

from services.research.gold_cash_observation import assess, reconcile
from services.research.gold_trade_observation import MAX_BYTES, load_document


def _read(path):
    with path.open('rb') as source:
        raw = source.read(MAX_BYTES + 1)
    return load_document(raw), hashlib.sha256(raw).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cash', type=Path, required=True)
    parser.add_argument('--fills', type=Path)
    args = parser.parse_args(argv)
    try:
        cash, cash_hash = _read(args.cash)
        report = {'status': 'CASH_OBSERVATION_ONLY', 'cash': assess(cash),
                  'cash_input_sha256': cash_hash, 'fills_input_sha256': None,
                  'crosscheck': None}
        mismatch = False
        if args.fills is not None:
            fills, fills_hash = _read(args.fills)
            checked = reconcile(fills, cash)
            report.update(crosscheck=checked, fills_input_sha256=fills_hash)
            mismatch = any(row['status'] == 'MISMATCH' for row in
                [*checked['commission_checks'].values(), checked['realized_pnl_check']])
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print(json.dumps({'status': 'REJECTED', 'reason': 'INVALID_CASH_OR_FILL_INPUT'}))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 2 if mismatch else 0


if __name__ == '__main__':
    raise SystemExit(main())
