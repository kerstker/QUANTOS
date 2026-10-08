"""Independent bounded public quote journal; no account or execution access."""
import argparse
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path

from quantos.contracts.gold_collection_budget import for_duration, plan_budget
from tools.perpetual_public_capture import (capture_gold_session, quote_transport_profile,
    session_quote_transport_name, RAW_GAP_TRANSPORT, POOLED_SESSION_TRANSPORTS)
from tools.perpetual_replay_research import source_identity
from services.execution.backtest_authority import exact_identity_hash
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_cost_review import _read

SEGMENT_PLAN = 'GOLD_PUBLIC_SEGMENTED_QUOTE_SESSION_PLAN_V1'
RETAINED_SEGMENT = 'GOLD_PUBLIC_RETAINED_QUOTE_SEGMENT_V1'
BUDGETED_SEGMENT_PLAN = 'GOLD_PUBLIC_SEGMENTED_QUOTE_SESSION_PLAN_V2'
BUDGETED_RETAINED_SEGMENT = 'GOLD_PUBLIC_RETAINED_QUOTE_SEGMENT_V2'
MAX_RECENT_RECEIPTS = 2048
TERMINAL_METADATA_BYTES = 65536


def _raw(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False)+'\n').encode()


def segment_records(segment):
    return [*(segment['bootstrap'][name] for name in ('exchange','funding','candles')),
        *(row[name] for row in segment['samples'] for name in ('mark','depth')),
        segment['exchange_after'],*segment['observed_funding_records'],*segment['capacity_observations']]


def _continuation_error(row, *, interval_seconds, maximum_cycles):
    from tools.gold_quote_transport import PATHS
    fields = {'kind', 'endpoint', 'attempt', 'observed_at', 'error_type', 'reason_type',
        'retry_scheduled', 'retry_profile', 'retry_delay_seconds', 'http_status',
        'continuation_cycle', 'continuation_scheduled', 'continuation_delay_seconds'}
    kinds = {'exchange': 'exchangeInfo', 'funding': 'fundingRate', 'candles': 'klines',
             'mark': 'premiumIndex', 'depth': 'depth'}
    if (type(row) is not dict or set(row) != fields
            or row['kind'] not in kinds or type(row['endpoint']) is not str
            or PATHS.fullmatch(row['endpoint']) is None
            or row['endpoint'].split('?', 1)[0] != '/fapi/v1/' + kinds[row['kind']]
            or row['retry_profile'] != RAW_GAP_TRANSPORT or row['error_type'] != 'URLError'
            or row['http_status'] is not None
            or row['reason_type'] not in ('TimeoutError', 'ConnectionError', 'SSLEOFError', 'gaierror')
            or type(row['observed_at']) is not int or row['observed_at'] <= 0
            or type(row['attempt']) is not int or row['attempt'] not in (1, 2)
            or type(row['continuation_cycle']) is not int
            or not 0 <= row['continuation_cycle'] < maximum_cycles
            or type(row['retry_scheduled']) is not bool
            or type(row['continuation_scheduled']) is not bool):
        raise ValueError('QUOTE_SESSION_CONTINUATION_ERROR')
    retry, continuing = row['retry_scheduled'], row['continuation_scheduled']
    wait = row['continuation_delay_seconds']
    if (retry and (continuing or row['attempt'] != 1
                   or row['retry_delay_seconds'] != .25 or wait is not None)
            or not retry and (not continuing or row['attempt'] != 2
                or row['retry_delay_seconds'] is not None or type(wait) not in (int, float)
                or not 0 < wait <= interval_seconds or row['continuation_cycle'] + 1 >= maximum_cycles)):
        raise ValueError('QUOTE_SESSION_CONTINUATION_SCHEDULE')
    return dict(kind=row['kind'], endpoint=row['endpoint'],
                cycle=row['continuation_cycle'] + int(continuing),
                attempt=1 if continuing else 2, observed_at=row['observed_at'])


