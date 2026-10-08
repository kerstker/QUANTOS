"""Original producer bytes, not self-described authority-shaped dictionaries."""
import copy
import dataclasses
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from quantos.contracts.backtest_identity import encode_identity_payload


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value if type(value) is bytes else encode(value)).hexdigest()


def _gold(tmp_path, monkeypatch, *, source_identity=None):
    from tests.tools.test_gold_bar_cost_replay import inputs
    from tools import gold_bar_experiment as experiment
    from tools.gold_bar_review import review_request
    from tools import gold_bar_definition as definition
    from services.research.trial_ledger import TrialLedger
    args = inputs()
    if source_identity is not None:
        args['release_identity'] = source_identity
    monkeypatch.setattr(experiment, 'source_identity', lambda: args['release_identity'])
    root = tmp_path / 'gold'
    experiment.archive(args, root)
    request = review_request(root)
    raw = {name: (root / f'{name}.json').read_bytes() for name in ('manifest', 'input')}
    raw['record'] = (root / 'report.json').read_bytes()
    report = json.loads(raw['record'])
    simulation = report['trials'][0]['simulation']
    with TrialLedger(str(root / 'trials.db'), create=False, read_only=True) as ledger:
        ledger.verify()
        history = {tid: [dataclasses.asdict(row) for row in ledger.load(tid).events]
                   for tid in request['trial_ids']}
    # Host/source acceptance is isolated; the actual retained definition builder,
    # original archive/journal verification and all result hashing remain native.
    if source_identity is None:
        monkeypatch.setattr(definition, 'verify_source', lambda directory, expected: {
            name: digest((Path(directory) / name).read_bytes()) for name in definition.SEMANTICS})
    candidate = definition.define(root, candidate='channel', source_directory=definition.ROOT)
    return dict(raw=raw, source=args['release_identity'], dataset=report['dataset_identity'],
                candidate=candidate, candidate_id='channel', history=history,
                run_id=request['trial_ids'][0], start=simulation['equity_history'][0]['timestamp'],
                end=simulation['equity_history'][-1]['timestamp'], kind='FORWARD',
                adapter='GOLD_BAR_FORWARD_V1', ledger_path=root/'trials.db', archive_root=root)


def _technical(tmp_path, monkeypatch, *, quantity_scale=None):
    from tests.execution import test_authoritative_perpetual_backtest as fixture
    from services.research.trial_ledger import TrialLedger, TrialRecord
    from services.execution import authoritative_perpetual_backtest as producer
    native = fixture.run_authoritative_linear_perpetual_v2
    captured = {}
    manifest_inputs = {}
    original_builder = producer.build_run_manifest_v2

    def build_manifest(**kwargs):
        manifest_inputs.update({k: copy.deepcopy(kwargs[k]) for k in
                                ('release_identity', 'data_identity', 'universe')})
        return original_builder(**kwargs)

    monkeypatch.setattr(producer, 'build_run_manifest_v2', build_manifest)

    def gold(value):
        if dataclasses.is_dataclass(value):
            return dataclasses.replace(value, **{f.name: gold(getattr(value, f.name))
                                                 for f in dataclasses.fields(value)})
        if type(value) is dict:
            return {gold(k): gold(v) for k, v in value.items()}
        if type(value) in (tuple, list):
            return type(value)(gold(v) for v in value)
        return value.replace('BTCUSDT', 'XAUUSDT') if type(value) is str else value

    def run(**kwargs):
        actual = gold(kwargs)
        if quantity_scale is not None:
            actual['alpha_definition']['position_mapping']['quantity_scale'] = quantity_scale
        captured.update(actual)
        return native(**actual)

    monkeypatch.setattr(fixture, 'run_authoritative_linear_perpetual_v2', run)
    record = fixture._run()
    candidate = json.loads(json.dumps(captured['alpha_definition'], default=str))
    tid = digest({'record': record['run_id'], 'candidate': candidate})
    with TrialLedger(str(tmp_path / 'technical-trials.db')) as ledger:
        for index, status in enumerate(('PLANNED', 'RUNNING', 'PASSED')):
            ledger.append(TrialRecord(tid, digest(candidate), digest('family'), digest(candidate),
                digest('technical-policy'), status, index, fixture.DECISION - 10000 + index))
        ledger.verify()
        history = {tid: [dataclasses.asdict(row) for row in ledger.load(tid).events]}
    source = captured['release_identity']
    dataset = captured['dataset_identity']
    return dict(raw={'record': encode(record), 'manifest': encode(record['detail']['run_manifest_v2']),
                     'input': encode({'schema': 'QUANTOS_TECHNICAL_PERPETUAL_INPUT_V1',
                                      'values': {k: encode_identity_payload(v) for k,v in manifest_inputs.items()}})},
                source=source, dataset=dataset, candidate=candidate,
                candidate_id=candidate['alpha_id'], history=history, run_id=record['run_id'],
                start=fixture.DECISION - 1000, end=fixture.DECISION + 3000,
                kind='BACKTEST', adapter='TECHNICAL_LINEAR_PERPETUAL_V2', ledger_path=tmp_path/'technical-trials.db')


