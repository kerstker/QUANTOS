"""Collect paired, bounded public originals independently of research execution."""
import argparse
import asyncio
import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

from quantos.contracts.gold_collection_budget import for_duration
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_bar_session import (observe as observe_bars, restore as restore_bars, _cursor,
                                   LATE_OPEN_POLICY, _policy as validate_late_open_policy)
from tools.gold_cost_review import _read
from tools.gold_joint_capture import collect_joint
from tools.gold_quote_campaign import _save
from tools.gold_quote_session import run as observe_quotes, review as review_quotes
from tools.perpetual_replay_research import source_identity
from tools.perpetual_public_capture import (quote_transport_profile, session_quote_transport_name,
                                          RAW_GAP_TRANSPORT)

PLAN_SCHEMA = 'GOLD_PAIRED_PUBLIC_SESSION_PLAN_V1'
SUMMARY_SCHEMA = 'GOLD_PAIRED_PUBLIC_SESSION_SUMMARY_V1'
RETAINED_PLAN_SCHEMA = 'GOLD_PAIRED_PUBLIC_SESSION_PLAN_V2'
RETAINED_SUMMARY_SCHEMA = 'GOLD_PAIRED_PUBLIC_SESSION_SUMMARY_V2'
QUOTE_MARGIN_SECONDS = 30
BAR_MARGIN_SECONDS = 120


def _reason(values, seconds):
    bars, quotes = values
    if any(isinstance(value, BaseException) for value in values):
        return 'COLLECTOR_FAILED'
    if bars.get('status') != 'OBSERVED' or quotes.get('status') != 'RAW_QUOTES_COLLECTED':
        return 'COLLECTOR_INCOMPLETE'
    coverage = quotes.get('quote_coverage', {})
    first, last = coverage.get('first_received_at'), coverage.get('last_received_at')
    if type(first) is not int or type(last) is not int or last - first < seconds * 1000:
        return 'QUOTE_COVERAGE_BEFORE_REQUESTED_DURATION'
    if type(bars.get('last_received_at_ms')) is not int or bars['last_received_at_ms'] < last:
        return 'BAR_WATERMARK_BEFORE_FINAL_QUOTE'
    return 'REQUESTED_RAW_SPAN_COLLECTED'


