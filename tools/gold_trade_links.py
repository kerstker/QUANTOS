"""Read-only declared trade/signal associations from a verified local replay.

No credentials, account queries, positions, profitability or order authority.
Output contains trade identifiers; keep it with the private source documents.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from services.research.gold_trade_links import associate
from services.research.gold_trade_observation import load_document, MAX_BYTES
from services.research.trial_ledger import TrialLedgerError
from tools.gold_signal_replay import verify_archive


def _read(path):
    with path.open('rb') as file:
        raw = file.read(MAX_BYTES+1)
    return raw, load_document(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fills', type=Path, required=True)
    parser.add_argument('--replay-dir', type=Path, required=True)
    parser.add_argument('--annotations', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        verified = verify_archive(args.replay_dir, include_report=True)
        fills_raw, fills = _read(args.fills)
        annotations_raw, annotations = _read(args.annotations)
        result = associate(fills, verified['report'], annotations,
                           as_of_ms=time.time_ns()//1000000)
        result.update(replay_verification=verified['status'], trial_id=verified['trial_id'],
            fills_input_sha256=hashlib.sha256(fills_raw).hexdigest(),
            annotations_input_sha256=hashlib.sha256(annotations_raw).hexdigest())
    except (OSError, ValueError, TypeError, KeyError, TrialLedgerError):
        print(json.dumps({'status':'REJECTED','reason':'INVALID_OR_UNVERIFIABLE_LINK_INPUT'}))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
