"""Prospective long collection cannot turn existing observations into declarations."""
import copy
import asyncio
import json

import pytest

from tests.contracts.test_gold_forward_evaluation_design import SOURCE, START, CONFIG, INTERVAL
from tests.tools.test_gold_long_collection import budget
from tests.tools.test_registered_gold_chunked_result import storage
from tests.tools.test_registered_research_run import registered_case as registered_case


def options():
    return dict(program_id=1, attempt=1, first_close_ms=START, window_bars=2881,
        selected_candidate='channel', proposal_sha256='a'*64,
        configuration=dict(copy.deepcopy(CONFIG), settlement_window_ms=1000), collection_budget=budget(),
        result_storage=storage(),
        history_storage=dict(schema='QUANTOS_GOLD_HISTORY_STORAGE_V1', maximum_bytes=256_000_000),
        source_storage=dict(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2',
            maximum_bytes=32_000_000, maximum_preparation_bytes=64_000_000,
            replay_budget=dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1',
                maximum_environment_segments=4096, maximum_elapsed_ms=32*86_400_000)))


def prepare(directory, monkeypatch, **changes):
    from tools import gold_forward_collection as coordinator
    now = START-INTERVAL-180_000
    monkeypatch.setattr(coordinator, 'source_identity', lambda: copy.deepcopy(SOURCE))
    monkeypatch.setattr(coordinator.time, 'time_ns', lambda: now*1_000_000)
    return coordinator, coordinator.prepare(directory, **dict(options(), **changes))


def test_monthly_plan_is_saved_before_any_public_history_or_worker(tmp_path, monkeypatch):
    coordinator, plan = prepare(tmp_path/'future', monkeypatch)
    assert plan['schema'] == 'GOLD_FORWARD_COLLECTION_PLAN_V2'
    design = plan['forward_design']
    assert design['schema'] == 'QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V4'
    assert len(design['window']['closed_bars']) == 2881
    assert plan['source'] == SOURCE
    assert plan['seconds'] == 2881*INTERVAL//1000+40
    assert plan['interval_seconds'] == 10 and plan['segment_samples'] == 120
    assert design['declared_at_ms'] < design['window']['observation_start_ms']
    assert coordinator.read_plan(tmp_path/'future') == plan
    assert set(p.name for p in (tmp_path/'future').iterdir()) == {'plan.json'}
    assert plan['economic_authority'] is plan['submission_enabled'] is False


def test_new_long_collection_requires_explicit_funding_policy_before_output(tmp_path, monkeypatch):
    value = options()
    value['configuration'].pop('settlement_window_ms')
    with pytest.raises(ValueError, match='FUNDING_WINDOW_REQUIRED'):
        prepare(tmp_path/'missing-funding-policy', monkeypatch, **value)
    assert not (tmp_path/'missing-funding-policy').exists()


@pytest.mark.parametrize('damage', ['late', 'duration', 'epochs', 'legacy_source', 'unselected'])
def test_bad_window_or_existing_resource_limits_reject_before_output(tmp_path, monkeypatch, damage):
    value = options()
    if damage == 'late':
        value['first_close_ms'] -= INTERVAL
    elif damage == 'duration':
        value['collection_budget']['maximum_duration_seconds'] = 30*86400
    elif damage == 'epochs':
        value['source_storage']['replay_budget']['maximum_environment_segments'] = 1
    elif damage == 'legacy_source':
        value['source_storage'].pop('replay_budget')
        value['source_storage']['schema'] = 'QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V1'
    else:
        value.update(selected_candidate=None, proposal_sha256=None)
    with pytest.raises(ValueError):
        prepare(tmp_path/'invalid', monkeypatch, **value)
    assert not (tmp_path/'invalid').exists()


def test_retained_plan_cannot_change_declaration_budget_source_or_claim_authority(tmp_path, monkeypatch):
    coordinator, original = prepare(tmp_path/'future', monkeypatch)
    for damage in ('source', 'declaration', 'budget', 'authority'):
        altered = copy.deepcopy(original)
        if damage == 'source':
            altered['source']['commit'] = '0'*40
        elif damage == 'declaration':
            altered['forward_design']['declared_at_ms'] += 1
        elif damage == 'budget':
            altered['source_storage']['maximum_bytes'] += 1
        else:
            altered['economic_authority'] = True
        (tmp_path/'future/plan.json').write_text(json.dumps(altered))
        with pytest.raises(ValueError):
            coordinator.read_plan(tmp_path/'future')


