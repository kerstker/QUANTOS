"""Bridge original continuous observation prefixes to the existing native jobs.

This is bounded offline preparation. It neither declares a completed collection
nor submits a job, changes trial history, or grants economic/trading authority.
"""
import base64
import hashlib
from pathlib import Path
import tempfile

from quantos.contracts import gold_segmented_input as segmented
from quantos.contracts.registered_research_run import canonical, digest, MAX_INPUT_BYTES
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_public_replay import GOLD_UNIT_BASIS
from services.execution.perpetual_public_replay import prepare_public_replay
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_bar_experiment import JOURNAL_FILES, _bytes, _encode, trial_candidates
from tools.gold_bar_journal import PREFIX_SCHEMA, restore, bind_capture
from tools.gold_cost_review import _read
from tools.perpetual_replay_research import source_identity


def arguments(directory, *, bar_plan, exchange_after, release_identity, quantity,
              initial_collateral, fee_rate, observed_funding_records=None,
              settlement_window_ms=None, continuation=False):
    """Retain all five frozen originals, raw clocks and the original bar plan."""
    root = Path(directory).absolute()
    if type(continuation) is not bool:
        raise ValueError('GOLD_NATIVE_PREFIX_CONTINUATION_MODE')
    if root.resolve() != root:
        raise ValueError('GOLD_NATIVE_PREFIX_PATH')
    if source_identity() != release_identity:
        raise ValueError('GOLD_NATIVE_PREFIX_SOURCE_CHANGED')
    raw = {name:_read(root,name) for name in
           ('summary.json','inputs.json','sources.json','warmup.json','warmup-receipt.json')}
    inputs, summary, plan = (decode(value) for value in
                            (raw['inputs.json'],raw['summary.json'],bar_plan))
    if (inputs.get('quote_source') != release_identity
            or inputs.get('bar_source') != release_identity or plan.get('source') != release_identity):
        raise ValueError('GOLD_NATIVE_PREFIX_SOURCE_BINDING')
    if 'exchange_after' in inputs and exchange_after != inputs['exchange_after']:
        raise ValueError('GOLD_NATIVE_PREFIX_SEGMENT_EXCHANGE_CHANGED')
    if 'observed_funding_records' in inputs:
        retained_funding = inputs['observed_funding_records']
        if observed_funding_records is not None and observed_funding_records != retained_funding:
            raise ValueError('GOLD_NATIVE_PREFIX_SEGMENT_FUNDING_CHANGED')
        observed_funding_records = retained_funding or None
    messages = b''.join(_bytes({name:row[name] for name in ('raw','received_at_ms')})+b'\n'
                        for row in inputs['bar_messages'])
    decisions = b''.join(_bytes(row['decision'])+b'\n' for row in inputs['bar_messages']
                         if row['decision']['status'] == 'CLOSED_BAR')
    files = {'plan.json':bar_plan, 'warmup.json':raw['warmup.json'],
        'warmup-receipt.json':raw['warmup-receipt.json'],
        'messages.jsonl':messages, 'decisions.jsonl':decisions}
    files['summary.json'] = _bytes(dict(schema=PREFIX_SCHEMA, status='FROZEN_PREFIX',
        reason='COMMON_WATERMARK_FROZEN', source_unchanged=True, submission_enabled=False,
        economic_eligible=False, as_of_ms=inputs['as_of_ms'], messages=len(inputs['bar_messages']),
        closed_bars=summary['closed_bars'], messages_sha256=hashlib.sha256(messages).hexdigest(),
        decisions_sha256=hashlib.sha256(decisions).hexdigest(),
        frozen_prefix={name:base64.b64encode(raw[name]).decode() for name in
                       ('summary.json','inputs.json','sources.json')}))
    capture = dict(schema_version='GOLD_PUBLIC_SESSION_PREFIX_V1', symbol='XAUUSDT',
        bootstrap=inputs['bootstrap'], samples=inputs['samples'], exchange_after=exchange_after,
        quantity_basis=dict(GOLD_UNIT_BASIS), max_gap_ms=30000,
        declared_interval_ms=inputs.get('quote_declared_interval_ms',10000),
        prefix_summary_sha256=hashlib.sha256(raw['summary.json']).hexdigest())
    if 'capacity_observations' in inputs:
        capture['capacity_observations'] = inputs['capacity_observations']
    capture['capture_id'] = exact_identity_hash(capture, name='public_replay_capture')
    result = dict(capture=capture, journal_files={name:base64.b64encode(value).decode()
        for name,value in files.items()}, release_identity=release_identity, quantity=quantity,
        initial_collateral=initial_collateral, fee_rate=fee_rate)
    if observed_funding_records is not None:
        result.update(observed_funding_records=observed_funding_records,
                      settlement_window_ms=settlement_window_ms)
    elif settlement_window_ms is not None:
        raise ValueError('GOLD_NATIVE_PREFIX_FUNDING_REQUIRED')
    canonical(_encode(result), maximum=MAX_INPUT_BYTES)
    # Validate actual originals and quote coverage before any trial or request.
    with tempfile.TemporaryDirectory(prefix='quantos-native-prefix-') as directory:
        journal = Path(directory)
        for name,value in files.items():
            (journal/name).write_bytes(value)
        if continuation:
            # Full bar ancestry belongs to the same original account. Its older
            # closes need not occur inside this later quote piece; the original
            # segmented producer verifies all append/cross-piece clocks.
            cursor, review = restore(journal)
            bind_capture(review, capture, observed_funding_records)
            prepared = prepare_public_replay(capture,
                observed_funding_records=observed_funding_records,
                settlement_window_ms=settlement_window_ms)
            warmup = cursor.bars[:-review['closed_bars']]
            if (not review['closed_bars'] or not warmup
                    or max(bar.available_ms for bar in warmup) > prepared['frames'][0].timestamp):
                raise ValueError('GOLD_NATIVE_PREFIX_CONTINUATION_WARMUP')
        else:
            from tools.gold_bar_observed_replay import _prepare_observed
            _prepare_observed(journal, capture, observed_funding_records, settlement_window_ms)
    if (any(_read(root,name) != value for name,value in raw.items())
            or source_identity() != release_identity):
        raise ValueError('GOLD_NATIVE_PREFIX_CHANGED_DURING_PREPARATION')
    if continuation:
        return dict(schema=segmented.SEGMENT_SCHEMA, **{name:result[name] for name in
            ('capture','journal_files','observed_funding_records','settlement_window_ms')
            if name in result})
    return result