def run(output, *, seconds=86400, interval_seconds=10, segment_samples=None,
        collection_budget=None, cancel_event=None, quote_transport=None):
    budget = for_duration(collection_budget, seconds)
    if quote_transport is not None:
        if budget is None:
            raise ValueError('QUOTE_TRANSPORT_EXPLICIT_BUDGET_REQUIRED')
        session_quote_transport_name(quote_transport_profile(quote_transport))
    if (type(interval_seconds) is not int or not 1 <= interval_seconds <= 10
            or budget is not None and segment_samples is None):
        raise ValueError('INVALID_QUOTE_SESSION_DURATION')
    if segment_samples is not None and (type(segment_samples) is not int or not 3<=segment_samples<=120):
        raise ValueError('INVALID_QUOTE_SEGMENT_SIZE')
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = source_identity()
    count = errors = size = 0
    chain = hashlib.sha256()
    manifest_hash = hashlib.sha256()
    manifest_count = 0
    if budget is not None:
        with (output / 'originals.jsonl').open('xb') as handle:
            os.chmod(handle.fileno(), 0o600)
    recent = OrderedDict()
    segments = 0

    def save(name, value):
        nonlocal size, manifest_count
        raw = _raw(value)
        terminal = budget is not None and name in ('failure.json', 'summary.json')
        maximum = budget['maximum_quote_bytes'] if budget else 512 * 1024 * 1024
        digest = hashlib.sha256(raw).hexdigest()
        binding = _raw(dict(name=name, bytes=len(raw), sha256=digest)) if budget and not terminal else b''
        if (terminal and len(raw) > TERMINAL_METADATA_BYTES
                or not terminal and size + len(raw) + len(binding) > maximum):
            raise ValueError('QUOTE_JOURNAL_BYTE_BUDGET')
        with (output / name).open('xb') as handle:
            os.chmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if not terminal:
            size += len(raw)
        if binding:
            with (output / 'originals.jsonl').open('ab') as handle:
                handle.write(binding)
                handle.flush()
                os.fsync(handle.fileno())
            manifest_hash.update(binding)
            manifest_count += 1
            size += len(binding)
        return digest

    def retain(row):
        nonlocal count
        if budget is not None and count >= budget['maximum_quote_receipts']:
            raise ValueError('QUOTE_JOURNAL_RECEIPT_BUDGET')
        digest = save(f'receipt-{count:06d}.json', row)
        if segment_samples is not None:
            recent[digest] = count
            recent.move_to_end(digest)
            if len(recent)>MAX_RECENT_RECEIPTS:
                recent.popitem(last=False)
        chain.update(bytes.fromhex(digest))
        count += 1

    def retain_error(row):
        nonlocal errors
        if budget is not None and errors >= budget['maximum_quote_receipts']:
            raise ValueError('QUOTE_JOURNAL_ERROR_BUDGET')
        save(f'error-{errors:06d}.json', row)
        errors += 1

    extra = dict(collection_budget=budget,
                 quote_transport=quote_transport_profile(quote_transport if quote_transport is not None
                                                          else 'FRESH_CADENCE_V1')) if budget else {}
    plan_sha = save('plan.json', dict(schema=BUDGETED_SEGMENT_PLAN if budget else
        SEGMENT_PLAN if segment_samples is not None else 'GOLD_PUBLIC_QUOTE_SESSION_PLAN_V1',
        seconds=seconds, interval_seconds=interval_seconds, source=source,
        receipt_chain='SHA256_CONCATENATED_ORDERED_FILE_SHA256_BYTES',
        submission_enabled=False, **extra, **(dict(segment_samples=segment_samples,
            maximum_segment_samples=240,maximum_terminal_extra_samples=2,
            dense_close_sampling=dict(clock='UTC_15M_CALENDAR',before_ms=10000,after_ms=20000,interval_ms=1000))
            if segment_samples is not None else {})))

    def retain_segment(segment):
        nonlocal segments
        if budget is not None and segments >= budget['maximum_quote_segments']:
            raise ValueError('QUOTE_JOURNAL_SEGMENT_BUDGET')
        if source_identity()!=source or segment['index']!=segments:
            raise ValueError('QUOTE_SEGMENT_SOURCE_OR_SEQUENCE_CHANGED')
        identity = {name:value for name,value in segment.items() if name!='segment_id'}
        if segment['segment_id']!=exact_identity_hash(identity,name='gold_raw_quote_segment'):
            raise ValueError('QUOTE_SEGMENT_IDENTITY_CHANGED')
        receipts=[]
        for row in segment_records(segment):
            digest=hashlib.sha256(_raw(row)).hexdigest()
            if digest not in recent:
                raise ValueError('QUOTE_SEGMENT_ORIGINAL_RECEIPT_MISSING')
            receipts.append(dict(index=recent[digest],sha256=digest))
        save(f'segment-{segments:06d}.json',dict(schema=BUDGETED_RETAINED_SEGMENT if budget else RETAINED_SEGMENT,
            source=source, parent_plan_sha256=plan_sha,segment=segment,receipts=receipts,
            **(dict(collection_budget=budget) if budget else {})))
        segments+=1
    try:
        result = capture_gold_session(seconds=seconds, interval_seconds=interval_seconds,
            on_record=retain,on_error=retain_error, **(dict(segment_samples=segment_samples,
            on_segment=retain_segment) if segment_samples is not None else {}),
            **(dict(collection_budget=budget) if budget else {}),
            **(dict(quote_transport=quote_transport) if quote_transport is not None else {}),
            **(dict(cancel_event=cancel_event) if cancel_event is not None else {}))
        if source_identity() != source:
            raise ValueError('QUOTE_SESSION_SOURCE_CHANGED')
        digest = save('capture.json', result)
        summary = dict(status='RAW_QUOTES_COLLECTED', capture_sha256=digest,
            sample_count=result['sample_count'], quote_coverage=result['quote_coverage'],
            replay_ready=False)
        if segment_samples is not None:
            if result['segment_count']!=segments:
                raise ValueError('QUOTE_SEGMENT_COUNT_CHANGED')
            summary.update(segment_count=segments,segments_replay_validated=False)
    except BaseException as exc:
        save('failure.json', dict(status='INCOMPLETE', error_type=type(exc).__name__,
            receipts=count, errors=errors, receipt_chain_sha256=chain.hexdigest(),
            source=source, submission_enabled=False,
            **(dict(collection_budget=budget, journal_data_bytes=size,
                    originals_sha256=manifest_hash.hexdigest(), original_files=manifest_count) if budget else {}),
            **(dict(segment_count=segments,segments_replay_validated=False)
                if segment_samples is not None else {})))
        raise
    summary.update(schema='GOLD_PUBLIC_QUOTE_SESSION_SUMMARY_V2' if budget else 'GOLD_PUBLIC_QUOTE_SESSION_SUMMARY_V1', receipts=count,
        errors=errors, receipt_chain_sha256=chain.hexdigest(), source=source,
        submission_enabled=False)
    if budget is not None:
        summary.update(collection_budget=budget, journal_data_bytes=size,
                       plan_sha256=plan_sha, originals_sha256=manifest_hash.hexdigest(),
                       original_files=manifest_count)
    save('summary.json', summary)
    return summary


