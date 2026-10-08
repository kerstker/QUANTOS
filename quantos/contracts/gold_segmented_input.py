"""Bounded original public segments within one native Gold invocation.

This transport grants no source, result, economic or trading authority. All
segments remain in the original input digest and each journal keeps its prefix.
"""
import base64

from quantos.contracts.registered_research_run import MAX_INPUT_BYTES, canonical

SCHEMA = 'QUANTOS_GOLD_SEGMENTED_INPUT_V1'
SEGMENT_SCHEMA = 'QUANTOS_GOLD_CONTINUATION_SEGMENT_V1'
MAX_SEGMENTS = 64
JOURNAL_FILES = ('summary.json', 'plan.json', 'warmup.json', 'warmup-receipt.json',
                 'messages.jsonl', 'decisions.jsonl')
SEGMENT_FIELDS = frozenset(('schema', 'capture', 'journal_files',
                           'observed_funding_records', 'settlement_window_ms'))


def require(condition, code):
    if not condition:
        raise ValueError('GOLD_SEGMENT_'+code)


def journals(document):
    """Validate JSON/base64 budgets and exact append-only byte ancestry."""
    canonical(document, maximum=MAX_INPUT_BYTES)
    parts = documents(document)
    return tuple(raw for _, raw in iter_journals(parts))


def iter_journals(parts):
    """Validate one original journal prefix at a time; caller owns transport bounds."""
    previous = None
    for part in parts:
        files = part.get('journal_files')
        require(type(files) is dict and set(files) == set(JOURNAL_FILES)
            and all(type(v) is str for v in files.values()), 'JOURNAL_FIELDS')
        try:
            raw = {n: base64.b64decode(v, validate=True) for n, v in files.items()}
        except ValueError as exc:
            raise ValueError('GOLD_SEGMENT_JOURNAL_BASE64') from exc
        require(all(0 < len(v) <= MAX_INPUT_BYTES for v in raw.values()), 'JOURNAL_BOUND')
        from quantos.contracts.gold_prefix_chain import journals as prefix_chain
        delta = prefix_chain(raw, previous)
        if previous is not None:
            old = previous
            require(all(raw[n] == old[n] for n in ('plan.json','warmup.json','warmup-receipt.json')),
                    'JOURNAL_HISTORY_CHANGED')
            if not delta:
                require(all(old[n].endswith(b'\n') and raw[n].startswith(old[n])
                            for n in ('messages.jsonl','decisions.jsonl')), 'JOURNAL_PREFIX_CHANGED')
        previous = raw
        yield part, raw


def documents(document):
    """Return ordered immutable pieces without executing the research producer."""
    require(type(document) is dict and document.get('input_schema') == SCHEMA,
            'INPUT_SCHEMA')
    require(set(document) <= {'input_schema','continuation_segments','capture','journal_files',
        'quantity','initial_collateral','fee_rate','release_identity','portfolio_plan',
        'observed_funding_records','settlement_window_ms'}, 'INPUT_FIELDS')
    segments = document.get('continuation_segments')
    require(type(segments) is list and 1 <= len(segments) < MAX_SEGMENTS, 'COUNT')
    for part in segments:
        require(type(part) is dict and {'schema','capture','journal_files'} <= set(part)
            <= SEGMENT_FIELDS and part['schema'] == SEGMENT_SCHEMA, 'FIELDS')
    parts = (document, *segments)
    require(all(type(p.get('capture')) is dict for p in parts), 'CAPTURE')
    for part in parts:
        if 'observed_funding_records' in part:
            require(type(part['observed_funding_records']) is list
                and 1 <= len(part['observed_funding_records']) <= 1000, 'FUNDING_RECORDS')
        if 'settlement_window_ms' in part:
            require(type(part['settlement_window_ms']) is int
                and 1 <= part['settlement_window_ms'] <= 1000, 'FUNDING_WINDOW')
    return parts


def final_journal(document):
    """Bind prospective window checks to the final full original journal."""
    journals(document)
    return documents(document)[-1]['journal_files']