async def run(history, output, *, seconds=30 * 86400, interval_seconds=10,
              segment_samples=120, collection_budget, quote_transport=None, late_open_policy=None):
    budget = for_duration(collection_budget, seconds)
    if budget is None:
        raise ValueError('PAIRED_PUBLIC_COLLECTION_BUDGET_REQUIRED')
    policy = validate_late_open_policy(late_open_policy)
    policy_options = dict(late_open_policy=policy) if policy is not None else {}
    selected_transport = None if quote_transport is None else quote_transport_profile(quote_transport)
    if selected_transport is not None:
        session_quote_transport_name(selected_transport)
    for_duration(budget, seconds + BAR_MARGIN_SECONDS)
    if (type(interval_seconds) is not int or not 1 <= interval_seconds <= 10
            or type(segment_samples) is not int or not 3 <= segment_samples <= 120):
        raise ValueError('PAIRED_PUBLIC_COLLECTION_CADENCE')
    history, output = Path(history).absolute(), Path(output).absolute()
    if any(path.resolve() != path for path in (history, output)):
        raise ValueError('PAIRED_PUBLIC_COLLECTION_PATH')
    warmup = _read(history, 'response.json')
    receipt = _read(history, 'receipt.json')
    _cursor(warmup, decode(receipt), **policy_options)
    existing = output.parent
    while not existing.exists():
        existing = existing.parent
    required = budget['maximum_quote_bytes'] + budget['maximum_bar_bytes'] + 128 * 1024**2
    if shutil.disk_usage(existing).free < required:
        raise ValueError('PAIRED_PUBLIC_COLLECTION_DISK_BUDGET')
    source = source_identity()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Keep the exact original history receipt, including its real capture clocks.
    for name, raw in (('history-response.json', warmup), ('history-receipt.json', receipt)):
        with (output / name).open('xb') as handle:
            os.chmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    plan = dict(schema=RETAINED_PLAN_SCHEMA if policy else PLAN_SCHEMA,
        seconds=seconds, quote_seconds=seconds + QUOTE_MARGIN_SECONDS,
        bar_seconds=seconds + BAR_MARGIN_SECONDS, interval_seconds=interval_seconds,
        segment_samples=segment_samples, collection_budget=budget, source=source,
        history_response_sha256=hashlib.sha256(warmup).hexdigest(),
        history_receipt_sha256=hashlib.sha256(receipt).hexdigest(),
        submission_enabled=False, economic_eligible=False,
        **(dict(quote_transport=selected_transport) if selected_transport is not None else {}), **policy_options)
    _save(output / 'plan.json', plan)
    stopped = threading.Event()
    cancelled = False
    try:
        values = await collect_joint(
            observe_bars(history, output / 'bars', seconds=plan['bar_seconds'],
                         collection_budget=budget, **policy_options),
            asyncio.to_thread(observe_quotes, output / 'quotes', seconds=plan['quote_seconds'],
                interval_seconds=interval_seconds, segment_samples=segment_samples,
                collection_budget=budget, cancel_event=stopped,
                **(dict(quote_transport=quote_transport) if quote_transport is not None else {})), stopped)
        reason = _reason(values, seconds)
    except asyncio.CancelledError:
        # collect_joint has already stopped and drained the original workers.
        cancelled = True
        values = [None, None]
        reason = 'CANCELLED'
    unchanged = source_identity() == source
    if not unchanged:
        reason = 'SOURCE_CHANGED'
    names = ('plan.json', 'history-response.json', 'history-receipt.json',
             'bars/summary.json', 'quotes/summary.json', 'quotes/failure.json')
    files = {name: hashlib.sha256(_read(output, name)).hexdigest()
             for name in names if (output / name).exists()}
    if reason == 'REQUESTED_RAW_SPAN_COLLECTED' and not {
            'bars/summary.json', 'quotes/summary.json'} <= files.keys():
        reason = 'TERMINAL_JOURNAL_EVIDENCE_MISSING'
    summary = dict(schema=RETAINED_SUMMARY_SCHEMA if policy else SUMMARY_SCHEMA,
        status='RAW_PAIRED_SESSION_COLLECTED'
        if reason == 'REQUESTED_RAW_SPAN_COLLECTED' else 'INCOMPLETE', reason=reason,
        seconds=seconds, collection_budget=budget, source=source, source_unchanged=unchanged,
        files=files, workers_terminal=True,
        errors=[type(value).__name__ if isinstance(value, BaseException) else None for value in values],
        replay_ready=False, economic_eligible=False, submission_enabled=False,
        limitations=['RAW_COLLECTION_NOT_COMPLETE_RESEARCH_INPUT', 'GAPS_RETAINED_NOT_FILLED',
                     'NO_INDEPENDENT_SOURCE_OR_RESULT_AUTHORITY'], **policy_options)
    _save(output / 'summary.json', summary)
    if cancelled:
        raise asyncio.CancelledError
    return summary


