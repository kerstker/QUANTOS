"""Original continuous observations must reach the native ledger truthfully."""
import base64
from copy import deepcopy
from decimal import Decimal
import hashlib
import json

import pytest

from tests.tools.test_gold_session_prefix import inputs
from tests.execution.test_perpetual_public_replay import sealed
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def frozen(tmp_path, monkeypatch, source=None):
    from tools.gold_session_prefix import snapshot

    quotes, bars, capture, messages = inputs(tmp_path, monkeypatch)
    source = source or {'fixture': True}
    for directory in (quotes, bars):
        plan = json.loads((directory/'plan.json').read_bytes())
        plan['source'] = source
        if directory == bars:
            plan.update(seconds=86400,
                url='wss://fstream.binance.com/market/stream?streams=xauusdt@kline_15m')
        (directory/'plan.json').write_text(json.dumps(plan))
    # The common watermark includes the next quote after the second bar receipt.
    last = messages[-1]
    received = capture['samples'][-1]['depth']['received_at']
    from tools.gold_bar_session import _cursor
    cursor = _cursor((bars/'warmup.json').read_bytes(),
                     json.loads((bars/'warmup-receipt.json').read_bytes()))
    for message in messages:
        cursor.ingest(message['raw'], received_at_ms=message['received_at_ms'])
    raw = json.loads(last['raw'])
    raw['data']['k']['x'] = False
    raw['data']['E'] = received-1
    raw = json.dumps(raw)
    row = dict(raw=raw, received_at_ms=received,
               decision=cursor.ingest(raw, received_at_ms=received))
    with (bars/'messages-0000.jsonl').open('a') as handle:
        handle.write(json.dumps(row)+'\n')
    prefix = tmp_path/'prefix'
    snapshot(quotes, bars, prefix)
    return prefix, bars, capture, source


