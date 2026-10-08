"""Offline normalized gold quote comparison; no network or order authority.

A source hash binds a declaration, not an authenticated market feed. Session
state is supplied by the operator and is not independently verified here.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from services.research.gold_cross_market import Quote, compare_at
from services.research.gold_trade_observation import MAX_BYTES, load_document

HEADER = {'schema', 'external_contract', 'decision_ms', 'max_age_ms',
          'max_skew_ms', 'external_session_open', 'local', 'external', 'fx'}
FIELDS = {'venue', 'instrument', 'unit', 'bid', 'ask', 'event_ms',
          'received_ms', 'source_hash'}
NUMBER = re.compile(r'(?:0|[1-9][0-9]{0,63})(?:\.[0-9]{1,64})?\Z')


def _quotes(rows):
    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError('INVALID_ROWS')
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError('INVALID_ROW')
        amounts = {}
        for field in ('bid', 'ask'):
            value = row[field]
            if not isinstance(value, str) or NUMBER.fullmatch(value) is None:
                raise ValueError('INVALID_DECIMAL')
            amounts[field] = Decimal(value)
        result.append(Quote(**(row | amounts)))
    return result


def inspect(raw):
    d = load_document(raw)
    if not isinstance(d, dict) or set(d) != HEADER or d['schema'] != 'GOLD_CROSS_MARKET_INPUT_V1':
        raise ValueError('INVALID_HEADER')
    report = compare_at(*(_quotes(d[key]) for key in ('local', 'external', 'fx')),
        **{key: d[key] for key in ('external_contract', 'decision_ms', 'max_age_ms',
                                   'max_skew_ms', 'external_session_open')})
    report.update(input_sha256=hashlib.sha256(raw).hexdigest(),
                  external_session_evidence='OPERATOR_DECLARED_NOT_VERIFIED')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.input.open('rb') as source:
            report = inspect(source.read(MAX_BYTES + 1))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print(json.dumps({'status': 'REJECTED', 'reason': 'INVALID_CROSS_MARKET_INPUT'}))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0 if report['status'] == 'ALIGNED_QUOTES_ONLY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
