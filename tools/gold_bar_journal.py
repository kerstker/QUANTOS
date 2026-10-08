"""Reconstruct completed journals or explicitly frozen observation prefixes."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.research.gold_bar_dataset import load_bars
from services.research.gold_bar_stream import GoldBarStream
from tools.gold_cost_review import _read

PREFIX_SCHEMA = 'GOLD_BAR_FROZEN_PREFIX_SUMMARY_V1'


def _prefix(summary, plan, raw):
    """Verify retained original prefix bytes; never open their source paths."""
    from quantos.contracts.registered_research_run import canonical, MAX_INPUT_BYTES
    from tools.gold_bar_session import SCHEMA
    frozen = summary.get('frozen_prefix')
    if (type(frozen) is not dict or set(frozen) != {'summary.json','inputs.json','sources.json'}
            or any(type(value) is not str for value in frozen.values())
            or plan.get('schema') != SCHEMA or type(plan.get('seconds')) is not int
            or not 1 <= plan['seconds'] <= 86400
            or summary.get('status') != 'FROZEN_PREFIX'
            or summary.get('reason') != 'COMMON_WATERMARK_FROZEN'
            or summary.get('economic_eligible') is not False):
        raise ValueError('BAR_FROZEN_PREFIX_SCHEMA')
    canonical(summary, maximum=MAX_INPUT_BYTES)
    original = {name:base64.b64decode(value, validate=True) for name,value in frozen.items()}
    original.update({name:raw[name] for name in ('warmup.json','warmup-receipt.json')})
    saved = decode(original['summary.json'])
    inputs = decode(original['inputs.json'])
    sources = decode(original['sources.json'])
    canonical(inputs, maximum=MAX_INPUT_BYTES)
    canonical(sources, maximum=MAX_INPUT_BYTES)
    if (saved.get('schema') != 'GOLD_SHARED_RAW_PREFIX_SUMMARY_V1'
            or inputs.get('schema') != 'GOLD_SHARED_RAW_PREFIX_V1'
            or saved.get('submission_enabled') is not False
            or saved.get('economic_eligible') is not False or saved.get('replay_ready') is not False
            or inputs.get('submission_enabled') is not False
            or set(saved.get('files',{})) != set(original)-{'summary.json'}
            or any(hashlib.sha256(original[name]).hexdigest() != value
                   for name,value in saved['files'].items())
            or type(inputs.get('as_of_ms')) is not int
            or not 0 <= inputs['as_of_ms'] <= 2**63-1
            or inputs['as_of_ms'] != saved.get('as_of_ms')
            or inputs['as_of_ms'] != summary.get('as_of_ms')
            or type(saved.get('quote_watermark_ms')) is not int
            or type(saved.get('bar_watermark_ms')) is not int
            or inputs['as_of_ms'] != min(saved['quote_watermark_ms'], saved['bar_watermark_ms'])
            or inputs.get('quote_source') != plan.get('source')
            or inputs.get('bar_source') != plan.get('source')
            or type(inputs.get('bar_messages')) is not list
            or type(inputs.get('samples')) is not list or not inputs['samples']):
        raise ValueError('BAR_FROZEN_PREFIX_BINDING')
    if (type(sources) is not list or len(sources) < 4
            or any(type(row) is not dict for row in sources[:4])
            or any(sources[index].get('name') != name
                or sources[index].get('prefix') is not False
                or sources[index].get('bytes') != len(raw[name])
                or sources[index].get('sha256') != hashlib.sha256(raw[name]).hexdigest()
                for index,name in ((1,'plan.json'),(2,'warmup.json'),(3,'warmup-receipt.json')))):
        raise ValueError('BAR_FROZEN_PREFIX_ORIGINAL_PLAN_CHANGED')
    if 'quote_declared_interval_ms' in inputs:
        interval = inputs['quote_declared_interval_ms']
        if type(interval) is not int or not 1000 <= interval <= 10000 or interval % 1000:
            raise ValueError('BAR_FROZEN_PREFIX_QUOTE_INTERVAL')
    return original, saved, inputs


def bind_capture(review, capture, observed_funding_records=None):
    """Keep original quotes and journal in the same immutable common prefix."""
    from quantos.contracts.registered_research_run import digest
    binding = review.get('frozen_prefix')
    if binding is None:
        if capture.get('schema_version') == 'GOLD_PUBLIC_SESSION_PREFIX_V1':
            raise ValueError('BAR_PREFIX_CAPTURE_REQUIRES_PREFIX_JOURNAL')
        return
    component_digest = digest
    if binding.get('schema') == 'GOLD_BAR_FROZEN_PREFIX_BINDING_V2':
        from quantos.contracts.gold_prefix_chain import component_digest
    if (capture.get('schema_version') != 'GOLD_PUBLIC_SESSION_PREFIX_V1'
            or capture.get('prefix_summary_sha256') != binding['summary_sha256']
            or capture.get('max_gap_ms') != 30000
            or ('declared_interval_ms' not in binding['components']
                and capture.get('declared_interval_ms') != 10000)
            or 'observation_gap_policy' in capture
            or any(component_digest(capture.get(name)) != value for name,value in binding['components'].items())
            or capture['samples'][-1]['depth']['received_at'] > binding['as_of_ms']):
        raise ValueError('BAR_FROZEN_PREFIX_CAPTURE_CHANGED')
    if ('observed_funding_records_sha256' in binding
            and digest(observed_funding_records or []) != binding['observed_funding_records_sha256']):
        raise ValueError('BAR_FROZEN_PREFIX_FUNDING_CHANGED')
    if binding.get('schema') == 'GOLD_BAR_FROZEN_PREFIX_BINDING_V2':
        from tools.gold_prefix_journal import bind_quote
        bind_quote(review, capture, observed_funding_records)


def restore(directory, *, previous_cursor=None, previous_review=None):
    from quantos.contracts.gold_observation_window import EXTENDED_MAX_SECONDS, EXTENDED_STREAM_SCHEMA
    root = Path(directory).absolute()
    names = ('summary.json', 'plan.json', 'warmup.json', 'warmup-receipt.json',
             'messages.jsonl', 'decisions.jsonl')
    raw = {n: _read(root, n) for n in names}
    summary, plan, receipt = (decode(raw[n]) for n in
                              ('summary.json', 'plan.json', 'warmup-receipt.json'))
    from quantos.contracts.gold_prefix_chain import SCHEMA as delta_schema
    if summary.get('schema') == delta_schema:
        from tools.gold_prefix_journal import restore as restore_delta
        result = restore_delta(raw, previous_cursor, previous_review)
        if any(_read(root, name) != body for name, body in raw.items()):
            raise ValueError('BAR_JOURNAL_CHANGED_DURING_REVIEW')
        return result
    if previous_review is not None and 'prefix_chain' in previous_review:
        raise ValueError('BAR_JOURNAL_PREFIX_VERSION_CHANGED')
    prefix = summary.get('schema') == PREFIX_SCHEMA
    frozen = _prefix(summary, plan, raw) if prefix else None
    extended = summary.get('schema') == EXTENDED_STREAM_SCHEMA
    if extended and (plan.get('extended') is not True or type(plan.get('seconds')) is not int
                     or not 1 <= plan['seconds'] <= EXTENDED_MAX_SECONDS):
        raise ValueError('BAR_EXTENDED_JOURNAL_PLAN')
    if ((not prefix and (summary.get('schema') not in ('GOLD_BAR_STREAM_SUMMARY_V1', EXTENDED_STREAM_SCHEMA)
            or summary.get('status') != 'OBSERVED' or summary.get('reason') not in ('DURATION_COMPLETE', 'FIRST_NEW_CLOSE_COMPLETE')))
            or summary.get('source_unchanged') is not True
            or summary.get('submission_enabled') is not False or plan.get('submission_enabled') is not False
            or plan.get('url') != 'wss://fstream.binance.com/market/stream?streams=xauusdt@kline_15m'):
        raise ValueError('BAR_JOURNAL_NOT_COMPLETE')
    hashes = {n: hashlib.sha256(body).hexdigest() for n, body in raw.items()}
    if (hashes['warmup.json'] != receipt['response_sha256']
            or (not prefix and hashes['warmup.json'] != plan['warmup_source_sha256'])
            or hashes['messages.jsonl'] != summary['messages_sha256']
            or hashes['decisions.jsonl'] != summary['decisions_sha256']):
        raise ValueError('BAR_JOURNAL_BYTES_CHANGED')
    cursor = GoldBarStream(load_bars(raw['warmup.json'], requested_at_ms=receipt['requested_at_ms'],
                                    received_at_ms=receipt['received_at_ms']))
    lines = raw['messages.jsonl'].splitlines()
    if (type(summary['messages']) is not int or not 1 <= len(lines) <= 20000
            or len(lines) != summary['messages']):
        raise ValueError('BAR_JOURNAL_MESSAGE_COUNT')
    decisions = []
    original_rows = []
    for line in lines:
        item = decode(line)
        if type(item) is not dict or set(item) != {'received_at_ms', 'raw'}:
            raise ValueError('BAR_JOURNAL_MESSAGE_SHAPE')
        observed = cursor.ingest(item['raw'], received_at_ms=item['received_at_ms'])
        if prefix:
            if item['received_at_ms'] > summary['as_of_ms']:
                raise ValueError('BAR_FROZEN_PREFIX_FUTURE_MESSAGE')
            original_rows.append(dict(item, decision=observed))
        if observed['status'] == 'CLOSED_BAR':
            decisions.append(observed)
    saved = [decode(line) for line in raw['decisions.jsonl'].splitlines()]
    if decisions != saved or type(summary['closed_bars']) is not int or len(decisions) != summary['closed_bars']:
        raise ValueError('BAR_JOURNAL_DECISIONS_CHANGED')
    prefix_binding = None
    if prefix:
        from quantos.contracts.registered_research_run import digest
        original, prefix_summary, inputs = frozen
        if (original_rows != inputs['bar_messages']
                or len(original_rows) != prefix_summary.get('bar_message_count')
                or len(decisions) != prefix_summary.get('closed_bars')
                or len(inputs['samples']) != prefix_summary.get('sample_count')
                or 'stop_after_close_ms' in plan):
            raise ValueError('BAR_FROZEN_PREFIX_ORIGINAL_MESSAGES_CHANGED')
        prefix_binding = dict(schema='GOLD_BAR_FROZEN_PREFIX_BINDING_V1',
            summary_sha256=hashlib.sha256(original['summary.json']).hexdigest(),
            files=prefix_summary['files'], as_of_ms=inputs['as_of_ms'],
            components={name:digest(inputs.get(name)) for name in
                        ('bootstrap','samples','capacity_observations')})
        if 'quote_declared_interval_ms' in inputs:
            prefix_binding['components']['declared_interval_ms'] = digest(inputs['quote_declared_interval_ms'])
        if 'exchange_after' in inputs:
            prefix_binding['components']['exchange_after'] = digest(inputs['exchange_after'])
        if 'observed_funding_records' in inputs:
            prefix_binding['observed_funding_records_sha256'] = digest(inputs['observed_funding_records'])
    threshold = plan.get('stop_after_close_ms')
    if 'stop_after_close_ms' in plan:
        if type(threshold) is not int or not 0 <= threshold <= 2**63-1 or extended:
            raise ValueError('BAR_FIRST_CLOSE_PLAN')
    eligible = [row for row in decisions if threshold is not None and row['close_ms'] > threshold]
    if summary['reason'] == 'FIRST_NEW_CLOSE_COMPLETE':
        if (threshold is None or len(eligible) != 1 or observed != eligible[0]
                or observed['status'] != 'CLOSED_BAR'):
            raise ValueError('BAR_FIRST_CLOSE_NOT_COMPLETE')
    elif eligible:
        raise ValueError('BAR_FIRST_CLOSE_NOT_STOPPED')
    # Do not accept a successful review over files replaced midway through it.
    if any(_read(root, name) != body for name, body in raw.items()):
        raise ValueError('BAR_JOURNAL_CHANGED_DURING_REVIEW')
    review = dict(schema='GOLD_BAR_JOURNAL_REVIEW_V1',
        status='RECONSTRUCTED_NOT_INDEPENDENTLY_APPROVED', closed_bars=len(decisions),
        messages=len(lines), hashes=hashes, source=plan['source'], submission_enabled=False,
        economic_eligible=False, source_authenticity_proven=False)
    if prefix_binding is not None:
        review['frozen_prefix'] = prefix_binding
    return cursor, review


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args(argv)
    _, report = restore(args.directory)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