@pytest.mark.parametrize('status', ['RAW_PAIRED_SESSION_COLLECTED', 'INCOMPLETE'])
def test_collection_waits_for_declared_open_and_retains_original_worker_outcome(tmp_path, monkeypatch, status):
    coordinator, plan = prepare(tmp_path/'future', monkeypatch, window_bars=1)
    clock = [plan['forward_design']['declared_at_ms']]
    monkeypatch.setattr(coordinator.time, 'time_ns', lambda: clock[0]*1_000_000)
    calls = []

    async def advance(seconds):
        clock[0] += int(seconds*1000)

    def history(directory):
        assert clock[0] == plan['forward_design']['window']['observation_start_ms']
        assert coordinator.read_plan(tmp_path/'future') == plan
        calls.append('history')
        directory.mkdir(mode=0o700)
        coordinator.original._put(directory/'receipt.json', dict(
            requested_at_ms=clock[0], received_at_ms=clock[0]+10))
        clock[0] += 10

    async def pair(history, directory, **options):
        assert calls == ['history']
        assert (tmp_path/'future/collection.json').is_file()
        assert options['quote_transport'] == 'POOLED_RETAINED_GAPS_V1'
        assert options['seconds'] == 940
        calls.append('pair')
        directory.mkdir(mode=0o700)
        outcome = dict(status=status, economic_eligible=False, submission_enabled=False)
        coordinator.original._put(directory/'summary.json', outcome)
        return outcome

    monkeypatch.setattr(coordinator.asyncio, 'sleep', advance)
    monkeypatch.setattr(coordinator, 'capture', history)
    monkeypatch.setattr(coordinator.public, 'run', pair)
    result = asyncio.run(coordinator.collect(tmp_path/'future'))
    assert calls == ['history', 'pair']
    assert result['paired_status'] == status
    assert result['economic_authority'] is result['submission_enabled'] is False
    original = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
                for p in (tmp_path/'future').rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='ALREADY_STARTED'):
        asyncio.run(coordinator.collect(tmp_path/'future'))
    assert {p.relative_to(tmp_path).as_posix(): p.read_bytes()
            for p in (tmp_path/'future').rglob('*') if p.is_file()} == original


@pytest.mark.parametrize('damage', ['missed', 'source', 'rollback'])
def test_collection_rejects_changed_source_missed_window_or_clock_rollback_before_history(
        tmp_path, monkeypatch, damage):
    coordinator, plan = prepare(tmp_path/'future', monkeypatch, window_bars=1)
    def forbidden(*args, **kwargs):
        pytest.fail('rejected collection cannot capture history')
    monkeypatch.setattr(coordinator, 'capture', forbidden)
    if damage == 'source':
        monkeypatch.setattr(coordinator, 'source_identity', lambda: dict(SOURCE, commit='0'*40))
    else:
        now = START if damage == 'missed' else plan['forward_design']['declared_at_ms']-1
        monkeypatch.setattr(coordinator.time, 'time_ns', lambda: now*1_000_000)
    with pytest.raises(ValueError):
        asyncio.run(coordinator.collect(tmp_path/'future'))
    assert set(p.name for p in (tmp_path/'future').iterdir()) == {'plan.json'}


