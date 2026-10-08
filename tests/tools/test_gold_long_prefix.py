"""Long original observation journals must enter the existing native producer."""
import base64
from copy import deepcopy
from decimal import Decimal
import hashlib
import json

import pytest

from tests.tools.test_gold_public_segment_producer import native_session
from tests.tools.test_registered_research_run import registered_case as registered_case


def budget():
    return dict(schema='GOLD_PUBLIC_COLLECTION_BUDGET_V1', maximum_duration_seconds=32*86400,
        maximum_quote_bytes=64*1024**3, maximum_quote_receipts=8_000_000,
        maximum_quote_segments=4096, maximum_bar_bytes=16*1024**3,
        maximum_bar_messages=16_000_000, maximum_bar_connections=128)


def original_session(tmp_path, monkeypatch, execution=None, *, dense=False, large=False, retain_late_open=False):
    from tools import gold_quote_session as quotes, gold_native_prefix as bridge
    from tools.gold_bar_session import _cursor, _encode, CHUNK_BYTES, CHUNK_MESSAGES, LATE_OPEN_POLICY
    execution = execution or {'fixture_execution': True}
    collector = {'fixture_collector': True}
    old = quotes.run
    monkeypatch.setattr(quotes, 'run', lambda *a, **k: old(*a, **k, collection_budget=budget()))
    if large:
        from tests.tools import test_gold_native_prefix as original_fixture
        frozen = original_fixture.frozen
        def full_response(*args, **kwargs):
            result = frozen(*args, **kwargs)
            result[2]['bootstrap']['exchange']['payload']['venue_originals'] = [
                dict(field=i, metadata=dict(raw=str(i))) for i in range(25_000)]
            return result
        monkeypatch.setattr(original_fixture, 'frozen', full_response)
    bars, _, result = native_session(tmp_path, monkeypatch, collector)
    plan = json.loads((bars/'plan.json').read_bytes())
    plan.update(schema='GOLD_CONTINUOUS_BAR_SESSION_V2', seconds=32*86400,
                collection_budget=budget())
    if retain_late_open:
        plan.update(schema='GOLD_CONTINUOUS_BAR_SESSION_V3', late_open_policy=LATE_OPEN_POLICY)
    (bars/'plan.json').write_text(json.dumps(plan))
    if dense or retain_late_open:
        rows = [json.loads(line) for line in (bars/'messages-0000.jsonl').read_bytes().splitlines()]
        first, second = [r for r in rows if r['decision']['status'] == 'CLOSED_BAR']
        template = json.loads(rows[-1]['raw'])
        for i in range(25_000 if dense else 1):
            received = first['received_at_ms']+1+i*30
            assert received < second['received_at_ms']
            wire = deepcopy(template)
            wire['data']['E'] = received - (30756 if retain_late_open else 1)
            wire['data']['k']['x'] = False
            rows.append(dict(raw=json.dumps(wire), received_at_ms=received))
        rows.sort(key=lambda row: row['received_at_ms'])
        cursor = _cursor((bars/'warmup.json').read_bytes(),
                         json.loads((bars/'warmup-receipt.json').read_bytes()),
                         late_open_policy=LATE_OPEN_POLICY if retain_late_open else None)
        (bars/'messages-0000.jsonl').unlink()
        index, chunk, count = 0, bytearray(), 0
        for row in rows:
            row['decision'] = cursor.ingest(row['raw'], received_at_ms=row['received_at_ms'])
            raw = _encode(row)
            if chunk and (len(chunk)+len(raw) > CHUNK_BYTES or count >= CHUNK_MESSAGES):
                (bars/f'messages-{index:04d}.jsonl').write_bytes(chunk)
                index, chunk, count = index+1, bytearray(), 0
            chunk.extend(raw)
            count += 1
        (bars/f'messages-{index:04d}.jsonl').write_bytes(chunk)
    monkeypatch.setattr(bridge, 'source_identity', lambda: execution)
    return bars, result, collector, execution


