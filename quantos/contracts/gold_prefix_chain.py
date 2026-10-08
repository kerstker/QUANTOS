"""Ordered raw-wire prefix pieces; provenance binding, never authority."""
import hashlib

from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode

SCHEMA = 'GOLD_BAR_FROZEN_DELTA_SUMMARY_V2'
MAX_ORIGINAL_NODES = 1_000_000


def original_json(value):
    """Whole venue responses in the new physical carrier, under its byte cap."""
    from quantos.contracts.registered_research_run import canonical, MAX_INPUT_BYTES
    return canonical(value, maximum=MAX_INPUT_BYTES, max_nodes=MAX_ORIGINAL_NODES)


def component_digest(value):
    return hashlib.sha256(original_json(value)).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError('GOLD_PREFIX_CHAIN_'+code)


def ordered(current, previous=None):
    """Prevent omission, duplication, mixed formats and reset of a raw prefix."""
    if current.get('schema') != SCHEMA:
        require(previous is None or previous.get('schema') != SCHEMA, 'VERSION_CHANGED')
        return
    for name in ('position', 'messages', 'closed_bars', 'prefix_messages',
                 'prefix_closed_bars', 'prefix_bytes', 'as_of_ms'):
        require(type(current.get(name)) is int and 0 <= current[name] <= 2**63-1, 'COUNTER')
    require(current['position'] < 4096 and current['closed_bars'] <= current['messages'], 'COUNTER')
    if previous is None:
        require(current['position'] == 0 and current.get('previous_summary_sha256') is None
            and current['prefix_messages'] == current['messages']
            and current['prefix_closed_bars'] == current['closed_bars'], 'FIRST')
    else:
        require(previous.get('schema') == SCHEMA, 'VERSION_CHANGED')
        require(current['position'] == previous['position']+1
            and current['as_of_ms'] > previous['as_of_ms']
            and current['prefix_messages'] == previous['prefix_messages']+current['messages']
            and current['prefix_closed_bars'] == previous['prefix_closed_bars']+current['closed_bars']
            and current['execution_source'] == previous['execution_source'], 'ORDER')


def journals(raw, previous):
    current = decode(raw['summary.json'])
    old = decode(previous['summary.json']) if previous is not None else None
    ordered(current, old)
    if current.get('schema') == SCHEMA and previous is not None:
        require(current.get('previous_summary_sha256') ==
                hashlib.sha256(previous['summary.json']).hexdigest(), 'PREVIOUS_BYTES')
    return current.get('schema') == SCHEMA