@pytest.fixture(params=['technical', 'gold'])
def native_materials(request, tmp_path, monkeypatch):
    v = (_technical if request.param == 'technical' else _gold)(tmp_path, monkeypatch)
    originals = dict(v['raw'], source_identity=encode(v['source']),
        source_manifest=encode({'schema': 'QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',
            'source_identity_sha256': digest(encode(v['source'])),
            'files': {'producer.py': digest('isolated synthetic release identity')}}),
        candidate_definition=encode(v['candidate']), dataset_manifest=encode(v['dataset']),
        trial_history=encode(v['history']), risk_replay=None,
        evaluation_plan=encode({'schema': 'QUANTOS_RESEARCH_EVALUATION_PLAN_V1',
            'candidate_id': v['candidate_id'], 'evidence_kind': v['kind'],
            'registered_at_ms': v['start'] - 1, 'start_ms': v['start'], 'end_ms': v['end'],
            'frequency_ms': 1000, 'trial_ids': list(v['history'])}))
    subject = dict(schema='QUANTOS_RESEARCH_EVIDENCE_SUBJECT_V1',
        purpose='CANDIDATE_RESEARCH_REVIEW', evidence_kind=v['kind'], native_adapter=v['adapter'],
        producer_id='isolated-research', candidate_id=v['candidate_id'], market='XAUUSDT',
        candidate_definition_sha256=digest(originals['candidate_definition']),
        source_identity_sha256=digest(originals['source_identity']), native_run_id=v['run_id'],
        original_record_sha256=digest(originals['record']),
        original_manifest_sha256=digest(originals['manifest']),
        dataset_manifest_sha256=digest(originals['dataset_manifest']),
        trial_history_sha256=digest(originals['trial_history']),
        evaluation_plan_sha256=digest(originals['evaluation_plan']))
    return dict(subject=subject, originals=originals, now_ms=v['end'] + 1000, ledger_path=v['ledger_path'])


def test_projects_actual_producer_without_relabel_or_fabricated_metrics(native_materials):
    from services.validation.research_result_projection import project_result
    f = native_materials
    before = copy.deepcopy(f)
    value = project_result(subject=f['subject'], originals=f['originals'])
    assert f == before
    assert value['original_record_sha256'] == digest(f['originals']['record'])
    assert value['metrics']['reconciliation_residual'] == '0'
    assert value['metrics']['annualized_sharpe'] is None
    assert value['original_risk_claims']['deployment_equivalent_risk'] is not True
    assert 'RISK_REPLAY_ADAPTER_UNAVAILABLE' in value['limitations']
    if f['subject']['evidence_kind'] == 'FORWARD':
        assert value['metrics']['sample_count'] > 0
    else:
        assert value['metrics']['sample_count'] == 0
        assert value['metrics']['statistics_status'] == 'INSUFFICIENT_HISTORY'


@pytest.mark.parametrize('change', ['record', 'candidate', 'history', 'kind', 'plan-bool', 'missing'])
def test_original_changes_and_wrong_kind_are_rejected(native_materials, change):
    from services.validation.research_result_projection import project_result
    f = copy.deepcopy(native_materials)
    if change == 'record':
        f['originals']['record'] += b' '
    elif change == 'candidate':
        f['subject']['candidate_id'] = 'another-candidate'
    elif change == 'history':
        f['originals']['trial_history'] = b'{}'
        f['subject']['trial_history_sha256'] = digest(b'{}')
    elif change == 'kind':
        f['subject']['evidence_kind'] = 'FORWARD' if f['subject']['evidence_kind'] == 'BACKTEST' else 'BACKTEST'
    elif change == 'plan-bool':
        plan = json.loads(f['originals']['evaluation_plan'])
        plan['frequency_ms'] = True
        f['originals']['evaluation_plan'] = encode(plan)
        f['subject']['evaluation_plan_sha256'] = digest(f['originals']['evaluation_plan'])
    else:
        f['originals'].pop('input')
    with pytest.raises(ValueError):
        project_result(subject=f['subject'], originals=f['originals'])