@pytest.mark.parametrize('full_risk_policy', [False, True])
def test_whole_native_source_reaches_original_queue_review_and_cold_recovery(
        registered_case, tmp_path, monkeypatch, full_risk_policy):
    import shutil
    from itertools import chain
    from tools import gold_forward_collection as coordinator, gold_chunked_source as source
    from tools import registered_research_job as workspace
    from tools.gold_chunked_source import NativeSource
    from tools.perpetual_replay_research import source_identity
    from tests.tools.test_gold_stream_input import long_parts

    f = registered_case
    identity = source_identity()
    f['arguments']['release_identity'] = identity
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(f['root']))
    monkeypatch.setattr(workspace, 'workspace_paths', lambda: (f['root']/'project', f['root']/'registry.db'))
    for path in f['root'].glob('registry.db*'):
        path.chmod(0o600)
    parts = iter(long_parts(tmp_path, monkeypatch, count=2))
    first = next(parts)
    first['release_identity'] = identity
    inputs = tmp_path/'retained-native-originals'
    source.freeze(chain((first,), parts), inputs, maximum_bytes=32_000_000, settlement_window_ms=1000)
    with NativeSource(inputs) as originals:
        document = originals.document
    closes = sorted({row[0] for row in document['forward_input']['closed_observations']})
    assert len(closes) == 1
    clock = [closes[0]-INTERVAL-180_000]
    monkeypatch.setattr(coordinator.time, 'time_ns', lambda: clock[0]*1_000_000)
    configuration = {k: document[k] for k in ('quantity', 'initial_collateral', 'fee_rate', 'settlement_window_ms')}
    directory = tmp_path/'prospective-coordinator'
    policy_options={}
    if full_risk_policy:
        from tests.validation.test_gold_research_risk_policy import policy
        policy_options['research_risk_policy']=policy(start=closes[0],end=closes[0]+10000,
            declared=clock[0])
    coordinator.prepare(directory, **dict(options(), configuration=configuration,
        first_close_ms=closes[0], window_bars=1, **policy_options))
    # The collector transport is an explicit fixture; native originals and all
    # registration, execution, report and unsigned-review consumers are real.
    monkeypatch.setattr(coordinator, '_paired', lambda root, plan: 2)
    def compile_originals(quote_root, bar_root, output, **kwargs):
        assert kwargs['release_identity'] == identity
        shutil.copytree(inputs, output)
    monkeypatch.setattr(source, 'from_session', compile_originals)
    execute = f['run'].gold._execute
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return execute(*args, **kwargs)
    monkeypatch.setattr(f['run'].gold, '_execute', counted)
    clock[0] = closes[0]+100_000
    result = coordinator.finish(directory)
    assert len(calls) == 1
    assert result['authentication'] == 'UNSIGNED_REQUEST'
    if full_risk_policy:
        from tools import registered_research_review_job as reviews
        from quantos.contracts import gold_research_risk_policy as risk_shape
        packet=reviews.read_request(result['review_request_id'])
        facts=packet['projection']
        assert facts['schema']=='QUANTOS_PROJECT_RESEARCH_FACTS_V8'
        risk_inputs=facts['execution_risk_review']['account_risk_inputs']
        assert risk_inputs['risk_policy']['policy']==policy_options['research_risk_policy']
        assert risk_inputs['source_groups']['risk_config']['value_sha256']==risk_shape.policy_sha256(
            policy_options['research_risk_policy'])
        # Original two-part fixture does not cover the declared final sample.
        # Available policy must not erase unavailable account inputs.
        assert 'risk_config' not in risk_inputs['missing_groups']
        assert {'weights','alpha_returns','regime_labels','alpha_metadata','capacity_and_aum'} <= set(
            risk_inputs['missing_groups'])
        assert risk_inputs['source_groups']['portfolio_returns']['status']=='UNAVAILABLE_SAMPLE_COVERAGE'
        assert risk_inputs['full_research_risk_ready'] is False
    assert result['statistical_decision'] != 'PASS'
    assert result['economic_authority'] is result['submission_enabled'] is False
    project = f['root']/'project/runs'/result['native_invocation_id']
    original = {p.relative_to(project).as_posix(): p.read_bytes()
                for p in project.rglob('*') if p.is_file()}
    clock[0] += 1000
    recovered = coordinator.finish(directory, recover=True)
    assert recovered['native_invocation_id'] == result['native_invocation_id']
    assert recovered['review_request_id'] == result['review_request_id']
    assert len(calls) == 1
    assert {p.relative_to(project).as_posix(): p.read_bytes()
            for p in project.rglob('*') if p.is_file()} == original