def parts(tmp_path, monkeypatch, execution=None, *, dense=False, large=False, retain_late_open=False):
    from tools import gold_native_prefix as bridge
    bars, collected, collector, execution = original_session(
        tmp_path, monkeypatch, execution, dense=dense, large=large, retain_late_open=retain_late_open)
    values = list(bridge.session_parts(tmp_path/'session', bars, tmp_path/'prefixes',
        segment_count=collected['segment_count'], release_identity=execution,
        quantity=Decimal('.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('.001')))
    return values, bars, collector, execution


def test_v2_collection_with_different_execution_version_preserves_every_wire_once(
        tmp_path, monkeypatch):
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute
    values, bars, collector, execution = parts(tmp_path, monkeypatch, dense=True)
    summaries = [json.loads(base64.b64decode(p['journal_files']['summary.json'])) for p in values]
    assert all(s['schema'] == 'GOLD_BAR_FROZEN_DELTA_SUMMARY_V2' for s in summaries)
    assert all(s['execution_source'] == execution for s in summaries)
    assert all(json.loads(base64.b64decode(p['journal_files']['plan.json']))['source'] == collector
               for p in values)
    actual = b''.join(base64.b64decode(p['journal_files']['messages.jsonl'])
                     for p,s in zip(values,summaries,strict=True) if s['messages'])
    expected = b''.join(line+b'\n' for path in sorted(bars.glob('messages-*.jsonl'))
        for line in path.read_bytes().splitlines()
        if json.loads(line)['received_at_ms'] <= summaries[-1]['as_of_ms'])
    assert actual == expected and len(expected.splitlines()) > 20_000
    assert sum(s['messages'] for s in summaries) == summaries[-1]['prefix_messages']
    result = _execute(bridge.assemble(values[0], values[1:]))
    channel = result['trials'][0]['simulation']
    assert channel['reconciliation']['initial_equity'] == '1000'
    assert channel['ending_state']['cashflow_identity']['residual'] == '0'
    assert channel['ending_state']['cashflow_identity']['fee'] == '0.02'
    assert result['dataset_identity']['journal_source'] == collector
    assert result['release_identity'] == execution
    assert result['economic_eligible'] is result['submission_enabled'] is False


@pytest.mark.parametrize('damage', ['budget', 'collector', 'source-downgrade'])
def test_v2_plan_mismatch_rejects_before_output(tmp_path, monkeypatch, damage):
    from tools import gold_native_prefix as bridge
    bars, collected, _, execution = original_session(tmp_path, monkeypatch)
    plan = json.loads((bars/'plan.json').read_bytes())
    if damage == 'budget':
        plan['collection_budget']['maximum_bar_messages'] -= 1
    elif damage == 'collector':
        plan['source'] = execution
    else:
        plan['schema'] = 'GOLD_CONTINUOUS_BAR_SESSION_V1'
    (bars/'plan.json').write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        list(bridge.session_parts(tmp_path/'session', bars, tmp_path/'prefixes',
            segment_count=collected['segment_count'], release_identity=execution,
            quantity=Decimal('.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('.001')))
    assert not (tmp_path/'prefixes').exists()


@pytest.mark.parametrize('retain_late_open', [False, True])
def test_original_native_source_binds_all_delta_closes_and_complete_wire_digest(
        tmp_path, monkeypatch, retain_late_open):
    from tools import gold_chunked_source as source
    values, bars, collector, execution = parts(tmp_path, monkeypatch, retain_late_open=retain_late_open)
    monkeypatch.setattr(source, 'source_identity', lambda: execution)
    root = tmp_path/'frozen-source'
    source.freeze(iter(values), root, maximum_bytes=64_000_000)
    expected = []
    wire = hashlib.sha256()
    for part in values:
        raw = base64.b64decode(part['journal_files']['messages.jsonl'])
        summary = json.loads(base64.b64decode(part['journal_files']['summary.json']))
        if summary['messages']:
            wire.update(raw)
        for row in raw.splitlines():
            if not row:
                continue
            entry = json.loads(row)
            if entry['decision']['status'] == 'CLOSED_BAR':
                expected.append([entry['decision']['close_ms'], entry['received_at_ms']])
    if retain_late_open:
        retained = [json.loads(row) for path in bars.glob('messages-*.jsonl')
                    for row in path.read_bytes().splitlines()]
        rejected, = [row for row in retained if row['decision']['status'] == 'REJECTED_LATE_OPEN_BAR']
        assert rejected['decision']['delay_ms'] == 30756
        originals = b''.join(base64.b64decode(part['journal_files']['messages.jsonl'])
                            for part in values if base64.b64decode(part['journal_files']['messages.jsonl']) != b'\n')
        final_summary = json.loads(base64.b64decode(values[-1]['journal_files']['summary.json']))
        expected_raw = b''.join(line + b'\n' for path in sorted(bars.glob('messages-*.jsonl'))
            for line in path.read_bytes().splitlines()
            if json.loads(line)['received_at_ms'] <= final_summary['as_of_ms'])
        assert originals == expected_raw
        assert sum(json.loads(row)['decision']['status'] == 'REJECTED_LATE_OPEN_BAR'
                   for row in originals.splitlines()) == 1
    with source.NativeSource(root) as frozen:
        assert frozen.document['forward_input']['closed_observations'] == expected
        assert len(expected) == 2
        assert frozen.document['forward_input']['journal_messages_sha256'] == wire.hexdigest()
        assert frozen.document['release_identity'] == execution != collector


def test_existing_request_writer_validates_delta_chain_in_order(tmp_path, monkeypatch):
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _encode
    from tools.gold_bar_research import read_arguments
    values, _, _, execution = parts(tmp_path, monkeypatch)
    arguments = bridge.assemble(values[0], values[1:])
    bridge.write_request(arguments, tmp_path/'request')
    assert _encode(read_arguments(tmp_path/'request', original_source=execution)) == _encode(arguments)


def test_cold_delta_continuation_cannot_change_the_retained_policy(tmp_path, monkeypatch):
    from tools import gold_prefix_journal as journal
    from tools import gold_bar_session as bars
    values, _, _, _ = parts(tmp_path, monkeypatch, retain_late_open=True)
    first, second = [{name: base64.b64decode(body) for name, body in part['journal_files'].items()}
                     for part in values[:2]]
    cursor, review = journal.restore(first)
    plan = json.loads(second['plan.json'])
    plan['schema'] = bars.BUDGETED_SCHEMA
    plan.pop('late_open_policy')
    second['plan.json'] = json.dumps(plan).encode()
    with pytest.raises(ValueError, match='ANCESTRY'):
        journal.restore(second, previous_cursor=cursor, previous_review=review)


def test_single_piece_consumer_rejects_wrong_execution_identity_before_account(tmp_path, monkeypatch):
    from tools import gold_bar_observed_replay as replay
    values, _, _, _ = parts(tmp_path, monkeypatch)
    root = tmp_path/'single-journal'
    root.mkdir()
    for name, encoded in values[0]['journal_files'].items():
        (root/name).write_bytes(base64.b64decode(encoded))
    calls = []
    monkeypatch.setattr(replay, '_compare_bars', lambda **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError, match='EXECUTION_SOURCE'):
        replay.compare_observed(journal=root, capture=values[0]['capture'], release_identity={'wrong':True},
            quantity=Decimal('.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('.001'))
    assert not calls


def test_full_exchange_original_uses_bounded_raw_carrier_without_widening_generic_json(
        tmp_path, monkeypatch):
    from quantos.contracts.registered_research_run import canonical
    from tools import gold_chunked_source as source
    from tools.gold_stream_history import GoldResearchHistory
    values, _, _, execution = parts(tmp_path, monkeypatch, large=True)
    payload = values[0]['capture']['bootstrap']['exchange']['payload']
    assert len(payload['venue_originals']) == 25_000
    with pytest.raises(ValueError, match='COMPLEXITY'):
        canonical(values[0]['capture'])
    monkeypatch.setattr(source, 'source_identity', lambda: execution)
    root = tmp_path/'full-original-inputs'
    source.freeze(iter(values), root, maximum_bytes=64_000_000)
    with source.open_source(root) as saved:
        restored = list(saved.parts())
        assert restored[0]['capture']['bootstrap']['exchange']['payload'] == payload
    with GoldResearchHistory(tmp_path/'accounts-history', maximum_history_bytes=256_000_000) as history:
        with source.compare_source(root, tmp_path/'prepared', maximum_preparation_bytes=128_000_000,
                history=history) as report:
            assert all(t['simulation']['reconciliation']['residual'] == '0' for t in report['trials'])
            assert report['economic_eligible'] is report['submission_enabled'] is False


@pytest.mark.parametrize('damage', ['omit', 'duplicate', 'wire-clock', 'execution', 'false-completion'])
def test_cold_delta_chain_rejects_omitted_originals_and_false_provenance(tmp_path, monkeypatch, damage):
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute
    values, _, _, _ = parts(tmp_path, monkeypatch)
    if damage == 'omit':
        values.pop(1)
    elif damage == 'duplicate':
        values.insert(1, deepcopy(values[1]))
    else:
        files = values[-1 if damage == 'wire-clock' else 1]['journal_files']
        summary = json.loads(base64.b64decode(files['summary.json']))
        if damage == 'wire-clock':
            raw = base64.b64decode(files['messages.jsonl'])
            assert raw != b'\n'
            rows = [json.loads(row) for row in raw.splitlines()]
            rows[0]['received_at_ms'] = summary['as_of_ms']+1
            changed = b''.join(json.dumps(row).encode()+b'\n' for row in rows)
            files['messages.jsonl'] = base64.b64encode(changed).decode()
            summary['messages_sha256'] = hashlib.sha256(changed).hexdigest()
        elif damage == 'execution':
            summary['execution_source'] = {'wrong': True}
        else:
            summary['status'], summary['reason'] = 'OBSERVED', 'DURATION_COMPLETE'
        files['summary.json'] = base64.b64encode(json.dumps(summary).encode()).decode()
    with pytest.raises(ValueError):
        _execute(bridge.assemble(values[0], values[1:]))


@pytest.mark.parametrize('retain_late_open', [False, True])
def test_original_queue_and_cold_project_consumer_accept_v2_delta_originals(
        registered_case, tmp_path, monkeypatch, retain_late_open):
    import time
    from tools import gold_chunked_source as source, registered_gold_chunked_job as job
    from tools import registered_research_job as workspace
    from tests.research.test_registered_research_job import queued
    from tests.tools.test_registered_gold_chunked_result import storage
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from quantos.contracts.project_chunked_results import report_for
    f = registered_case
    wire = tmp_path/'wire'
    wire.mkdir()
    values, _, collector, execution = parts(wire, monkeypatch, f['arguments']['release_identity'],
                                          retain_late_open=retain_late_open)
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(f['root']))
    monkeypatch.setattr(source, 'source_identity', lambda: execution)
    inputs = tmp_path/'original-inputs'
    source.freeze(iter(values), inputs, maximum_bytes=64_000_000)
    plan = job.prepare_source_run(inputs, search_id='v2-original-delta-chain', dataset_version='synthetic-v2',
        registered_at_ms=time.time_ns()//1_000_000, invocation_id='d'*64, result_storage=storage(),
        history_storage=dict(schema='QUANTOS_GOLD_HISTORY_STORAGE_V1', maximum_bytes=256_000_000),
        source_storage=dict(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2', maximum_bytes=64_000_000,
            maximum_preparation_bytes=128_000_000, replay_budget=dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1',
                maximum_environment_segments=len(values), maximum_elapsed_ms=30*86_400_000)))
    producer, calls = f['run'].gold._execute, []
    def actual(arguments, **kwargs):
        calls.append(1)
        return producer(arguments, **kwargs)
    monkeypatch.setattr(f['run'].gold, '_execute', actual)
    registry, queue = queued(f['root'])
    params = dict(native_invocation_id=plan['invocation_id'])
    uid = queue.submit('registered_gold_chunked', params, time.time_ns()//1_000_000, invocation_id='v2-delta-first')
    queue.join()
    first = registry.get(uid)
    assert first.status == 'done', first.error
    project, database = workspace.workspace_paths()
    directory = project/'runs'/plan['invocation_id']
    reader = ProjectResearchEvidenceReader(project, registry_database=database)
    with reader.evidence() as (cut, reports):
        run = cut['runs'][plan['invocation_id']]
        assert run['input_material']['status'] == 'COMPLETE'
        assert run['input_material']['part_count'] == len(values)
        report = report_for(run, reports)
        assert dict(report['dataset_identity']['journal_source']) == collector
        assert len(report['dataset_identity']['closed_decision_times']) == 2
        for engine in [report['cash_baseline'], *(t['simulation'] for t in report['trials'])]:
            assert engine['reconciliation']['initial_equity'] == '1000'
            assert engine['reconciliation']['residual'] == '0'
    before = {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    uid = queue.submit('registered_gold_chunked_recover', params, time.time_ns()//1_000_000,
                       invocation_id='v2-delta-recover')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert registry.get(uid).metrics == first.metrics and calls == [1]
    assert {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()} == before


def test_invalid_open_wire_is_still_rejected_instead_of_discarded(tmp_path, monkeypatch):
    from tools import gold_native_prefix as bridge
    bars, collected, _, execution = original_session(tmp_path, monkeypatch, dense=True)
    path = bars/'messages-0000.jsonl'
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    row = next(r for r in rows if r['decision']['status'] == 'OPEN_BAR')
    wire = json.loads(row['raw'])
    wire['data']['E'] = row['received_at_ms']+1
    row['raw'] = json.dumps(wire)
    path.write_bytes(b''.join(json.dumps(r).encode()+b'\n' for r in rows))
    with pytest.raises(ValueError, match='INVALID_BAR_EVENT_CLOCK'):
        list(bridge.session_parts(tmp_path/'session', bars, tmp_path/'prefixes',
            segment_count=collected['segment_count'], release_identity=execution,
            quantity=Decimal('.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('.001')))