def test_projection_cannot_accept_outer_summary_or_bool_alias(native_materials):
    from services.validation.research_result_projection import project_result, validate_projection
    f = native_materials
    value = project_result(subject=f['subject'], originals=f['originals'])
    value['metrics']['annualized_sharpe'] = '100'
    with pytest.raises(ValueError):
        validate_projection(value, subject=f['subject'], originals=f['originals'])
    value = project_result(subject=f['subject'], originals=f['originals'])
    value['metrics']['sample_count'] = bool(value['metrics']['sample_count'])
    with pytest.raises(ValueError):
        validate_projection(value, subject=f['subject'], originals=f['originals'])


def test_v1_and_duplicate_keys_never_become_native_result(native_materials):
    from services.validation.research_result_projection import project_result
    f = copy.deepcopy(native_materials)
    for raw in (b'{"authoritative":true,"detail":{"evidence_status":"AUTHORITATIVE"}}',
                b'{"schema":1,"schema":2}'):
        f['originals']['record'] = raw
        f['subject']['original_record_sha256'] = digest(raw)
        with pytest.raises(ValueError):
            project_result(subject=f['subject'], originals=f['originals'])


def test_original_complete_trial_history_includes_failed_and_planned(native_materials):
    from services.research.trial_ledger import TrialLedger, TrialRecord
    from services.validation.research_result_projection import project_result
    f = copy.deepcopy(native_materials)
    with TrialLedger(str(f['ledger_path']), create=False) as ledger:
        for label, statuses in (('failed-extra', ('PLANNED', 'RUNNING', 'FAILED')), ('pending-extra', ('PLANNED',))):
            tid = digest(label)
            for index, status in enumerate(statuses):
                ledger.append(TrialRecord(tid, digest(label), digest('family'), digest(label),
                    digest('policy'), status, index, f['now_ms'] + index))
        ledger.verify()
        ids = list(json.loads(f['originals']['trial_history'])) + [digest('failed-extra'), digest('pending-extra')]
        history = {tid: [dataclasses.asdict(e) for e in ledger.load(tid).events] for tid in ids}
    f['originals']['trial_history'] = encode(history)
    f['subject']['trial_history_sha256'] = digest(f['originals']['trial_history'])
    plan = json.loads(f['originals']['evaluation_plan'])
    plan['trial_ids'] = ids
    f['originals']['evaluation_plan'] = encode(plan)
    f['subject']['evaluation_plan_sha256'] = digest(f['originals']['evaluation_plan'])
    result = project_result(subject=f['subject'], originals=f['originals'])
    assert result['trial_binding']['trial_count'] == len(ids)
    plan['trial_ids'].pop()
    f['originals']['evaluation_plan'] = encode(plan)
    f['subject']['evaluation_plan_sha256'] = digest(f['originals']['evaluation_plan'])
    with pytest.raises(ValueError, match='TRIAL_SET'):
        project_result(subject=f['subject'], originals=f['originals'])


@pytest.mark.parametrize('native_materials', ['gold'], indirect=True)
@pytest.mark.parametrize('native_position', [0, 1, 2])
def test_each_native_gold_trial_retains_its_bound_policy(native_materials, native_position):
    from quantos.contracts.trial_identity import trial_event_hash
    from services.validation.research_result_projection import project_result
    f = copy.deepcopy(native_materials)
    history = json.loads(f['originals']['trial_history'])
    ids = json.loads(f['originals']['manifest'])['trial_ids']
    changed_id = ids[native_position]
    global_previous = ''
    previous_by_trial = {}
    for event in sorted((e for rows in history.values() for e in rows), key=lambda e: e['global_seq']):
        if event['trial_id'] == changed_id:
            event['validation_policy_digest'] = digest('different native policy')
        event['prev_hash'] = previous_by_trial.get(event['trial_id'], '')
        record = {k: event[k] for k in ('trial_id', 'alpha_id', 'family_id',
            'exact_definition_id', 'validation_policy_digest', 'status', 'event_index', 'at')}
        event['event_hash'] = trial_event_hash(record, global_seq=event['global_seq'],
            previous_hash=event['prev_hash'], global_previous_hash=global_previous)
        previous_by_trial[event['trial_id']] = event['event_hash']
        global_previous = event['event_hash']
    f['originals']['trial_history'] = encode(history)
    f['subject']['trial_history_sha256'] = digest(f['originals']['trial_history'])
    # Even a valid complete hash chain cannot substitute another policy for any
    # native candidate, including siblings of the candidate under review.
    with pytest.raises(ValueError, match='GOLD_NATIVE_POLICY'):
        project_result(subject=f['subject'], originals=f['originals'])


