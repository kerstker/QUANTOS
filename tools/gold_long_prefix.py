"""Compile budgeted public originals into ordered, bounded native journal pieces."""
import base64
import hashlib
from pathlib import Path

from quantos.contracts.gold_collection_budget import plan_budget
from quantos.contracts.gold_prefix_chain import SCHEMA, require, component_digest
from quantos.contracts import gold_segmented_input as segments
from quantos.contracts.registered_research_run import MAX_INPUT_BYTES, canonical, digest
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_public_replay import GOLD_UNIT_BASIS, prepare_public_replay
from tools import gold_bar_session as bars, gold_quote_session as quotes
from tools.gold_bar_experiment import _bytes
from tools.gold_cost_review import _read

DATA_FIELDS = ('bootstrap', 'samples', 'exchange_after', 'observed_funding_records', 'capacity_observations')


def session_parts(quote_directory, bar_directory, output, *, segment_count,
                  release_identity, quantity, initial_collateral, fee_rate,
                  settlement_window_ms=None, current_source):
    qroot, broot, out = (Path(p).absolute() for p in (quote_directory, bar_directory, output))
    require(all(p.resolve() == p for p in (qroot, broot, out)), 'PATH')
    qraw, braw = _read(qroot, 'plan.json'), _read(broot, 'plan.json')
    qp, bp = decode(qraw), decode(braw)
    qb = plan_budget(qp, legacy_schema=quotes.SEGMENT_PLAN,
                     budget_schema=quotes.BUDGETED_SEGMENT_PLAN)
    bb = bars.bar_plan_budget(bp)
    require(qb is not None and qb == bb and qp['source'] == bp['source']
        and qp.get('submission_enabled') is False and bp.get('submission_enabled') is False
        and type(segment_count) is int and 1 <= segment_count <= qb['maximum_quote_segments']
        and current_source() == release_identity, 'PLAN_BINDING')
    require(type(qp.get('interval_seconds')) is int and 1 <= qp['interval_seconds'] <= 10, 'INTERVAL')
    quotes.session_quote_transport_name(qp.get('quote_transport'))
    require(type(qp.get('segment_samples')) is int and 3 <= qp['segment_samples'] <= 120
        and qp.get('maximum_segment_samples') == 240, 'QUOTE_PLAN')
    require(settlement_window_ms is None or type(settlement_window_ms) is int
            and 1 <= settlement_window_ms <= 1000, 'FUNDING_WINDOW')
    fixed = {name: _read(broot, name) for name in ('warmup.json', 'warmup-receipt.json')}
    receipt = decode(fixed['warmup-receipt.json'])
    cursor = bars._cursor(fixed['warmup.json'], receipt, late_open_policy=bp.get('late_open_policy'))
    names = sorted(p.name for p in broot.glob('messages-*.jsonl'))
    require(names and len(names) <= qb['maximum_bar_messages'], 'CHUNK_COUNT')
    consumed, originals = [], []
    read_bytes = 0

    def lines():
        nonlocal read_bytes
        for index, name in enumerate(names):
            require(name == f'messages-{index:04d}.jsonl', 'CHUNK_SEQUENCE')
            path = broot/name
            require(path.stat().st_nlink == 1, 'CHUNK_ALIAS')
            raw = _read(broot, name)
            require(len(raw) <= bars.CHUNK_BYTES, 'CHUNK_BYTES')
            read_bytes += len(raw)
            require(read_bytes <= qb['maximum_bar_bytes'], 'BYTE_BUDGET')
            if not raw.endswith(b'\n'):
                require(index == len(names)-1, 'INCOMPLETE_EARLY_CHUNK')
                raw = raw[:raw.rfind(b'\n')+1]
            chunk_sha = hashlib.sha256(raw).hexdigest()
            consumed.append((name, len(raw), chunk_sha))
            offset = count = 0
            for line in raw.splitlines(keepends=True):
                count += 1
                require(count <= bars.CHUNK_MESSAGES and line.endswith(b'\n'), 'CHUNK_MESSAGES')
                row = decode(line)
                require(type(row) is dict and set(row) == {'raw', 'received_at_ms', 'decision'}
                    and type(row['received_at_ms']) is int, 'WIRE_SHAPE')
                yield row, line, name, offset, len(raw), chunk_sha
                offset += len(line)

    stream = iter(lines())
    pending = next(stream, None)
    require(pending is not None, 'NO_MESSAGES')
    previous = previous_sample = None
    prefix_messages = prefix_closed = prefix_bytes = 0
    wire_hash = hashlib.sha256()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    for index in range(segment_count):
        saved = quotes.read_segment(qroot, index)
        original = _read(qroot, f'segment-{index:06d}.json')
        require(decode(original) == saved and saved['source'] == bp['source']
            and saved['collection_budget'] == qb
            and saved['parent_plan_sha256'] == hashlib.sha256(qraw).hexdigest(), 'QUOTE_BINDING')
        segment = saved['segment']
        require(segment['target_samples'] == qp['segment_samples'] and segment['maximum_samples'] == 240,
                'QUOTE_PLAN')
        require(previous_sample is None or previous_sample == segment['samples'][0], 'SHARED_BOUNDARY')
        as_of = segment['samples'][-1]['depth']['received_at']
        require(receipt['received_at_ms'] <= segment['samples'][0]['mark']['requested_at'], 'WARMUP')
        messages, decisions, ranges = bytearray(), bytearray(), []
        count = closed = 0
        while pending is not None and pending[0]['received_at_ms'] <= as_of:
            row, line, name, offset, chunk_bytes, chunk_sha = pending
            decision = cursor.ingest(row['raw'], received_at_ms=row['received_at_ms'])
            require(decision == row['decision'], 'DECISION_CHANGED')
            require(len(messages)+len(line) <= MAX_INPUT_BYTES, 'PART_BYTES')
            messages.extend(line)
            wire_hash.update(line)
            if ranges and ranges[-1]['name'] == name:
                require(ranges[-1]['offset']+ranges[-1]['bytes'] == offset, 'WIRE_RANGE')
                ranges[-1]['bytes'] += len(line)
            else:
                ranges.append(dict(name=name, offset=offset, bytes=len(line),
                                   chunk_bytes=chunk_bytes, chunk_sha256=chunk_sha))
            count += 1
            if decision['status'] == 'CLOSED_BAR':
                closed += 1
                decisions.extend(_bytes(decision)+b'\n')
            pending = next(stream, None)
        watermark = pending[0]['received_at_ms'] if pending is not None else cursor.last_received
        require(watermark >= as_of, 'BAR_WATERMARK_INCOMPLETE')
        prefix_messages += count
        prefix_closed += closed
        prefix_bytes += len(messages)
        require(prefix_messages <= qb['maximum_bar_messages'] and prefix_bytes <= qb['maximum_bar_bytes'],
                'PREFIX_BUDGET')
        require(index or closed, 'FIRST_REQUIRES_CLOSED_BAR')
        offset = 0
        for item in ranges:
            item['sha256'] = hashlib.sha256(messages[offset:offset+item['bytes']]).hexdigest()
            offset += item['bytes']
        capture = dict(schema_version='GOLD_PUBLIC_SESSION_PREFIX_V1', symbol='XAUUSDT',
            bootstrap=segment['bootstrap'], samples=segment['samples'], exchange_after=segment['exchange_after'],
            quantity_basis=dict(GOLD_UNIT_BASIS), max_gap_ms=30000,
            declared_interval_ms=qp['interval_seconds']*1000)
        if segment['capacity_observations']:
            capture['capacity_observations'] = segment['capacity_observations']
        summary = dict(schema=SCHEMA, status='FROZEN_PREFIX', reason='COMMON_WATERMARK_FROZEN',
            source_unchanged=True, economic_eligible=False, submission_enabled=False, replay_ready=False,
            position=index, previous_summary_sha256=None if previous is None else hashlib.sha256(previous).hexdigest(),
            as_of_ms=as_of, messages=count, closed_bars=closed, prefix_messages=prefix_messages,
            prefix_closed_bars=prefix_closed, prefix_bytes=prefix_bytes, prefix_messages_sha256=wire_hash.hexdigest(),
            messages_sha256=hashlib.sha256(messages or b'\n').hexdigest(),
            decisions_sha256=hashlib.sha256(decisions or b'\n').hexdigest(), collection_budget=qb,
            execution_source=release_identity, quote_plan=base64.b64encode(qraw).decode(),
            quote_segment=dict(metadata={k:v for k,v in segment.items() if k not in DATA_FIELDS},
                parent_plan_sha256=saved['parent_plan_sha256'], receipts=saved['receipts']), bar_originals=ranges,
            capture_components={k:component_digest(capture.get(k)) for k in
                ('bootstrap','samples','exchange_after','capacity_observations','declared_interval_ms')},
            observed_funding_records_sha256=digest(segment['observed_funding_records']))
        canonical(summary, maximum=MAX_INPUT_BYTES)
        body = _bytes(summary)
        capture['prefix_summary_sha256'] = hashlib.sha256(body).hexdigest()
        capture['capture_id'] = exact_identity_hash(capture, name='public_replay_capture')
        funding = segment['observed_funding_records'] or None
        require(not funding or settlement_window_ms is not None, 'FUNDING_WINDOW_REQUIRED')
        prepare_public_replay(capture, observed_funding_records=funding,
            settlement_window_ms=settlement_window_ms if funding else None)
        files = dict(fixed, **{'plan.json':braw, 'summary.json':body,
            'messages.jsonl':bytes(messages) or b'\n', 'decisions.jsonl':bytes(decisions) or b'\n'})
        directory = out/f'prefix-{index:06d}'
        directory.mkdir(mode=0o700)
        for name, raw in files.items():
            (directory/name).write_bytes(raw)
        value = dict(capture=capture, journal_files={n:base64.b64encode(v).decode() for n,v in files.items()})
        if funding:
            value.update(observed_funding_records=funding, settlement_window_ms=settlement_window_ms)
        if index:
            value['schema'] = segments.SEGMENT_SCHEMA
        else:
            value.update(release_identity=release_identity, quantity=quantity,
                         initial_collateral=initial_collateral, fee_rate=fee_rate)
        originals.append(hashlib.sha256(original).hexdigest())
        previous, previous_sample = body, segment['samples'][-1]
        yield value
    # Appends are allowed; every byte actually consumed remains fixed.
    for name, size, expected in consumed:
        path = broot/name
        raw = _read(broot, name)[:size]
        require(path.stat().st_nlink == 1 and len(raw) == size
                and hashlib.sha256(raw).hexdigest() == expected, 'WIRE_CHANGED')
    for index, expected in enumerate(originals):
        saved = quotes.read_segment(qroot, index)
        raw = _read(qroot, f'segment-{index:06d}.json')
        require(decode(raw) == saved and hashlib.sha256(raw).hexdigest() == expected, 'QUOTE_CHANGED')
    require(_read(qroot, 'plan.json') == qraw and _read(broot, 'plan.json') == braw
        and all(_read(broot, name) == raw for name,raw in fixed.items())
        and current_source() == release_identity, 'SOURCE_CHANGED')