def read_segment(directory,index):
    """Cold-read one fixed original segment and every bound receipt; no paths."""
    from services.execution.perpetual_public_replay import _record
    root=Path(directory).absolute()
    plan_raw = _read(root, 'plan.json')
    plan = decode(plan_raw)
    budget = plan_budget(plan, legacy_schema=SEGMENT_PLAN, budget_schema=BUDGETED_SEGMENT_PLAN)
    if budget is not None:
        session_quote_transport_name(plan.get('quote_transport'))
    if type(index) is not int or not 0 <= index < (budget['maximum_quote_segments'] if budget else 86403):
        raise ValueError('QUOTE_SEGMENT_INDEX')
    name=f'segment-{index:06d}.json'
    raw = _read(root, name)
    saved = decode(raw)
    if (plan.get('submission_enabled') is not False
            or saved.get('schema') != (BUDGETED_RETAINED_SEGMENT if budget else RETAINED_SEGMENT)
            or (saved.get('collection_budget') != budget if budget else 'collection_budget' in saved)
            or saved.get('source')!=plan.get('source')
            or saved.get('parent_plan_sha256')!=hashlib.sha256(plan_raw).hexdigest()):
        raise ValueError('QUOTE_SEGMENT_PLAN_BINDING')
    segment=saved['segment']
    body={key:value for key,value in segment.items() if key!='segment_id'}
    if (segment.get('schema')!='GOLD_PUBLIC_QUOTE_SEGMENT_V1' or segment.get('index')!=index
            or segment.get('submission_enabled') is not False
            or segment.get('economic_eligible') is not False or segment.get('replay_ready') is not False
            or segment.get('status')!='RAW_SEGMENT_NOT_REPLAY_VALIDATED'
            or segment.get('segment_id')!=exact_identity_hash(body,name='gold_raw_quote_segment')
            or type(segment.get('samples')) is not list or not 3<=len(segment['samples'])<=240
            or type(segment.get('observed_funding_records')) is not list
            or len(segment['observed_funding_records'])>240
            or type(segment.get('capacity_observations')) is not list
            or len(segment['capacity_observations'])>31):
        raise ValueError('QUOTE_SEGMENT_CONTENT')
    rows=segment_records(segment)
    bindings=saved.get('receipts')
    if type(bindings) is not list or len(bindings)!=len(rows):
        raise ValueError('QUOTE_SEGMENT_RECEIPT_COUNT')
    originals={}
    for binding,row in zip(bindings,rows):
        if (type(binding) is not dict or set(binding)!={'index','sha256'}
                or type(binding['index']) is not int or not 0 <= binding['index'] <
                    (budget['maximum_quote_receipts'] if budget else 1_000_000)):
            raise ValueError('QUOTE_SEGMENT_RECEIPT_INDEX')
        receipt_name=f"receipt-{binding['index']:06d}.json"
        original=_read(root,receipt_name)
        if hashlib.sha256(original).hexdigest()!=binding['sha256'] or decode(original)!=row:
            raise ValueError('QUOTE_SEGMENT_ORIGINAL_CHANGED')
        _record(row,row['kind'])
        originals[receipt_name]=original
    if (_read(root,'plan.json')!=plan_raw or _read(root,name)!=raw
            or any(_read(root,key)!=value for key,value in originals.items())):
        raise ValueError('QUOTE_SEGMENT_CHANGED_DURING_READ')
    return saved