def assemble(first, continuations):
    """Freeze one original invocation; configuration stays with its first piece."""
    if type(continuations) is not list or not continuations:
        raise ValueError('GOLD_NATIVE_PREFIX_CONTINUATIONS_REQUIRED')
    if source_identity() != first.get('release_identity'):
        raise ValueError('GOLD_NATIVE_PREFIX_SOURCE_CHANGED')
    if 'input_schema' in first or 'continuation_segments' in first:
        segmented.journals(_encode(first))
    result = dict(first, input_schema=segmented.SCHEMA,
                  continuation_segments=[*first.get('continuation_segments',[]),*continuations])
    segmented.journals(_encode(result))
    return result


def session_parts(quote_directory, bar_directory, output, *, segment_count,
                  release_identity, quantity, initial_collateral, fee_rate,
                  settlement_window_ms=None):
    """Yield original parts, then recheck the complete retained source prefix.

    Every quote in each selected segment must reach the common bar watermark.
    Consumers must exhaust this iterator. It grants no execution admission.
    """
    from quantos.contracts.gold_chunked_source import MAX_PARTS
    from tools.gold_quote_session import read_segment
    from tools.gold_session_prefix import snapshot
    if type(segment_count) is not int or not 1 <= segment_count <= MAX_PARTS:
        raise ValueError('GOLD_NATIVE_SESSION_SEGMENT_COUNT')
    if settlement_window_ms is not None and (type(settlement_window_ms) is not int
            or not 1 <= settlement_window_ms <= 1000):
        raise ValueError('GOLD_NATIVE_SESSION_FUNDING_WINDOW')
    if source_identity() != release_identity:
        raise ValueError('GOLD_NATIVE_PREFIX_SOURCE_CHANGED')
    quotes, bars, root = (Path(value).absolute() for value in
                         (quote_directory, bar_directory, output))
    if any(path.resolve() != path for path in (quotes,bars,root)):
        raise ValueError('GOLD_NATIVE_SESSION_PATH')
    plan = _read(bars,'plan.json')
    quote_plan = _read(quotes,'plan.json')
    from tools.gold_quote_session import BUDGETED_SEGMENT_PLAN
    from tools.gold_bar_session import BUDGETED_SCHEMA, RETAINED_SCHEMA
    if (decode(quote_plan).get('schema') == BUDGETED_SEGMENT_PLAN
            or decode(plan).get('schema') in (BUDGETED_SCHEMA, RETAINED_SCHEMA)):
        from tools.gold_long_prefix import session_parts as long_parts
        yield from long_parts(quotes, bars, root, segment_count=segment_count,
            release_identity=release_identity, quantity=quantity,
            initial_collateral=initial_collateral, fee_rate=fee_rate,
            settlement_window_ms=settlement_window_ms, current_source=source_identity)
        return
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    originals = []
    previous = None
    for index in range(segment_count):
        saved = read_segment(quotes,index)
        original = _read(quotes,f'segment-{index:06d}.json')
        if decode(original) != saved or saved['source'] != release_identity:
            raise ValueError('GOLD_NATIVE_SESSION_SEGMENT_CHANGED')
        segment = saved['segment']
        if previous is not None and previous != segment['samples'][0]:
            raise ValueError('GOLD_NATIVE_SESSION_SHARED_BOUNDARY_CHANGED')
        directory = root/f'prefix-{index:06d}'
        report = snapshot(quotes,bars,directory,quote_segment=index)
        if report['sample_count'] != len(segment['samples']):
            raise ValueError('GOLD_NATIVE_SESSION_BAR_WATERMARK_INCOMPLETE')
        result=arguments(directory,bar_plan=plan,exchange_after=segment['exchange_after'],
            release_identity=release_identity,quantity=quantity,initial_collateral=initial_collateral,
            fee_rate=fee_rate,settlement_window_ms=settlement_window_ms
                if segment['observed_funding_records'] else None,continuation=index>0)
        originals.append(hashlib.sha256(original).hexdigest())
        previous = segment['samples'][-1]
        yield result
    if (_read(bars,'plan.json') != plan or _read(quotes,'plan.json') != quote_plan
            or source_identity() != release_identity):
        raise ValueError('GOLD_NATIVE_SESSION_CHANGED_DURING_PREPARATION')
    for index,expected in enumerate(originals):
        saved=read_segment(quotes,index)
        original=_read(quotes,f'segment-{index:06d}.json')
        if decode(original)!=saved or hashlib.sha256(original).hexdigest()!=expected:
            raise ValueError('GOLD_NATIVE_SESSION_CHANGED_DURING_PREPARATION')


