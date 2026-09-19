"""Offline whole-batch funding review; creates no reservation and cannot submit orders."""
import argparse
import json
import os
from pathlib import Path
import time

from services.data.kraken_public import require
from services.portfolio.kraken_funding import review
from tools.kraken_inventory_report import load, sealed
from tools.kraken_public_capture import digest


def build(account, requirements, drafts, *, as_of_ms):
    sealed(account)
    require(isinstance(drafts, list) and 1 <= len(drafts) <= 32, 'KRAKEN_FUNDING_DRAFTS')
    bindings = {}
    for draft in drafts:
        require(isinstance(draft, dict) and isinstance(draft.get('client_order_id'), str),
                'KRAKEN_FUNDING_DRAFT_ID')
        oid = draft['client_order_id']
        require(oid not in bindings, 'KRAKEN_FUNDING_DUPLICATE_DRAFT')
        bindings[oid] = digest(draft)
    result = review(account, requirements, as_of_ms=as_of_ms)
    require(bindings == {o['client_order_id']: o['draft_sha256'] for o in requirements['orders']},
            'KRAKEN_FUNDING_DRAFT_BINDING')
    result.update(account_source_hash=account['result_hash'], requirements_hash=digest(requirements),
                  draft_hashes=bindings)
    result['result_hash'] = digest(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', type=Path, required=True)
    parser.add_argument('--requirements', type=Path, required=True)
    parser.add_argument('--drafts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = build(load(args.account), load(args.requirements), load(args.drafts),
                   as_of_ms=time.time_ns()//1_000_000)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(result, stream, sort_keys=True, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({'status': result['status'], 'output': str(args.output), 'submission_enabled': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