def review(directory):
    """Stream all V2 originals twice; successful collection is not PIT authority."""
    from services.execution.perpetual_public_replay import _record
    root = Path(directory).absolute()
    fixed = {name: _read(root, name) for name in ('plan.json', 'summary.json', 'capture.json')}
    plan, summary, capture = (decode(fixed[name]) for name in ('plan.json', 'summary.json', 'capture.json'))
    budget = plan_budget(plan, legacy_schema=SEGMENT_PLAN, budget_schema=BUDGETED_SEGMENT_PLAN)
    transport_name = session_quote_transport_name(plan.get('quote_transport'))
    if (budget is None or summary.get('schema') != 'GOLD_PUBLIC_QUOTE_SESSION_SUMMARY_V2'
            or summary.get('status') != 'RAW_QUOTES_COLLECTED'
            or plan.get('submission_enabled') is not False
            or summary.get('submission_enabled') is not False or summary.get('replay_ready') is not False
            or summary.get('source') != plan.get('source')
            or summary.get('plan_sha256') != hashlib.sha256(fixed['plan.json']).hexdigest()
            or summary.get('capture_sha256') != hashlib.sha256(fixed['capture.json']).hexdigest()
            or capture.get('schema_version') != 'GOLD_PUBLIC_SEGMENTED_QUOTE_SESSION_V2'
            or any(document.get('collection_budget') != budget for document in (summary, capture))
            or capture.get('requested_seconds') != plan['seconds']
            or capture.get('declared_interval_ms') != plan.get('interval_seconds', 0) * 1000
            or capture.get('segment_samples') != plan.get('segment_samples')
            or any(type(capture.get(name)) is not int for name in ('requested_seconds', 'sample_count', 'segment_count'))
            or capture.get('submission_enabled') is not False or capture.get('replay_ready') is not False
            or capture.get('quote_transport') != plan['quote_transport']
            or type(plan.get('segment_samples')) is not int or not 3 <= plan['segment_samples'] <= 120
            or type(plan.get('interval_seconds')) is not int or not 1 <= plan['interval_seconds'] <= 10
            or plan.get('receipt_chain') != 'SHA256_CONCATENATED_ORDERED_FILE_SHA256_BYTES'
            or len(fixed['summary.json']) > TERMINAL_METADATA_BYTES
            or capture.get('capture_id') != exact_identity_hash(
                {key: value for key, value in capture.items() if key != 'capture_id'}, name='public_replay_capture')):
        raise ValueError('QUOTE_SESSION_REVIEW_BINDING')
    for name, maximum in (('receipts', budget['maximum_quote_receipts']),
                          ('errors', budget['maximum_quote_receipts']),
                          ('segment_count', budget['maximum_quote_segments']),
                          ('journal_data_bytes', budget['maximum_quote_bytes']),
                          ('original_files', 2 * budget['maximum_quote_receipts'] + budget['maximum_quote_segments'] + 2)):
        if type(summary.get(name)) is not int or not 0 <= summary[name] <= maximum:
            raise ValueError('QUOTE_SESSION_REVIEW_BUDGET')
    if not summary['segment_count'] or capture.get('segment_count') != summary['segment_count']:
        raise ValueError('QUOTE_SESSION_REVIEW_SEGMENTS')

    def originals():
        path = root / 'originals.jsonl'
        if (path.resolve() != path or not path.is_file() or path.stat().st_nlink != 1
                or path.stat().st_size > budget['maximum_quote_bytes']):
            raise ValueError('QUOTE_SESSION_ORIGINAL_MANIFEST_PATH')
        digest = hashlib.sha256()
        size = files = 0
        counters = dict(receipt=0, error=0, segment=0)
        with path.open('rb') as handle:
            while line := handle.readline(4097):
                if len(line) > 4096 or not line.endswith(b'\n'):
                    raise ValueError('QUOTE_SESSION_ORIGINAL_MANIFEST_ROW')
                binding = decode(line)
                if (type(binding) is not dict or set(binding) != {'name', 'bytes', 'sha256'}
                        or type(binding['name']) is not str or type(binding['bytes']) is not int
                        or not 1 <= binding['bytes'] <= budget['maximum_quote_bytes']):
                    raise ValueError('QUOTE_SESSION_ORIGINAL_MANIFEST_ROW')
                name = binding['name']
                if files == 0:
                    valid = name == 'plan.json'
                elif name == 'capture.json':
                    valid = files == summary['original_files'] - 1
                else:
                    kind = name.split('-', 1)[0]
                    valid = kind in counters and name == f'{kind}-{counters[kind]:06d}.json'
                    if valid:
                        counters[kind] += 1
                if not valid or files >= summary['original_files']:
                    raise ValueError('QUOTE_SESSION_ORIGINAL_SEQUENCE')
                original = root / name
                if original.stat().st_nlink != 1:
                    raise ValueError('QUOTE_SESSION_ORIGINAL_ALIAS')
                raw = _read(root, name)
                if len(raw) != binding['bytes'] or hashlib.sha256(raw).hexdigest() != binding['sha256']:
                    raise ValueError('QUOTE_SESSION_ORIGINAL_CHANGED')
                size += len(line) + len(raw)
                files += 1
                if size > budget['maximum_quote_bytes']:
                    raise ValueError('QUOTE_SESSION_REVIEW_BUDGET')
                digest.update(line)
                yield name, raw
        if (digest.hexdigest() != summary.get('originals_sha256')
                or files != summary['original_files'] or size != summary['journal_data_bytes']
                or counters != dict(receipt=summary['receipts'], error=summary['errors'], segment=summary['segment_count'])):
            raise ValueError('QUOTE_SESSION_ORIGINAL_MANIFEST_CHANGED')

    receipt_chain = hashlib.sha256()
    wire_bytes = 0
    sample_count = 0
    first_sample = last_sample = pending = None
    coverage = dict(first_received_at=None, last_received_at=None, max_gap_ms=0, gap_count=0)
    bootstrap = []
    last_record = None
    scheduled = None
    for name, raw in originals():
        if name.startswith('error-'):
            error = decode(raw)
            continuation_fields = {'continuation_cycle', 'continuation_scheduled',
                                   'continuation_delay_seconds'}
            if transport_name != RAW_GAP_TRANSPORT and continuation_fields.intersection(error):
                raise ValueError('QUOTE_SESSION_CONTINUATION_DOWNGRADE')
            if transport_name == RAW_GAP_TRANSPORT:
                next_request = _continuation_error(error, interval_seconds=plan['interval_seconds'],
                                                   maximum_cycles=plan['seconds'] + 3)
                if scheduled is None:
                    if error['continuation_cycle'] != 0 or error['attempt'] != 1:
                        raise ValueError('QUOTE_SESSION_CONTINUATION_SEQUENCE')
                elif (error['kind'] != scheduled['kind'] or error['endpoint'] != scheduled['endpoint']
                        or error['continuation_cycle'] != scheduled['cycle']
                        or error['attempt'] != scheduled['attempt']
                        or error['observed_at'] < scheduled['observed_at']):
                    raise ValueError('QUOTE_SESSION_CONTINUATION_SEQUENCE')
                scheduled = next_request
        if not name.startswith('receipt-'):
            continue
        receipt_chain.update(hashlib.sha256(raw).digest())
        row = decode(raw)
        if type(row) is not dict or row.get('kind') not in ('exchange', 'funding', 'candles', 'mark', 'depth'):
            raise ValueError('QUOTE_SESSION_ORIGINAL_KIND')
        _record(row, row['kind'])
        if scheduled is not None:
            if (row['kind'] != scheduled['kind'] or row['endpoint'] != scheduled['endpoint']
                    or row['requested_at'] < scheduled['observed_at']):
                raise ValueError('QUOTE_SESSION_CONTINUATION_RECEIPT')
            scheduled = None
        encoded_wire = row.get('wire_response_base64', row['response_base64'])
        wire_bytes += len(encoded_wire) // 4 * 3 - (len(encoded_wire) - len(encoded_wire.rstrip('=')))
        last_record = row
        if len(bootstrap) < 3:
            bootstrap.append(row)
        if row['kind'] == 'mark':
            if pending is not None:
                raise ValueError('QUOTE_SESSION_MARK_DEPTH_SEQUENCE')
            pending = row
        elif row['kind'] == 'depth':
            if pending is None or pending['received_at'] > row['requested_at']:
                raise ValueError('QUOTE_SESSION_MARK_DEPTH_SEQUENCE')
            sample = dict(mark=pending, depth=row)
            first_sample = first_sample or sample
            last_sample = sample
            received = row['received_at']
            if coverage['last_received_at'] is None:
                coverage['first_received_at'] = received
            else:
                gap = received - coverage['last_received_at']
                coverage['max_gap_ms'] = max(coverage['max_gap_ms'], gap)
                coverage['gap_count'] += int(gap <= 0 or gap > 30000)
            coverage['last_received_at'] = received
            sample_count += 1
            pending = None
    if (pending is not None or scheduled is not None or not sample_count
            or receipt_chain.hexdigest() != summary['receipt_chain_sha256']
            or sample_count != summary.get('sample_count') or sample_count != capture.get('sample_count')
            or coverage != summary.get('quote_coverage') or coverage != capture.get('quote_coverage')
            or bootstrap != [capture['bootstrap'][key] for key in ('exchange', 'funding', 'candles')]
            or capture.get('samples') != [last_sample] or last_record != capture.get('exchange_after')):
        raise ValueError('QUOTE_SESSION_ORIGINAL_COUNTS')
    metrics = capture.get('transport_metrics')
    if transport_name in POOLED_SESSION_TRANSPORTS:
        if (type(metrics) is not dict
                or set(metrics) != {'schema', 'requests', 'responses', 'failures', 'wire_bytes', 'connections_created'}
                or metrics.get('schema') != 'GOLD_PUBLIC_QUOTE_TRANSPORT_METRICS_V1'
                or any(type(metrics[name]) is not int for name in metrics if name != 'schema')
                or metrics['requests'] != summary['receipts'] + summary['errors']
                or metrics['responses'] != summary['receipts'] or metrics['failures'] != summary['errors']
                or metrics['wire_bytes'] != wire_bytes
                or not 1 <= metrics['connections_created'] <= metrics['requests']):
            raise ValueError('QUOTE_SESSION_TRANSPORT_METRICS')
    elif 'transport_metrics' in capture:
        raise ValueError('QUOTE_SESSION_TRANSPORT_DOWNGRADE')
    previous = None
    count = 0
    for index in range(summary['segment_count']):
        saved = read_segment(root, index)
        segment = saved['segment']
        if segment.get('target_samples') != plan['segment_samples']:
            raise ValueError('QUOTE_SESSION_SEGMENT_PLAN_CHANGED')
        if index == 0:
            if segment['samples'][0] != first_sample or segment['bootstrap'] != capture['bootstrap']:
                raise ValueError('QUOTE_SESSION_FIRST_SEGMENT_CHANGED')
        elif segment['samples'][0] != previous:
            raise ValueError('QUOTE_SESSION_SHARED_BOUNDARY_CHANGED')
        count += len(segment['samples']) - int(index > 0)
        previous = segment['samples'][-1]
    if count != sample_count or previous != last_sample:
        raise ValueError('QUOTE_SESSION_SEGMENT_COUNTS')
    expected_files = summary['original_files'] + 2  # original manifest and terminal summary
    entries = 0
    with os.scandir(root) as members:
        for member in members:
            if not member.is_file(follow_symlinks=False):
                raise ValueError('QUOTE_SESSION_EXTRA_ORIGINAL')
            name = member.name
            valid = name in ('plan.json', 'summary.json', 'capture.json', 'originals.jsonl')
            for kind, maximum in (('receipt', summary['receipts']), ('error', summary['errors']),
                                  ('segment', summary['segment_count'])):
                if name.startswith(kind + '-') and name.endswith('.json'):
                    number = name[len(kind) + 1:-5]
                    valid = number.isdigit() and 0 <= int(number) < maximum and name == f'{kind}-{int(number):06d}.json'
            entries += 1
            if not valid or entries > expected_files:
                raise ValueError('QUOTE_SESSION_EXTRA_ORIGINAL')
    if entries != expected_files:
        raise ValueError('QUOTE_SESSION_MISSING_ORIGINAL')
    for _ in originals():
        pass
    if any(_read(root, name) != raw for name, raw in fixed.items()):
        raise ValueError('QUOTE_SESSION_CHANGED_DURING_REVIEW')
    return dict(schema='GOLD_PUBLIC_QUOTE_SESSION_REVIEW_V2', status='RAW_ORIGINALS_RECONSTRUCTED',
                source=plan['source'], collection_budget=budget, sample_count=sample_count,
                segment_count=summary['segment_count'], quote_coverage=coverage,
                journal_data_bytes=summary['journal_data_bytes'], source_authenticity_proven=False,
                replay_ready=False, economic_eligible=False, submission_enabled=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=86400)
    parser.add_argument('--interval-seconds', type=int, default=10)
    parser.add_argument('--segment-samples',type=int)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.output, seconds=args.seconds,
                         interval_seconds=args.interval_seconds,
                         segment_samples=args.segment_samples)), flush=True)


if __name__ == '__main__':
    main()
