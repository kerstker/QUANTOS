"""Cold reconstruction of every original wire in an ordered V2 prefix chain."""
import base64
import hashlib
import re

from quantos.contracts.gold_collection_budget import plan_budget
from quantos.contracts.gold_prefix_chain import SCHEMA, ordered, require
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from quantos.contracts.registered_research_run import canonical, MAX_INPUT_BYTES, digest
from services.execution.backtest_authority import exact_identity_hash
from tools import gold_bar_session as bars, gold_quote_session as quotes

FIELDS = frozenset(('schema','status','reason','source_unchanged','economic_eligible','submission_enabled',
    'replay_ready','position','previous_summary_sha256','as_of_ms','messages','closed_bars','prefix_messages',
    'prefix_closed_bars','prefix_bytes','prefix_messages_sha256','messages_sha256','decisions_sha256',
    'collection_budget','execution_source','quote_plan','quote_segment','bar_originals','capture_components',
    'observed_funding_records_sha256'))


def restore(raw, previous_cursor=None, previous_review=None):
    summary, plan, receipt = (decode(raw[n]) for n in ('summary.json','plan.json','warmup-receipt.json'))
    require(type(summary) is dict and set(summary) == FIELDS and summary['schema'] == SCHEMA, 'SUMMARY_FIELDS')
    canonical(summary, maximum=MAX_INPUT_BYTES)
    require(summary['status'] == 'FROZEN_PREFIX' and summary['reason'] == 'COMMON_WATERMARK_FROZEN'
        and summary['source_unchanged'] is True and summary['economic_eligible'] is False
        and summary['submission_enabled'] is False and summary['replay_ready'] is False, 'NOT_FROZEN')
    qp_raw = base64.b64decode(summary['quote_plan'], validate=True)
    qp = decode(qp_raw)
    budget = bars.bar_plan_budget(plan)
    qb = plan_budget(qp, legacy_schema=quotes.SEGMENT_PLAN, budget_schema=quotes.BUDGETED_SEGMENT_PLAN)
    require(budget is not None and budget == qb == summary['collection_budget']
        and qp['source'] == plan['source'] and qp.get('submission_enabled') is False
        and plan.get('submission_enabled') is False and plan.get('url') == bars.URL
        and type(qp.get('interval_seconds')) is int and 1 <= qp['interval_seconds'] <= 10,
        'PLAN_BINDING')
    quotes.session_quote_transport_name(qp.get('quote_transport'))
    require(type(qp.get('segment_samples')) is int and 3 <= qp['segment_samples'] <= 120
        and qp.get('maximum_segment_samples') == 240, 'QUOTE_PLAN')
    require(summary['position'] < budget['maximum_quote_segments']
        and 0 <= summary['prefix_messages'] <= budget['maximum_bar_messages']
        and 0 <= summary['prefix_bytes'] <= budget['maximum_bar_bytes'], 'BUDGET')
    hashes = {name: hashlib.sha256(body).hexdigest() for name,body in raw.items()}
    require(hashes['messages.jsonl'] == summary['messages_sha256']
        and hashes['decisions.jsonl'] == summary['decisions_sha256'], 'ORIGINAL_BYTES')
    old = previous_review.get('prefix_chain') if previous_review is not None else None
    ordered(summary, old)
    if old is None:
        require(previous_cursor is None, 'UNBOUND_CURSOR')
        cursor = bars._cursor(raw['warmup.json'], receipt, late_open_policy=plan.get('late_open_policy'))
        message_hash, chunk_hash, last_range = hashlib.sha256(), None, None
        old_bytes = 0
    else:
        require(previous_cursor is not None and getattr(previous_cursor, '_gold_prefix_review', None) == previous_review
            and summary['previous_summary_sha256'] == previous_review['hashes']['summary.json']
            and all(hashes[n] == previous_review['hashes'][n] for n in
                ('plan.json','warmup.json','warmup-receipt.json'))
            and hashlib.sha256(qp_raw).hexdigest() == previous_review['quote_plan_sha256'], 'ANCESTRY')
        cursor = previous_cursor
        message_hash = cursor._gold_prefix_digest.copy()
        chunk_hash = cursor._gold_chunk_digest.copy() if cursor._gold_chunk_digest is not None else None
        last_range = previous_review['last_bar_original']
        old_bytes = old['prefix_bytes']
    message_raw = b'' if raw['messages.jsonl'] == b'\n' and summary['messages'] == 0 else raw['messages.jsonl']
    require(not message_raw or message_raw.endswith(b'\n'), 'WIRE_TERMINATION')
    ranges = summary['bar_originals']
    require(type(ranges) is list and len(ranges) <= budget['maximum_bar_messages'], 'WIRE_RANGES')
    offset = 0
    for row in ranges:
        require(type(row) is dict and set(row) == {'name','offset','bytes','sha256','chunk_bytes','chunk_sha256'}
            and type(row['name']) is str and re.fullmatch(r'messages-\d{4,}\.jsonl', row['name'])
            and all(type(row[n]) is int for n in ('offset','bytes','chunk_bytes'))
            and 0 <= row['offset'] < row['offset']+row['bytes'] <= row['chunk_bytes'] <= bars.CHUNK_BYTES,
            'WIRE_RANGE')
        piece = message_raw[offset:offset+row['bytes']]
        require(len(piece) == row['bytes'] and hashlib.sha256(piece).hexdigest() == row['sha256'], 'WIRE_RANGE_BYTES')
        number = int(row['name'][9:-6])
        if last_range is None:
            require(number == 0 and row['offset'] == 0, 'FIRST_WIRE_RANGE')
            chunk_hash = hashlib.sha256()
        elif row['name'] == last_range['name']:
            require(row['offset'] == last_range['offset']+last_range['bytes']
                and row['chunk_bytes'] == last_range['chunk_bytes']
                and row['chunk_sha256'] == last_range['chunk_sha256'], 'WIRE_RANGE_CONTINUATION')
        else:
            require(last_range['offset']+last_range['bytes'] == last_range['chunk_bytes']
                and chunk_hash.hexdigest() == last_range['chunk_sha256']
                and number == int(last_range['name'][9:-6])+1 and row['offset'] == 0, 'WIRE_CHUNK_CONTINUATION')
            chunk_hash = hashlib.sha256()
        chunk_hash.update(piece)
        if row['offset']+row['bytes'] == row['chunk_bytes']:
            require(chunk_hash.hexdigest() == row['chunk_sha256'], 'WIRE_CHUNK_BYTES')
        last_range = row
        offset += row['bytes']
    require(offset == len(message_raw) and old_bytes+len(message_raw) == summary['prefix_bytes'], 'WIRE_BYTES')
    message_hash.update(message_raw)
    require(message_hash.hexdigest() == summary['prefix_messages_sha256'], 'WIRE_PREFIX_DIGEST')
    count, decisions = 0, []
    for line in message_raw.splitlines():
        entry = decode(line)
        require(type(entry) is dict and set(entry) == {'raw','received_at_ms','decision'}
            and type(entry['received_at_ms']) is int and entry['received_at_ms'] <= summary['as_of_ms'], 'WIRE_SHAPE')
        observed = cursor.ingest(entry['raw'], received_at_ms=entry['received_at_ms'])
        require(observed == entry['decision'], 'WIRE_DECISION')
        count += 1
        if observed['status'] == 'CLOSED_BAR':
            decisions.append(observed)
    saved_raw = b'' if raw['decisions.jsonl'] == b'\n' and not summary['closed_bars'] else raw['decisions.jsonl']
    require((not saved_raw or saved_raw.endswith(b'\n')) and decisions == [decode(v) for v in saved_raw.splitlines()]
        and count == summary['messages'] and len(decisions) == summary['closed_bars'], 'DECISIONS')
    components = summary['capture_components']
    require(type(components) is dict and set(components) ==
        {'bootstrap','samples','exchange_after','capacity_observations','declared_interval_ms'}
        and components['declared_interval_ms'] == digest(qp['interval_seconds']*1000), 'QUOTE_COMPONENTS')
    chain = {n: summary[n] for n in ('schema','position','messages','closed_bars','prefix_messages',
        'prefix_closed_bars','prefix_bytes','as_of_ms','execution_source')}
    review = dict(schema='GOLD_BAR_JOURNAL_REVIEW_V2', status='RECONSTRUCTED_NOT_INDEPENDENTLY_APPROVED',
        closed_bars=len(decisions), messages=count, hashes=hashes, source=plan['source'],
        execution_source=summary['execution_source'], submission_enabled=False, economic_eligible=False,
        source_authenticity_proven=False, prefix_chain=chain, last_bar_original=last_range,
        quote_plan_sha256=hashlib.sha256(qp_raw).hexdigest(), target_quote_samples=qp['segment_samples'],
        quote_segment=summary['quote_segment'],
        collection_budget=budget, frozen_prefix=dict(schema='GOLD_BAR_FROZEN_PREFIX_BINDING_V2',
            summary_sha256=hashes['summary.json'], as_of_ms=summary['as_of_ms'], components=components,
            observed_funding_records_sha256=summary['observed_funding_records_sha256']))
    cursor._gold_prefix_digest, cursor._gold_chunk_digest = message_hash, chunk_hash
    cursor._gold_prefix_review = review
    return cursor, review