@pytest.mark.parametrize('change', ['path', 'future-plan', 'event-bool', 'frequency-budget'])
def test_strict_source_plan_and_journal_types(native_materials, change):
    from services.validation.research_result_projection import project_result
    f = copy.deepcopy(native_materials)
    role = 'evaluation_plan'
    value = json.loads(f['originals'][role])
    if change == 'path':
        role = 'source_manifest'
        value = json.loads(f['originals'][role])
        value['files'] = {'../services/x.py': 'a' * 64}
    elif change == 'future-plan':
        value['registered_at_ms'] = value['end_ms'] + 1
    elif change == 'event-bool':
        role = 'trial_history'
        value = json.loads(f['originals'][role])
        next(iter(value.values()))[0]['event_index'] = False
    else:
        value['frequency_ms'] = 86400001
    f['originals'][role] = encode(value)
    if role + '_sha256' in f['subject']:
        f['subject'][role + '_sha256'] = digest(f['originals'][role])
    with pytest.raises(ValueError):
        project_result(subject=f['subject'], originals=f['originals'])


@pytest.mark.parametrize('budget', ['leaf', 'total'])
def test_new_original_resource_limits_do_not_accept_oversized_materials(native_materials, budget):
    from services.validation.research_result_projection import project_result
    f = copy.deepcopy(native_materials)
    if budget == 'leaf':
        f['originals']['risk_replay'] = b' ' * 16000001
        reason = 'ORIGINAL_BYTES'
    else:
        payload = b' ' * 16000000
        for role in ('risk_replay', 'input', 'source_manifest', 'trial_history'):
            f['originals'][role] = payload
        reason = 'TOTAL_BYTES'
    with pytest.raises(ValueError, match=reason):
        project_result(subject=f['subject'], originals=f['originals'])


def test_native_decimal_transport_cannot_become_same_text(tmp_path, monkeypatch):
    from services.validation.research_result_projection import project_result
    # Obtain the full fixture through its original native producer; this case is
    # technical-only because Gold already stores canonical decimal strings.
    class Parameter:
        param = 'technical'
    f = native_materials.__wrapped__(Parameter(), tmp_path, monkeypatch)
    transport = json.loads(f['originals']['input'])

    def replace_decimal(node):
        if type(node) is list and node and node[0] == 'decimal':
            node[0] = 'str'
            return True
        return type(node) is list and any(replace_decimal(child) for child in node)

    assert replace_decimal(transport['values']['data_identity'])
    f['originals']['input'] = encode(transport)
    with pytest.raises(ValueError, match='TECHNICAL_SOURCE'):
        project_result(subject=f['subject'], originals=f['originals'])


def test_projection_does_not_change_original_authority_gate(native_materials):
    from services.execution.backtest_authority import is_authoritative_backtest_record
    from services.validation.research_result_projection import project_result
    project_result(subject=native_materials['subject'], originals=native_materials['originals'])
    assert is_authoritative_backtest_record(json.loads(native_materials['originals']['record'])) is False


@pytest.mark.parametrize('field', ['at', 'status'])
def test_native_trial_chain_content_cannot_keep_old_event_hash(native_materials, field):
    from services.validation.research_result_projection import project_result
    f = copy.deepcopy(native_materials)
    history = json.loads(f['originals']['trial_history'])
    event = next(iter(history.values()))[-1]
    event[field] = event['at'] + 1 if field == 'at' else 'FAILED'
    f['originals']['trial_history'] = encode(history)
    f['subject']['trial_history_sha256'] = digest(f['originals']['trial_history'])
    with pytest.raises(ValueError, match='TRIAL_EVENT_HASH'):
        project_result(subject=f['subject'], originals=f['originals'])


def test_original_alpha_decimal_representation_is_preserved(tmp_path, monkeypatch):
    from services.validation.research_result_projection import project_result
    original = _technical
    monkeypatch.setattr(__import__(__name__, fromlist=['_technical']), '_technical',
        lambda tmp, patch: original(tmp, patch, quantity_scale=Decimal('0.40')))
    class Parameter:
        param = 'technical'
    f = native_materials.__wrapped__(Parameter(), tmp_path, monkeypatch)
    assert json.loads(f['originals']['candidate_definition'])['position_mapping']['quantity_scale'] == '0.40'
    result = project_result(subject=f['subject'], originals=f['originals'])
    assert result['original_record_sha256'] == digest(f['originals']['record'])


def test_plan_grid_includes_initial_point_in_100k_bound(native_materials):
    from services.validation.research_result_projection import project_result
    f = copy.deepcopy(native_materials)
    plan = json.loads(f['originals']['evaluation_plan'])
    plan['end_ms'] = plan['start_ms'] + 100000 * plan['frequency_ms']
    f['originals']['evaluation_plan'] = encode(plan)
    f['subject']['evaluation_plan_sha256'] = digest(f['originals']['evaluation_plan'])
    with pytest.raises(ValueError, match='PLAN_WINDOW'):
        project_result(subject=f['subject'], originals=f['originals'])