def arguments(tmp_path, monkeypatch):
    from tools import gold_native_prefix as bridge

    prefix, bars, capture, source = frozen(tmp_path, monkeypatch)
    monkeypatch.setattr(bridge, 'source_identity', lambda: source)
    result = bridge.arguments(prefix, bar_plan=(bars/'plan.json').read_bytes(),
        exchange_after=capture['exchange_after'], release_identity=source,
        quantity=Decimal('.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('.001'))
    return result, prefix


def test_original_continuous_prefix_runs_original_three_accounts_without_completion_claim(
        tmp_path, monkeypatch):
    from tools.gold_bar_experiment import _execute
    args, prefix = arguments(tmp_path, monkeypatch)
    originals = {p.name:p.read_bytes() for p in prefix.iterdir()}
    result = _execute(args)
    assert len(result['trials']) == 3
    channel = result['trials'][0]['simulation']
    assert len(channel['orders']) == 2
    assert channel['reconciliation']['initial_equity'] == '1000'
    assert channel['ending_state']['cashflow_identity']['residual'] == '0'
    summary = json.loads(base64.b64decode(args['journal_files']['summary.json']))
    assert summary['status'] == 'FROZEN_PREFIX'
    assert summary['reason'] == 'COMMON_WATERMARK_FROZEN'
    assert result['economic_eligible'] is result['submission_enabled'] is False
    assert originals == {p.name:p.read_bytes() for p in prefix.iterdir()}


@pytest.mark.parametrize('damage', ['input', 'source', 'future', 'capture', 'counter', 'completed', 'plan'])
def test_native_prefix_cold_consumer_rejects_changed_originals_and_false_completion(
        tmp_path, monkeypatch, damage):
    from tools.gold_bar_experiment import _execute
    args, _ = arguments(tmp_path, monkeypatch)
    summary = json.loads(base64.b64decode(args['journal_files']['summary.json']))
    if damage == 'plan':
        plan = json.loads(base64.b64decode(args['journal_files']['plan.json']))
        plan['seconds'] -= 1
        args['journal_files']['plan.json'] = base64.b64encode(json.dumps(plan).encode()).decode()
    elif damage == 'capture':
        capture = deepcopy(args['capture'])
        capture['samples'][0]['depth']['payload']['lastUpdateId'] += 1
        args['capture'] = sealed(capture)
    elif damage in ('input','source'):
        name = 'inputs.json' if damage == 'input' else 'sources.json'
        raw = base64.b64decode(summary['frozen_prefix'][name])+b' '
        summary['frozen_prefix'][name] = base64.b64encode(raw).decode()
    elif damage == 'future':
        summary['as_of_ms'] -= 2_000_000
    elif damage == 'counter':
        summary['messages'] += 1
    else:
        summary['status'], summary['reason'] = 'OBSERVED', 'DURATION_COMPLETE'
    args['journal_files']['summary.json'] = base64.b64encode(json.dumps(summary).encode()).decode()
    with pytest.raises(ValueError):
        _execute(args)


def test_adapter_preserves_current_source_and_native_input_budget(tmp_path, monkeypatch):
    from tools import gold_native_prefix as bridge
    prefix, bars, capture, source = frozen(tmp_path, monkeypatch)
    kwargs = dict(bar_plan=(bars/'plan.json').read_bytes(), exchange_after=capture['exchange_after'],
        release_identity=source, quantity=Decimal('.1'), initial_collateral=Decimal('1000'),
        fee_rate=Decimal('.001'))
    monkeypatch.setattr(bridge, 'source_identity', lambda: {'changed':True})
    with pytest.raises(ValueError, match='SOURCE'):
        bridge.arguments(prefix, **kwargs)
    monkeypatch.setattr(bridge, 'source_identity', lambda: source)
    monkeypatch.setattr(bridge, 'MAX_INPUT_BYTES', 100)
    with pytest.raises(ValueError):
        bridge.arguments(prefix, **kwargs)


def test_native_request_writer_uses_existing_plan_and_exact_segment_digest(tmp_path, monkeypatch):
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_research import read_arguments
    from tools.gold_bar_experiment import _encode

    args, _ = arguments(tmp_path, monkeypatch)
    request = tmp_path/'request'
    bridge.write_request(args, request)
    assert _encode(read_arguments(request, original_source=args['release_identity'])) == _encode(args)
    plan = json.loads((request/'plan.json').read_bytes())
    assert plan['register_original_trials'] is True
    with pytest.raises(FileExistsError):
        bridge.write_request(args, request)
    from tests.tools.test_gold_segmented_replay import segmented_arguments
    directory = tmp_path/'segmented'
    directory.mkdir()
    segmented, _, _ = segmented_arguments(directory, monkeypatch)
    bridge.write_request(segmented, tmp_path/'segmented-request')
    raw = (tmp_path/'segmented-request/segments.json').read_bytes()
    plan = json.loads((tmp_path/'segmented-request/plan.json').read_bytes())
    assert plan['segmented_input']['segments_sha256'] == hashlib.sha256(raw).hexdigest()
    assert _encode(read_arguments(tmp_path/'segmented-request',
                   original_source=segmented['release_identity'])) == _encode(segmented)


@pytest.mark.parametrize('declared', [False, True])
def test_original_queue_registers_prefix_once_and_recovers_retained_results(
        tmp_path, monkeypatch, technical_arguments, declared):
    import time
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tests.research.test_registered_research_job import queued
    from tools import gold_native_prefix as bridge, gold_bar_experiment, gold_bar_research
    from tools import registered_gold_collection as collected, registered_research_job as job
    from tools import registered_research_run as native
    source = technical_arguments['release_identity']
    prefix, bars, capture, _ = frozen(tmp_path, monkeypatch, source)
    for module in (bridge, gold_bar_experiment, gold_bar_research):
        monkeypatch.setattr(module, 'source_identity', lambda: source)
    monkeypatch.setattr(time, 'time_ns', lambda:
                        (capture['exchange_after']['received_at']+1000)*1_000_000)
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    args = bridge.arguments(prefix, bar_plan=(bars/'plan.json').read_bytes(),
        exchange_after=capture['exchange_after'], release_identity=source,
        quantity=Decimal('.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('.001'))
    request = tmp_path/'data/cache/gold_bar_research/requests/1'
    design = None
    if declared:
        from quantos.contracts.gold_forward_evaluation_design import declare
        messages = base64.b64decode(args['journal_files']['messages.jsonl']).splitlines()
        close = json.loads(json.loads(messages[0])['raw'])['data']['k']['T']+1
        design = declare(program_id=1, attempt=1, source_identity=source,
            configuration={name:str(args[name]) for name in ('quantity','initial_collateral','fee_rate')},
            first_close_ms=close, window_bars=2, declared_at_ms=close-1_100_000,
            selected_candidate='channel', proposal_sha256='a'*64)
    bridge.write_request(args, request, forward_design=design)
    original, calls = native._native_run, []
    def observed(**kwargs):
        producer = kwargs['execute']
        def execute():
            calls.append(1)
            return producer()
        return original(**{**kwargs, 'execute':execute})
    monkeypatch.setattr(native, '_native_run', observed)
    registry, queue = queued(tmp_path)
    uid = queue.submit('gold_bar_research', {'attempt':1}, 1, invocation_id='prefix-native')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    project, database = job.workspace_paths()
    cut = ProjectResearchEvidenceReader(project, registry_database=database)()
    assert cut['attempt_count'] == 3
    report = project/'runs'/collected.invocation_id(1)/'archive/report.json'
    before = report.read_bytes(), database.read_bytes()
    assert len(json.loads(before[0])['trials'][0]['simulation']['orders']) == 2
    assert calls == [1]
    uid = queue.submit('gold_bar_recover', {'attempt':1}, 2, invocation_id='prefix-recover')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert calls == [1] and before == (report.read_bytes(), database.read_bytes())


def test_original_chunk_and_receipt_collectors_feed_the_native_bridge(tmp_path, monkeypatch):
    import asyncio
    from tools import gold_bar_session as bars, gold_quote_session as quotes
    from tools import gold_native_prefix as bridge
    from tools.gold_session_prefix import snapshot
    from tools.gold_bar_experiment import _execute
    prefix, _, capture, source = frozen(tmp_path, monkeypatch)
    original = json.loads((prefix/'inputs.json').read_bytes())
    history = tmp_path/'collector-history'
    history.mkdir()
    (history/'response.json').write_bytes((prefix/'warmup.json').read_bytes())
    (history/'receipt.json').write_bytes((prefix/'warmup-receipt.json').read_bytes())
    for module in (bars,quotes,bridge):
        monkeypatch.setattr(module, 'source_identity', lambda: source)
    async def transport(seconds, state):
        state['connected'] = True
        for row in original['bar_messages']:
            yield row['raw'], row['received_at_ms']
        await asyncio.sleep(10)
    monkeypatch.setattr(bars, '_messages', transport)
    monkeypatch.setattr(bars, 'CHUNK_MESSAGES', 1)
    summary = asyncio.run(bars.observe(history, tmp_path/'collected-bars', seconds=1))
    assert summary['status'] == 'OBSERVED' and summary['chunks'] == 3
    def quote_transport(**kwargs):
        for kind in ('exchange','funding','candles'):
            kwargs['on_record'](original['bootstrap'][kind])
        for sample in original['samples']:
            for kind in ('mark','depth'):
                kwargs['on_record'](sample[kind])
        return dict(sample_count=len(original['samples']), quote_coverage={'gap_count':0})
    monkeypatch.setattr(quotes, 'capture_gold_session', quote_transport)
    quotes.run(tmp_path/'collected-quotes', seconds=1800)
    snapshot(tmp_path/'collected-quotes',tmp_path/'collected-bars',tmp_path/'collected-prefix')
    args = bridge.arguments(tmp_path/'collected-prefix',
        bar_plan=(tmp_path/'collected-bars/plan.json').read_bytes(),
        exchange_after=capture['exchange_after'], release_identity=source,
        quantity=Decimal('.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('.001'))
    result = _execute(args)
    assert len(result['trials'][0]['simulation']['orders']) == 2
    assert result['trials'][0]['simulation']['ending_state']['cashflow_identity']['residual'] == '0'