def bind_quote(review, capture, funding):
    """Reconstruct the retained quote segment and all original receipt hashes."""
    binding = review['quote_segment']
    require(type(binding) is dict and set(binding) == {'metadata','parent_plan_sha256','receipts'}
        and binding['parent_plan_sha256'] == review['quote_plan_sha256'], 'QUOTE_SEGMENT_BINDING')
    metadata = binding['metadata']
    require(type(metadata) is dict and set(metadata) == {'schema','index','target_samples','maximum_samples',
        'status','replay_ready','economic_eligible','submission_enabled','segment_id'}
        and metadata['schema'] == 'GOLD_PUBLIC_QUOTE_SEGMENT_V1'
        and metadata['index'] == review['prefix_chain']['position']
        and metadata['target_samples'] == review['target_quote_samples'] and metadata['maximum_samples'] == 240
        and metadata['status'] == 'RAW_SEGMENT_NOT_REPLAY_VALIDATED'
        and metadata['replay_ready'] is metadata['economic_eligible'] is metadata['submission_enabled'] is False,
        'QUOTE_METADATA')
    segment = dict(metadata, bootstrap=capture['bootstrap'], samples=capture['samples'],
        exchange_after=capture['exchange_after'], observed_funding_records=funding or [],
        capacity_observations=capture.get('capacity_observations', []))
    require(segment['segment_id'] == exact_identity_hash(
        {k:v for k,v in segment.items() if k != 'segment_id'}, name='gold_raw_quote_segment'), 'QUOTE_SEGMENT_ID')
    receipts, rows = binding['receipts'], quotes.segment_records(segment)
    require(type(receipts) is list and len(receipts) == len(rows), 'QUOTE_RECEIPTS')
    for saved, row in zip(receipts, rows, strict=True):
        require(type(saved) is dict and set(saved) == {'index','sha256'} and type(saved['index']) is int
            and 0 <= saved['index'] < review['collection_budget']['maximum_quote_receipts']
            and saved['sha256'] == hashlib.sha256(quotes._raw(row)).hexdigest(), 'QUOTE_RECEIPT_BYTES')