def from_session(quote_directory, bar_directory, output, *, segment_count,
                 release_identity, quantity, initial_collateral, fee_rate,
                 settlement_window_ms=None):
    """Prepare the original bounded native invocation; budgets stay unchanged."""
    if type(segment_count) is not int or not 1<=segment_count<=segmented.MAX_SEGMENTS:
        raise ValueError('GOLD_NATIVE_SESSION_SEGMENT_COUNT')
    parts=session_parts(quote_directory,bar_directory,output,segment_count=segment_count,
        release_identity=release_identity,quantity=quantity,initial_collateral=initial_collateral,
        fee_rate=fee_rate,settlement_window_ms=settlement_window_ms)
    first=next(parts)
    rest=list(parts)
    result=assemble(first,rest) if rest else first
    return result


def write_request(arguments, directory, *, forward_design=None):
    """Write the fixed original job request once, publishing plan.json last."""
    document = _encode(arguments)
    canonical(document, maximum=MAX_INPUT_BYTES)
    trial_candidates(arguments)
    if source_identity() != arguments['release_identity']:
        raise ValueError('GOLD_NATIVE_PREFIX_SOURCE_CHANGED')
    if 'input_schema' in document or 'continuation_segments' in document:
        journals, parts = segmented.journals(document), segmented.documents(document)
    else:
        if set(document)-{'capture','journal_files','quantity','initial_collateral','fee_rate',
                          'release_identity','portfolio_plan','observed_funding_records',
                          'settlement_window_ms'}:
            raise ValueError('GOLD_NATIVE_PREFIX_REQUEST_FIELDS')
        if set(document['journal_files']) != set(JOURNAL_FILES):
            raise ValueError('GOLD_NATIVE_PREFIX_JOURNAL_FIELDS')
        journals = ({name:base64.b64decode(value, validate=True)
                     for name,value in document['journal_files'].items()},)
        parts = (document,)
    with tempfile.TemporaryDirectory(prefix='quantos-native-prefix-review-') as temporary:
        chain_cursor = chain_review = None
        for index,(part,files) in enumerate(zip(parts,journals)):
            root = Path(temporary)/str(index)
            root.mkdir()
            for name,value in files.items():
                (root/name).write_bytes(value)
            cursor, review = restore(root, previous_cursor=chain_cursor, previous_review=chain_review)
            if 'prefix_chain' in review:
                if review['execution_source'] != arguments['release_identity']:
                    raise ValueError('GOLD_NATIVE_PREFIX_EXECUTION_SOURCE_CHANGED')
                chain_cursor, chain_review = cursor, review
            bind_capture(review, part['capture'], part.get('observed_funding_records'))
    plan = {name:document[name] for name in
            ('quantity','initial_collateral','fee_rate','release_identity')}
    plan['register_original_trials'] = True
    raw = {'quotes.json':_bytes(document['capture'])}
    if 'portfolio_plan' in document:
        plan['portfolio_plan'] = document['portfolio_plan']
    if 'observed_funding_records' in document:
        raw['funding.json'] = _bytes(document['observed_funding_records'])
        plan['observed_funding'] = dict(records_sha256=hashlib.sha256(raw['funding.json']).hexdigest(),
            settlement_window_ms=document['settlement_window_ms'])
    if len(parts) > 1:
        raw['segments.json'] = _bytes(document['continuation_segments'])
        plan['segmented_input'] = dict(schema=segmented.SCHEMA,
            segments_sha256=hashlib.sha256(raw['segments.json']).hexdigest())
    if forward_design is not None:
        from quantos.contracts.gold_forward_evaluation_design import validate
        plan['forward_design'] = validate(forward_design)
        if plan['forward_design']['source_identity_sha256'] != digest(document['release_identity']):
            raise ValueError('GOLD_NATIVE_PREFIX_FORWARD_SOURCE')
        configuration = {name:document[name] for name in ('quantity','initial_collateral',
            'fee_rate','portfolio_plan','settlement_window_ms') if name in document}
        if plan['forward_design']['configuration'] != configuration:
            raise ValueError('GOLD_NATIVE_PREFIX_FORWARD_CONFIGURATION')
        if 'settlement_window_ms' in document:
            plan['settlement_window_ms'] = document['settlement_window_ms']
    if source_identity() != arguments['release_identity']:
        raise ValueError('GOLD_NATIVE_PREFIX_SOURCE_CHANGED')
    root = Path(directory).absolute()
    if root.resolve() != root:
        raise ValueError('GOLD_NATIVE_PREFIX_REQUEST_PATH')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root/'journal').mkdir(mode=0o700)
    for name,value in journals[0].items():
        with (root/'journal'/name).open('xb') as handle:
            handle.write(value)
    for name,value in raw.items():
        with (root/name).open('xb') as handle:
            handle.write(value)
    with (root/'plan.json').open('xb') as handle:
        handle.write(_bytes(plan))
    return plan
