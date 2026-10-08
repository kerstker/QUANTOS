"""Freeze the shared received-time prefix of two independently running journals.

This exports original evidence, not an exchange-rule/funding-complete replay tape.
Incomplete live tails are excluded; earlier holes or changed prefixes reject.
"""
from pathlib import Path
import hashlib

from services.execution.perpetual_public_replay import _record
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_bar_capture import _write
from tools.gold_bar_session import _cursor, SCHEMA
from tools.gold_cost_review import _read

MAX_BYTES = 1024 * 1024 * 1024


def snapshot(quote_directory, bar_directory, output, *, capacity_observations=None,
             quote_segment=None):
    from tools.gold_quote_session import SEGMENT_PLAN, read_segment, segment_records
    if quote_segment is not None and (type(quote_segment) is not int or not 0 <= quote_segment < 86403):
        raise ValueError('SESSION_PREFIX_SEGMENT_INDEX')
    quotes, bars = Path(quote_directory).absolute(), Path(bar_directory).absolute()
    bindings = []
    total = 0
    incomplete = False

    def read(root, name, *, prefix=False):
        nonlocal total, incomplete
        raw = _read(root,name)
        total += len(raw)
        if total > MAX_BYTES:
            raise ValueError('SESSION_PREFIX_BYTE_BUDGET')
        if prefix and not raw.endswith(b'\n'):
            raw = raw[:raw.rfind(b'\n')+1]
            incomplete = True
        bindings.append(dict(root=str(root), name=name, bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(), prefix=prefix))
        return raw

    quote_plan = decode(read(quotes,'plan.json'))
    bar_plan = decode(read(bars,'plan.json'))
    segmented = quote_plan.get('schema') == SEGMENT_PLAN
    if segmented and quote_segment is None:
        raise ValueError('SESSION_PREFIX_SEGMENT_REQUIRED')
    if quote_segment is not None and not segmented:
        raise ValueError('SESSION_PREFIX_SEGMENT_PLAN')
    if (quote_plan.get('schema') not in ('GOLD_PUBLIC_QUOTE_SESSION_PLAN_V1', SEGMENT_PLAN)
            or bar_plan.get('schema') != SCHEMA
            or any(p.get('submission_enabled') is not False for p in (quote_plan,bar_plan))):
        raise ValueError('SESSION_PREFIX_PLAN')
    warmup = read(bars,'warmup.json')
    receipt_raw = read(bars,'warmup-receipt.json')
    receipt = decode(receipt_raw)
    cursor = _cursor(warmup,receipt)
    records = []
    segment = None
    if segmented:
        saved = read_segment(quotes, quote_segment)
        retained = decode(read(quotes, f'segment-{quote_segment:06d}.json'))
        if saved != retained or saved['parent_plan_sha256'] != bindings[0]['sha256']:
            raise ValueError('SESSION_PREFIX_SEGMENT_CHANGED')
        segment = saved['segment']
        for binding, row in zip(saved['receipts'], segment_records(segment)):
            raw = read(quotes, f"receipt-{binding['index']:06d}.json")
            if hashlib.sha256(raw).hexdigest() != binding['sha256'] or decode(raw) != row:
                raise ValueError('SESSION_PREFIX_SEGMENT_ORIGINAL_CHANGED')
        records = [*(segment['bootstrap'][name] for name in ('exchange','funding','candles')),
            *(row[name] for row in segment['samples'] for name in ('mark','depth')),
            segment['exchange_after']]
    else:
        names = sorted(p.name for p in quotes.glob('receipt-*.json'))
        if not 5 <= len(names) <= 172806:
            raise ValueError('SESSION_PREFIX_QUOTE_BUDGET')
        for index, name in enumerate(names):
            if name != f'receipt-{index:06d}.json':
                raise ValueError('SESSION_PREFIX_RECEIPT_SEQUENCE')
            raw = read(quotes,name, prefix=index==len(names)-1)
            if not raw:
                break
            row = decode(raw)
            if not isinstance(row,dict) or row.get('kind') not in ('exchange','funding','candles','mark','depth'):
                raise ValueError('SESSION_PREFIX_RECORD_KIND')
            _record(row,row['kind'])
            records.append(row)
    if [r['kind'] for r in records[:3]] != ['exchange','funding','candles']:
        raise ValueError('SESSION_PREFIX_BOOTSTRAP')
    samples = []
    pending = None
    for i, row in enumerate(records[3:], start=3):
        if row['kind']=='exchange' and i==len(records)-1 and pending is None:
            continue
        if row['kind']=='mark' and pending is None:
            pending = row
        elif row['kind']=='depth' and pending is not None:
            if (pending['received_at'] > row['requested_at'] or samples
                    and (samples[-1]['depth']['received_at'] > pending['requested_at']
                         or samples[-1]['depth']['received_at'] >= row['received_at'])):
                raise ValueError('SESSION_PREFIX_QUOTE_CLOCK')
            samples.append(dict(mark=pending,depth=row))
            pending = None
        else:
            raise ValueError('SESSION_PREFIX_MARK_DEPTH_SEQUENCE')
    if not samples:
        raise ValueError('SESSION_PREFIX_NO_QUOTES')
    names = sorted(p.name for p in bars.glob('messages-*.jsonl'))
    if not 1 <= len(names) <= 500000:
        raise ValueError('SESSION_PREFIX_BAR_BUDGET')
    messages = []
    for index, name in enumerate(names):
        if name != f'messages-{index:04d}.jsonl':
            raise ValueError('SESSION_PREFIX_BAR_SEQUENCE')
        raw = read(bars,name,prefix=index==len(names)-1)
        for line in raw.splitlines():
            row = decode(line)
            if type(row) is not dict or set(row) != {'raw','received_at_ms','decision'}:
                raise ValueError('SESSION_PREFIX_MESSAGE_SHAPE')
            observed = cursor.ingest(row['raw'],received_at_ms=row['received_at_ms'])
            if observed != row['decision']:
                raise ValueError('SESSION_PREFIX_DECISION_MISMATCH')
            messages.append(row)
            if len(messages)>500000:
                raise ValueError('SESSION_PREFIX_MESSAGE_BUDGET')
    if not messages:
        raise ValueError('SESSION_PREFIX_NO_BAR_MESSAGES')
    quote_watermark = samples[-1]['depth']['received_at']
    bar_watermark = messages[-1]['received_at_ms']
    as_of = min(quote_watermark,bar_watermark)
    selected = [s for s in samples if s['mark']['requested_at'] >= receipt['received_at_ms']
                and s['depth']['received_at'] <= as_of]
    if not selected:
        raise ValueError('SESSION_PREFIX_NO_COMMON_WARMED_QUOTES')
    messages = [m for m in messages if m['received_at_ms'] <= as_of]
    gaps = [b['depth']['received_at']-a['depth']['received_at'] for a,b in zip(selected,selected[1:])]
    # Source writers may append, but no consumed bytes may change.
    for binding in bindings:
        raw = _read(Path(binding['root']),binding['name'])
        if binding['prefix']:
            raw = raw[:binding['bytes']]
        if len(raw)!=binding['bytes'] or hashlib.sha256(raw).hexdigest()!=binding['sha256']:
            raise ValueError('SESSION_PREFIX_SOURCE_CHANGED')
    inputs = dict(schema='GOLD_SHARED_RAW_PREFIX_V1', as_of_ms=as_of,
        bootstrap=dict(zip(('exchange','funding','candles'),records[:3])),
        samples=selected,bar_messages=messages,quote_source=quote_plan['source'],
        bar_source=bar_plan['source'],submission_enabled=False)
    if 'interval_seconds' in quote_plan:
        interval = quote_plan['interval_seconds']
        if type(interval) is not int or not 1 <= interval <= 10:
            raise ValueError('SESSION_PREFIX_QUOTE_INTERVAL')
        inputs['quote_declared_interval_ms'] = interval * 1000
    if segment is not None:
        inputs.update(quote_segment=dict(index=quote_segment, segment_id=segment['segment_id'],
            parent_plan_sha256=saved['parent_plan_sha256']), exchange_after=segment['exchange_after'],
            observed_funding_records=segment['observed_funding_records'])
        for record in segment['observed_funding_records']:
            _, available = _record(record, 'funding')
            if available > as_of:
                raise ValueError('SESSION_PREFIX_FUNDING_NOT_YET_AVAILABLE')
        if capacity_observations is not None and capacity_observations != segment['capacity_observations']:
            raise ValueError('SESSION_PREFIX_SEGMENT_CAPACITY_CHANGED')
        capacity_observations = segment['capacity_observations'] or None
    if capacity_observations is not None:
        if type(capacity_observations) is not list or not 1 <= len(capacity_observations) <= 31:
            raise ValueError('SESSION_PREFIX_CAPACITY_BUDGET')
        for record in capacity_observations:
            _, available = _record(record, 'candles')
            if available > as_of:
                raise ValueError('SESSION_PREFIX_CAPACITY_NOT_YET_AVAILABLE')
        inputs['capacity_observations'] = capacity_observations
    output = Path(output)
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    (output/'warmup.json').write_bytes(warmup)
    (output/'warmup-receipt.json').write_bytes(receipt_raw)
    _write(output/'inputs.json',inputs)
    _write(output/'sources.json',bindings)
    result = dict(schema='GOLD_SHARED_RAW_PREFIX_SUMMARY_V1', as_of_ms=as_of,
        quote_watermark_ms=quote_watermark,bar_watermark_ms=bar_watermark,
        sample_count=len(selected),bar_message_count=len(messages),
        closed_bars=sum(m['decision']['status']=='CLOSED_BAR' for m in messages),
        quote_gap_count=sum(g>30000 for g in gaps),max_quote_gap_ms=max(gaps,default=0),
        ignored_incomplete_tail=incomplete,unpaired_mark_omitted=pending is not None,
        files={name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in
               ('warmup.json','warmup-receipt.json','inputs.json','sources.json')},
        replay_ready=False,economic_eligible=False,submission_enabled=False,
        limitations=['COMMON_RAW_PREFIX_NOT_ACCOUNT_REPLAY',
                     'FUNDING_AND_RULE_COVERAGE_NOT_ESTABLISHED'])
    _write(output/'summary.json',result)
    return result