def review(directory):
    """Cold-read both complete original journals; never certify a strategy."""
    root = Path(directory).absolute()
    names = ('plan.json', 'summary.json', 'history-response.json', 'history-receipt.json',
             'bars/summary.json', 'quotes/summary.json')
    fixed = {name: _read(root, name) for name in names}
    plan, summary = decode(fixed['plan.json']), decode(fixed['summary.json'])
    retained = plan.get('schema') == RETAINED_PLAN_SCHEMA
    policy = validate_late_open_policy(plan.get('late_open_policy'))
    if ((retained and (policy != LATE_OPEN_POLICY or summary.get('late_open_policy') != policy))
            or (not retained and any('late_open_policy' in value for value in (plan, summary)))):
        raise ValueError('PAIRED_PUBLIC_COLLECTION_LATE_OPEN_POLICY_BINDING')
    budget = for_duration(plan.get('collection_budget'), plan.get('seconds'))
    if budget is None:
        raise ValueError('PAIRED_PUBLIC_COLLECTION_BUDGET_REQUIRED')
    for_duration(budget, plan['seconds'] + BAR_MARGIN_SECONDS)
    expected_files = {name: hashlib.sha256(raw).hexdigest() for name, raw in fixed.items() if name != 'summary.json'}
    if (plan.get('schema') != (RETAINED_PLAN_SCHEMA if retained else PLAN_SCHEMA)
            or summary.get('schema') != (RETAINED_SUMMARY_SCHEMA if retained else SUMMARY_SCHEMA)
            or summary.get('status') != 'RAW_PAIRED_SESSION_COLLECTED'
            or summary.get('reason') != 'REQUESTED_RAW_SPAN_COLLECTED'
            or summary.get('source_unchanged') is not True or summary.get('workers_terminal') is not True
            or summary.get('errors') != [None, None]
            or summary.get('files') != expected_files
            or summary.get('seconds') != plan['seconds']
            or summary.get('collection_budget') != budget or summary.get('source') != plan.get('source')
            or summary.get('replay_ready') is not False
            or any(value.get('submission_enabled') is not False or value.get('economic_eligible') is not False
                   for value in (plan, summary))
            or plan.get('quote_seconds') != plan['seconds'] + QUOTE_MARGIN_SECONDS
            or plan.get('bar_seconds') != plan['seconds'] + BAR_MARGIN_SECONDS
            or hashlib.sha256(fixed['history-response.json']).hexdigest() != plan.get('history_response_sha256')
            or hashlib.sha256(fixed['history-receipt.json']).hexdigest() != plan.get('history_receipt_sha256')):
        raise ValueError('PAIRED_PUBLIC_COLLECTION_BINDING')
    bars, quotes = restore_bars(root / 'bars'), review_quotes(root / 'quotes')
    bar_plan = decode(_read(root / 'bars', 'plan.json'))
    quote_plan = decode(_read(root / 'quotes', 'plan.json'))
    if any(value.get('late_open_policy') != policy if retained else 'late_open_policy' in value
           for value in (bars, bar_plan)):
        raise ValueError('PAIRED_PUBLIC_COLLECTION_LATE_OPEN_POLICY_CHANGED')
    expected_transport = plan.get('quote_transport', quote_transport_profile('FRESH_CADENCE_V1'))
    session_quote_transport_name(expected_transport)
    if quote_plan.get('quote_transport') != expected_transport:
        raise ValueError('PAIRED_PUBLIC_COLLECTION_TRANSPORT_CHANGED')
    if (any(value.get('source') != plan['source'] or value.get('collection_budget') != budget
            for value in (bars, quotes, bar_plan, quote_plan))
            or bar_plan.get('seconds') != plan['bar_seconds']
            or quote_plan.get('seconds') != plan['quote_seconds']
            or quote_plan.get('interval_seconds') != plan.get('interval_seconds')
            or quote_plan.get('segment_samples') != plan.get('segment_samples')
            or _read(root / 'bars', 'warmup.json') != fixed['history-response.json']
            or decode(_read(root / 'bars', 'warmup-receipt.json')) != decode(fixed['history-receipt.json'])):
        raise ValueError('PAIRED_PUBLIC_COLLECTION_JOURNAL_CHANGED')
    reason = _reason([dict(bars, status='OBSERVED'), dict(quotes, status='RAW_QUOTES_COLLECTED')], plan['seconds'])
    if reason != 'REQUESTED_RAW_SPAN_COLLECTED':
        raise ValueError('PAIRED_PUBLIC_COLLECTION_' + reason)
    if any(_read(root, name) != raw for name, raw in fixed.items()):
        raise ValueError('PAIRED_PUBLIC_COLLECTION_CHANGED_DURING_REVIEW')
    if {path.name for path in root.iterdir()} != {
            'plan.json', 'summary.json', 'history-response.json', 'history-receipt.json', 'bars', 'quotes'}:
        raise ValueError('PAIRED_PUBLIC_COLLECTION_EXTRA_ORIGINAL')
    return dict(schema='GOLD_PAIRED_PUBLIC_SESSION_REVIEW_V2' if retained else 'GOLD_PAIRED_PUBLIC_SESSION_REVIEW_V1',
        status='RAW_ORIGINALS_RECONSTRUCTED', seconds=plan['seconds'], source=plan['source'],
        collection_budget=budget, bars=bars, quotes=quotes, replay_ready=False,
        source_authenticity_proven=False, economic_eligible=False, submission_enabled=False,
        **(dict(late_open_policy=policy) if retained else {}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    observe = commands.add_parser('observe')
    observe.add_argument('--history-dir', type=Path, required=True)
    observe.add_argument('--output-dir', type=Path, required=True)
    observe.add_argument('--budget-file', type=Path, required=True)
    observe.add_argument('--seconds', type=int, default=30 * 86400)
    observe.add_argument('--interval-seconds', type=int, default=10)
    observe.add_argument('--segment-samples', type=int, default=120)
    observe.add_argument('--quote-transport', choices=('FRESH_CADENCE_V1', 'POOLED_CADENCE_V1', RAW_GAP_TRANSPORT))
    observe.add_argument('--late-open-policy', choices=(LATE_OPEN_POLICY,))
    inspect = commands.add_parser('review')
    inspect.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'review':
        result = review(args.directory)
    else:
        path = args.budget_file.absolute()
        result = asyncio.run(run(args.history_dir, args.output_dir, seconds=args.seconds,
            interval_seconds=args.interval_seconds, segment_samples=args.segment_samples,
            collection_budget=decode(_read(path.parent, path.name)), quote_transport=args.quote_transport,
            late_open_policy=args.late_open_policy))
    print(json.dumps(result), flush=True)
    return 0 if result['status'] != 'INCOMPLETE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