def test_original_paired_collectors_and_cold_reader_keep_declared_window_binding(tmp_path, monkeypatch):
    import shutil
    import hashlib
    from tools import gold_forward_collection as coordinator, gold_bar_session as bars
    from tools import gold_quote_session as quotes
    from tools.gold_bar_capture import _write
    from tests.tools.test_gold_bar_session import fixture_history
    from tests.tools import test_gold_public_segment_producer as transport_fixture
    from tests.tools.test_gold_pooled_collection import pooled_fixture

    template = tmp_path/'template'
    template.mkdir()
    history, unshifted_message = fixture_history(template)
    receipt = json.loads((history/'receipt.json').read_bytes())
    shift = START-INTERVAL-receipt['requested_at_ms']
    rows = json.loads((history/'response.json').read_bytes())
    for row in rows:
        row[0] += shift
        row[6] += shift
    history_raw = json.dumps(rows).encode()
    (history/'response.json').write_bytes(history_raw)
    receipt = dict(receipt, requested_at_ms=receipt['requested_at_ms']+shift,
        received_at_ms=receipt['received_at_ms']+shift,
        response_sha256=hashlib.sha256(history_raw).hexdigest())
    def message(index):
        raw, received = unshifted_message(index)
        wire = json.loads(raw)
        wire['data']['E'] += shift
        wire['data']['k']['t'] += shift
        wire['data']['k']['T'] += shift
        return json.dumps(wire), received+shift
    # Preserve the history capture's original serializer and permissions.
    (history/'receipt.json').unlink()
    _write(history/'receipt.json', receipt)
    opening = receipt['requested_at_ms']
    clock = [opening-180_000]
    monkeypatch.setattr(coordinator.time, 'time_ns', lambda: clock[0]*1_000_000)
    for module in (coordinator, coordinator.public, bars, quotes):
        monkeypatch.setattr(module, 'source_identity', lambda: copy.deepcopy(SOURCE))
    directory = tmp_path/'prospective-pair'
    plan = coordinator.prepare(directory, **dict(options(), first_close_ms=opening+INTERVAL,
                                               window_bars=1))
    clock[0] = opening+10
    def copied_history(target):
        shutil.copytree(history, target)
    monkeypatch.setattr(coordinator, 'capture', copied_history)
    monkeypatch.setattr(transport_fixture, 'BASE', opening+10)
    quote_clock, _, _, _ = pooled_fixture(monkeypatch)
    monkeypatch.setattr(quotes, 'source_identity', lambda: copy.deepcopy(SOURCE))
    original_capture = quotes.capture_gold_session
    def advancing_capture(**kwargs):
        cancel = kwargs['cancel_event']
        class TransportWait:
            def is_set(self):
                return cancel.is_set()
            def wait(self, seconds):
                quote_clock[0] += seconds
                return cancel.is_set()
        return original_capture(**dict(kwargs, cancel_event=TransportWait()))
    monkeypatch.setattr(quotes, 'capture_gold_session', advancing_capture)

    class SyntheticDuration:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            raise TimeoutError
        def expired(self):
            return True

    async def stream(seconds, transport, **kwargs):
        transport['connected'] = True
        yield message(0)
        wire = json.loads(message(0)[0])
        received = opening+seconds*1000+20
        wire['data']['k']['x'] = False
        wire['data']['E'] = received-1
        yield json.dumps(wire), received

    # Only public transports, duration and clocks are synthetic. Both original
    # collectors persist their own journals; both original cold readers run.
    monkeypatch.setattr(bars, '_messages', stream)
    monkeypatch.setattr(bars.asyncio, 'timeout', lambda seconds: SyntheticDuration())
    result = asyncio.run(coordinator.collect(directory))
    assert result['paired_status'] == 'RAW_PAIRED_SESSION_COLLECTED'
    count = coordinator._paired(directory, plan)
    assert count > 0
    chunk = directory/'pair/bars/messages-0000.jsonl'
    chunk.write_bytes(chunk.read_bytes()+b'\n')
    with pytest.raises(ValueError, match='HASH'):
        coordinator._paired(directory, plan)


@pytest.mark.parametrize('status', ['RAW_PAIRED_SESSION_COLLECTED', 'INCOMPLETE'])
def test_run_only_finishes_original_complete_collection(tmp_path, monkeypatch, status):
    from tools import gold_forward_collection as coordinator
    events = []
    terminal = dict(paired_status=status, economic_authority=False, submission_enabled=False)
    async def collected(directory):
        events.append('collected')
        return terminal
    def finished(directory):
        assert events == ['collected']
        events.append('finished')
        return dict(authentication='UNSIGNED_REQUEST', economic_authority=False,
                    submission_enabled=False)
    monkeypatch.setattr(coordinator, 'collect', collected)
    monkeypatch.setattr(coordinator, 'finish', finished)
    if status == 'INCOMPLETE':
        with pytest.raises(ValueError, match='COLLECTION_INCOMPLETE'):
            coordinator.main(['run', '--output', str(tmp_path/'future')])
        assert events == ['collected']
    else:
        assert coordinator.main(['run', '--output', str(tmp_path/'future')]) == 0
        assert events == ['collected', 'finished']
