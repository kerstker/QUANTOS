"""Bounded projections of immutable technical research, without admission or IO.

Only the two named native formats are consumed. Signed wrappers do not change
their original claims. Filesystem custody and complete journal/source capture
remain responsibilities of the fixed consumer reader.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from itertools import zip_longest
from decimal import Decimal
from pathlib import PurePosixPath

from quantos.contracts.backtest_identity import RunManifestV2, exact_identity_hash, decode_identity_payload
from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from quantos.contracts.research_statistics import fixed_frequency_equity
from quantos.contracts.trial_identity import trial_event_hash

MAX_LEAF = 16_000_000
MAX_TOTAL = 64_000_000
MAX_ITEMS = 100_000
ROLES = frozenset('source_manifest source_identity candidate_definition manifest input record '
                  'dataset_manifest trial_history evaluation_plan risk_replay'.split())
SUBJECT_FIELDS = frozenset('schema purpose evidence_kind native_adapter producer_id candidate_id market '
    'candidate_definition_sha256 source_identity_sha256 native_run_id original_record_sha256 '
    'original_manifest_sha256 dataset_manifest_sha256 trial_history_sha256 evaluation_plan_sha256'.split())
ADAPTERS = {'TECHNICAL_LINEAR_PERPETUAL_V2': 'BACKTEST', 'GOLD_BAR_FORWARD_V1': 'FORWARD'}


def _require(ok, reason):
    if not ok:
        raise ValueError('RESEARCH_EVIDENCE_' + reason)


def _fields(value, fields, reason):
    _require(type(value) is dict and set(value) == set(fields), reason)


def _integer(value, *, minimum=0, maximum=2**63-1):
    _require(type(value) is int and minimum <= value <= maximum, 'INTEGER')
    return value


def _text(value):
    _require(type(value) is str and 0 < len(value) <= 255 and value == value.strip(), 'TEXT')
    return value


def _sha(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None, 'SHA256')
    return value


def _tree(value, *, floats=False, depth=0):
    _require(depth <= 64, 'DEPTH')
    if type(value) is dict:
        _require(len(value) <= MAX_ITEMS and all(type(k) is str for k in value), 'JSON_KEYS')
        for v in value.values():
            _tree(v, floats=floats, depth=depth+1)
    elif type(value) is list:
        _require(len(value) <= MAX_ITEMS, 'ITEMS')
        for v in value:
            _tree(v, floats=floats, depth=depth+1)
    elif type(value) is float:
        _require(floats and math.isfinite(value), 'FLOAT')
    else:
        _require(value is None or type(value) in (str, int, bool), 'JSON_TYPE')


def _canonical(value, *, maximum=MAX_LEAF, floats=False):
    _tree(value, floats=floats)
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False).encode()
    except (ValueError, UnicodeError, OverflowError) as exc:
        raise ValueError('RESEARCH_EVIDENCE_JSON') from exc
    _require(len(raw) <= maximum, 'BYTES')
    return raw


def _digest(value):
    return hashlib.sha256(value if type(value) is bytes else _canonical(value)).hexdigest()


def _decode(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_LEAF, 'ORIGINAL_BYTES')
    def pairs(items):
        out = {}
        for k, v in items:
            _require(k not in out, 'DUPLICATE_KEY')
            out[k] = v
        return out
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        _tree(value, floats=True)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError('RESEARCH_EVIDENCE_ORIGINAL_JSON') from exc


def _same(left, right):
    return _canonical(left, floats=True) == _canonical(right, floats=True)


def _number(value):
    _require(type(value) is str and 0 < len(value) <= 256, 'DECIMAL')
    try:
        number = Decimal(value)
        _require(number.is_finite() and (not number or -96 <= number.adjusted() <= 96), 'DECIMAL')
        _require(canonical_decimal(number) == value, 'CANONICAL_DECIMAL')
        return number
    except ArithmeticError as exc:
        raise ValueError('RESEARCH_EVIDENCE_DECIMAL') from exc


def _subject(value):
    _fields(value, SUBJECT_FIELDS, 'SUBJECT_FIELDS')
    _canonical(value, maximum=16384)
    _require(value['schema'] == 'QUANTOS_RESEARCH_EVIDENCE_SUBJECT_V1'
        and value['purpose'] == 'CANDIDATE_RESEARCH_REVIEW'
        and ADAPTERS.get(value['native_adapter']) == value['evidence_kind']
        and value['market'] == 'XAUUSDT', 'SUBJECT_SCOPE')
    for k, v in value.items():
        _sha(v) if k.endswith('_sha256') else _text(v)


def _originals(subject, originals):
    _fields(originals, ROLES, 'ORIGINAL_ROLES')
    _require(sum(len(v) for v in originals.values() if type(v) is bytes) <= MAX_TOTAL, 'TOTAL_BYTES')
    decoded = {k: None if k == 'risk_replay' and v is None else _decode(v) for k, v in originals.items()}
    for role, key in (('candidate_definition', 'candidate_definition_sha256'),
            ('source_identity', 'source_identity_sha256'), ('record', 'original_record_sha256'),
            ('manifest', 'original_manifest_sha256'), ('dataset_manifest', 'dataset_manifest_sha256'),
            ('trial_history', 'trial_history_sha256'), ('evaluation_plan', 'evaluation_plan_sha256')):
        _require(_digest(originals[role]) == subject[key], 'ORIGINAL_BINDING')
    source = decoded['source_manifest']
    _fields(source, ('schema', 'source_identity_sha256', 'files'), 'SOURCE_MANIFEST')
    _require(source['schema'] == 'QUANTOS_RESEARCH_SOURCE_MANIFEST_V1'
        and source['source_identity_sha256'] == subject['source_identity_sha256']
        and type(source['files']) is dict and 0 < len(source['files']) <= MAX_ITEMS, 'SOURCE_MANIFEST')
    for name, value in source['files'].items():
        _require(type(name) is str and 0 < len(name) <= 1024 and '\\' not in name
            and '\x00' not in name and not name.startswith('/') and
            all(p not in ('', '.', '..') for p in name.split('/'))
            and str(PurePosixPath(name)) == name, 'SOURCE_PATH')
        _sha(value)
    return decoded


def _plan(subject, value):
    _fields(value, 'schema candidate_id evidence_kind registered_at_ms start_ms end_ms frequency_ms trial_ids'.split(), 'PLAN')
    _require(value['schema'] == 'QUANTOS_RESEARCH_EVALUATION_PLAN_V1'
        and value['candidate_id'] == subject['candidate_id']
        and value['evidence_kind'] == subject['evidence_kind'], 'PLAN_BINDING')
    for key in ('registered_at_ms', 'start_ms', 'end_ms'):
        _integer(value[key])
    _integer(value['frequency_ms'], minimum=1, maximum=86_400_000)
    _require(value['registered_at_ms'] <= value['start_ms'] <= value['end_ms']
        and (value['end_ms']-value['start_ms'])//value['frequency_ms']+1 <= MAX_ITEMS, 'PLAN_WINDOW')
    ids = value['trial_ids']
    _require(type(ids) is list and 0 < len(ids) <= MAX_ITEMS and len(set(ids)) == len(ids), 'PLAN_TRIALS')
    for tid in ids:
        _sha(tid)


def _trials(value, plan):
    _require(type(value) is dict and set(value) == set(plan['trial_ids']), 'TRIAL_SET')
    transitions = {'PLANNED': {'RUNNING', 'CANCELLED'}, 'RUNNING': {'PASSED', 'FAILED', 'CANCELLED'}}
    fields = 'trial_id alpha_id family_id exact_definition_id validation_policy_digest status event_index at global_seq prev_hash event_hash'.split()
    sequences = []
    for tid, events in value.items():
        _require(type(events) is list and 1 <= len(events) <= MAX_ITEMS, 'TRIAL_EVENTS')
        previous = None
        for index, event in enumerate(events):
            _fields(event, fields, 'TRIAL_EVENT')
            for key in ('trial_id', 'alpha_id', 'family_id', 'exact_definition_id', 'validation_policy_digest', 'event_hash'):
                _sha(event[key])
            _integer(event['event_index']); _integer(event['at']); _integer(event['global_seq'], minimum=1)
            sequences.append(event['global_seq'])
            _require(event['trial_id'] == tid and event['event_index'] == index, 'TRIAL_IDENTITY')
            if previous is None:
                _require(event['status'] == 'PLANNED' and event['prev_hash'] == '', 'TRIAL_START')
            else:
                _require(event['status'] in transitions.get(previous['status'], set())
                    and event['prev_hash'] == previous['event_hash'] and event['at'] >= previous['at']
                    and all(event[k] == previous[k] for k in
                        ('alpha_id', 'family_id', 'exact_definition_id', 'validation_policy_digest')), 'TRIAL_TRANSITION')
            previous = event
    _require(len(sequences) <= MAX_ITEMS and sorted(sequences) == list(range(1, len(sequences)+1)), 'TRIAL_GLOBAL_SET')
    global_previous = ''
    for event in sorted((row for events in value.values() for row in events), key=lambda row: row['global_seq']):
        record = {key: event[key] for key in ('trial_id', 'alpha_id', 'family_id', 'exact_definition_id',
                                             'validation_policy_digest', 'status', 'event_index', 'at')}
        expected = trial_event_hash(record, global_seq=event['global_seq'], previous_hash=event['prev_hash'],
                                    global_previous_hash=global_previous)
        _require(event['event_hash'] == expected, 'TRIAL_EVENT_HASH')
        global_previous = event['event_hash']


def _technical(subject, docs):
    record, manifest = docs['record'], RunManifestV2.from_dict(docs['manifest'])
    _fields(record, ('run_id', 'authoritative', 'detail'), 'TECHNICAL_RECORD')
    detail = record['detail']
    _require(type(detail) is dict and record['authoritative'] is False
        and record['run_id'] == subject['native_run_id'] == manifest.run_id
        and detail.get('evidence_status') == 'TECHNICAL_VALIDATION_ONLY'
        and detail.get('engine_semantics') == 'LINEAR_PERPETUAL_EVENT_V2'
        and detail.get('manifest_hash') == manifest.manifest_hash
        and _same(detail.get('run_manifest_v2'), docs['manifest']), 'TECHNICAL_BINDING')
    result = detail.get('result_hash_payload')
    _fields(result, 'schema_version run_id engine_policy reconciliation ending_state ledger_events liquidations '
        'decision_contexts research_risk_results execution_risk_contexts execution_results'.split(), 'TECHNICAL_PAYLOAD')
    _require(result['schema_version'] == 'TECHNICAL_PERPETUAL_VALIDATION_V2'
        and result['run_id'] == manifest.run_id
        and detail.get('result_hash') == exact_identity_hash(result, name='authoritative_backtest_result'), 'RESULT_HASH')
    transport = docs['input']
    _fields(transport, ('schema', 'values'), 'TECHNICAL_INPUT')
    _require(transport['schema'] == 'QUANTOS_TECHNICAL_PERPETUAL_INPUT_V1', 'TECHNICAL_INPUT_SCHEMA')
    _fields(transport['values'], ('release_identity', 'data_identity', 'universe'), 'TECHNICAL_INPUT_VALUES')
    inputs = {k: decode_identity_payload(v) for k,v in transport['values'].items()}
    _require(exact_identity_hash(inputs['release_identity']) == exact_identity_hash(docs['source_identity'])
        and exact_identity_hash(inputs['data_identity']['dataset_identity']) == exact_identity_hash(docs['dataset_manifest'])
        and manifest.release_hash == exact_identity_hash(docs['source_identity'])
        and manifest.data_hash == exact_identity_hash(inputs['data_identity'])
        and exact_identity_hash(inputs['data_identity']['universe_snapshot']) == exact_identity_hash(inputs['universe']), 'TECHNICAL_SOURCE')
    definition = dict(docs['candidate_definition'])
    mapping = dict(definition.get('position_mapping', {}))
    _require(definition.get('alpha_id') == subject['candidate_id']
        and mapping.get('schema') == 'DIRECT_LINEAR_TARGET_QUANTITY_V1', 'TECHNICAL_CANDIDATE')
    for key in ('quantity_scale', 'max_abs_quantity'):
        # Native identity distinguishes Decimal('0.40') from Decimal('0.4').
        # Preserve its original representation; monetary output remains canonical.
        mapping[key] = decode_identity_payload(['decimal', mapping.get(key)])
    definition['position_mapping'] = mapping
    _require(manifest.alpha_hash == exact_identity_hash(definition), 'TECHNICAL_ALPHA_HASH')
    _require(manifest.universe_hash == exact_identity_hash(inputs['universe'])
        and type(inputs['universe']) is dict
        and type(inputs['universe'].get('value')) in (list, tuple)
        and tuple(inputs['universe']['value']) == (subject['market'],), 'TECHNICAL_MARKET')
    claims = detail.get('engine_attestations')
    _require(type(claims) is dict and all(type(v) is bool for v in claims.values()), 'RISK_TYPES')
    selected = [tid for tid, rows in docs['trial_history'].items()
        if rows[-1]['alpha_id'] == _digest(docs['candidate_definition'])]
    _require(len(selected) == 1, 'CANDIDATE_TRIAL_AMBIGUOUS')
    return result, selected[0], detail['result_hash'], manifest.manifest_hash, claims


def _gold(subject, docs):
    report, manifest, inputs, definition = (docs[k] for k in ('record', 'manifest', 'input', 'candidate_definition'))
    _require(type(report) is dict and report.get('schema') == 'GOLD_BAR_PUBLIC_COMPARISON_V1'
        and all(report.get(k) is False for k in ('authoritative', 'economic_eligible', 'submission_enabled', 'historical_oos_evaluated'))
        and report.get('result_hash') == exact_identity_hash({k:v for k,v in report.items() if k != 'result_hash'}, name='gold_bar_public_comparison'), 'GOLD_RESULT')
    _require(type(manifest) is dict and manifest.get('schema') in
        ('GOLD_BAR_EXPERIMENT_V1', 'QUANTOS_REGISTERED_GOLD_BAR_EXPERIMENT_V2')
        and manifest.get('terminal') == 'PASSED'
        and manifest.get('report_sha256') == subject['original_record_sha256']
        and _same(manifest.get('source'), docs['source_identity'])
        and _same(report.get('release_identity'), docs['source_identity'])
        and _same(inputs.get('release_identity'), docs['source_identity'])
        and _same(report.get('dataset_identity'), docs['dataset_manifest']), 'GOLD_MANIFEST')
    from quantos.contracts.registered_research_registration import gold_archive_policy
    policy = gold_archive_policy(manifest, inputs)
    _require(type(definition) is dict and definition.get('schema') == 'GOLD_BAR_RESEARCH_DEFINITION_V1'
        and definition.get('definition_hash') == exact_identity_hash(
            {k:v for k,v in definition.items() if k != 'definition_hash'}, name='gold_bar_research_definition')
        and definition.get('candidate') == subject['candidate_id']
        and definition.get('symbol') == subject['market']
        and definition.get('result_hash') == report['result_hash']
        and _same(definition.get('source'), docs['source_identity']), 'GOLD_DEFINITION')
    trials = report.get('trials')
    _require(type(trials) is list and 0 < len(trials) <= 4, 'GOLD_TRIALS')
    ids = manifest.get('trial_ids')
    _require(type(ids) is list and len(ids) in (len(trials), len(trials)+1)
        and len(set(ids)) == len(ids) and set(ids) <= set(docs['trial_history']), 'GOLD_TRIAL_IDS')
    # The complete population also contains other searches and policies. Every
    # native archive trial still binds the original policy; no trial is omitted
    # from the population/hash-chain validation performed by _trials.
    _require(all(e['validation_policy_digest'] == policy
        for tid in ids for e in docs['trial_history'][tid]), 'GOLD_NATIVE_POLICY')
    selected = [i for i,t in enumerate(trials) if t.get('candidate') == subject['candidate_id']]
    _require(len(selected) == 1, 'GOLD_CANDIDATE')
    index = selected[0]
    _require(ids[index] == definition.get('trial_id') == subject['native_run_id'], 'GOLD_TRIAL_BINDING')
    simulation = trials[index]['simulation']
    from quantos.contracts.gold_native_source import simulation_budget
    _require(simulation_budget(report['dataset_identity'], simulation)
        and simulation.get('authoritative') is False
        and simulation.get('evidence_status') == 'TECHNICAL_VALIDATION_ONLY', 'GOLD_SIMULATION')
    claims = dict(full_research_risk_9_of_9=simulation.get('full_research_risk_9_of_9'),
        risk_history_ledger_replayed=(None if simulation.get('risk_history_source') is None else
                                     simulation['risk_history_source'] == 'LEDGER_EVENT_REPLAY'),
        deployment_equivalent_risk=None)
    # The archive has no native manifest-hash field; retain its actual byte hash.
    return simulation, ids[index], report['result_hash'], subject['original_manifest_sha256'], claims


@ledger_decimal_context()
def _native_metrics(simulation, plan, claims, evidence_kind, *, statistical_history=None,
                    allow_uncovered_sample=False, maximum_history_points=None, statistical_grid_budget=None):
    """Original native cash/statistics calculation, shared without changing policy."""
    identity = simulation['reconciliation']
    state = simulation['ending_state']
    if maximum_history_points is not None:
        _integer(maximum_history_points, minimum=1, maximum=100_000_000)
        from quantos.streaming_identity import json_parts
        identity = json.loads(b''.join(json_parts(identity, maximum_bytes=MAX_LEAF)))
        state_identity = json.loads(b''.join(json_parts(state['cashflow_identity'], maximum_bytes=MAX_LEAF)))
    else:
        state_identity = state['cashflow_identity']
    initial, ending, change, residual = (_number(identity[k]) for k in
        ('initial_equity', 'ending_equity', 'attributed_change', 'residual'))
    _require(residual == 0 and ending-initial == change
        and _same(state_identity, identity) and _number(state['account_equity']) == ending, 'CASH_IDENTITY')
    history = simulation.get('equity_history')
    limitations = {'RISK_REPLAY_ADAPTER_UNAVAILABLE', 'NO_PROMOTION_OR_EXECUTION_APPROVAL'}
    if history is None:
        count, sharpe, status, drawdown = 0, None, 'INSUFFICIENT_HISTORY', None
    else:
        _require((type(history) is list and 0 < len(history) <= MAX_ITEMS if maximum_history_points is None else
            isinstance(history, Sequence) and not isinstance(history, (str, bytes))
                and 0 < len(history) <= maximum_history_points)
            and _number(history[0]['equity']) == initial and _number(history[-1]['equity']) == ending, 'EQUITY_HISTORY')
        covered = history[0]['timestamp'] <= plan['start_ms'] <= plan['end_ms'] <= history[-1]['timestamp']
        _require(covered or allow_uncovered_sample is True, 'EQUITY_HISTORY')
        peak, drawdown_value = initial, Decimal(0)
        for row in history:
            equity = _number(row['equity'])
            peak = max(peak, equity)
            if peak > 0:
                drawdown_value = max(drawdown_value, (peak-equity)/peak)
        grid_history = history if statistical_history is None else statistical_history
        if statistical_history is not None:
            if maximum_history_points is None:
                _require(type(grid_history) is list and len(grid_history) == len(history), 'STATISTICAL_HISTORY_BINDING')
                observations = grid_history
            else:
                from quantos.streaming_identity import StreamArray
                _require(isinstance(grid_history, StreamArray), 'STATISTICAL_HISTORY_BINDING')
                observations = grid_history.items()
            _require(all(a is not None and b is not None and a['sequence'] == b['sequence'] and a['equity'] == b['equity']
                    and a['timestamp'] >= b['timestamp'] for a, b in zip_longest(observations, history)),
                'STATISTICAL_HISTORY_BINDING')
        if covered:
            from quantos.streaming_identity import StreamArray
            observations = grid_history.items() if isinstance(grid_history, StreamArray) else grid_history
            grid = fixed_frequency_equity(observations, start_ms=plan['start_ms'], end_ms=plan['end_ms'],
                                          frequency_ms=plan['frequency_ms'], budget=statistical_grid_budget)
            count, sharpe, status = len(grid['points'])-1, grid['annualized_sharpe'], grid['statistics_status']
        else:
            count, sharpe, status = 0, None, 'INSUFFICIENT_SAMPLE_COVERAGE'
        drawdown = canonical_decimal(drawdown_value)
    if sharpe is None:
        limitations.add(status)
    risk = {key: claims.get(key) for key in ('full_research_risk_9_of_9', 'risk_history_ledger_replayed', 'deployment_equivalent_risk')}
    _require(all(v is None or type(v) is bool for v in risk.values()), 'RISK_TYPES')
    for key, value in risk.items():
        if value is not True:
            limitations.add(key.upper() + '_NOT_PROVEN')
    if evidence_kind == 'FORWARD':
        limitations.add('FORWARD_NOT_HISTORICAL_OOS')
        limitations.add('MANIFEST_IDENTITY_IS_ARCHIVE_RAW_SHA256')
    metrics = dict(initial_equity=identity['initial_equity'], ending_equity=identity['ending_equity'],
        net_equity_change=identity['attributed_change'], fees=canonical_decimal(_number(identity['fee'])),
        funding=canonical_decimal(_number(identity['funding'])), reconciliation_residual=identity['residual'],
        max_observed_drawdown=drawdown, frequency_ms=plan['frequency_ms'], sample_count=count,
        annualized_sharpe=sharpe, statistics_status=status)
    return metrics, risk, limitations


@ledger_decimal_context()
def project_result(*, subject, originals):
    """Derive exact cash/statistics facts; never turn technical facts into permission."""
    try:
        _subject(subject)
        docs = _originals(subject, originals)
        plan = docs['evaluation_plan']
        _plan(subject, plan)
        _trials(docs['trial_history'], plan)
        if subject['native_adapter'] == 'GOLD_BAR_FORWARD_V1':
            _require(docs['manifest'].get('input_sha256') == _digest(originals['input']), 'INPUT_HASH')
            simulation, tid, result_hash, manifest_hash, claims = _gold(subject, docs)
        else:
            simulation, tid, result_hash, manifest_hash, claims = _technical(subject, docs)
        metrics, risk, limitations = _native_metrics(simulation, plan, claims, subject['evidence_kind'])
        value = dict(schema='QUANTOS_RESEARCH_RESULT_PROJECTION_V1', subject_sha256=_digest(subject),
            evidence_kind=subject['evidence_kind'], native_adapter=subject['native_adapter'],
            candidate_id=subject['candidate_id'], source_identity_sha256=subject['source_identity_sha256'],
            original_record_sha256=subject['original_record_sha256'], native_result_hash=result_hash,
            native_manifest_hash=manifest_hash, metrics=metrics,
            trial_binding=dict(trial_history_sha256=subject['trial_history_sha256'], trial_count=len(plan['trial_ids']),
                candidate_trial_id=tid, trial_set_sha256=_digest(sorted(plan['trial_ids']))),
            original_risk_claims=risk, limitations=sorted(limitations))
        _canonical(value)
        return value
    except (KeyError, TypeError, IndexError, ArithmeticError) as exc:
        raise ValueError('RESEARCH_EVIDENCE_NATIVE_RECORD') from exc


def validate_projection(value, *, subject, originals):
    actual = project_result(subject=subject, originals=originals)
    _require(_canonical(value) == _canonical(actual), 'PROJECTION_CHANGED')
    return actual


__all__ = ['project_result', 'validate_projection']
