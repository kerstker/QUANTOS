"""Historical project reviews in the original evaluation row contract.

The signed cut precedes its own persistence. Current use must verify a fresh cut
and the explicit review-only delta; a historical row never grants admission.
No store or filesystem is reachable from this pure verifier.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

from quantos.contracts.research_evidence_json import canonical, digest, decode, require
from quantos.contracts.research_registry_identity import search_identity_hash
from services.validation import project_research_evidence_authority as authority
from services.validation import project_research_population as population_contract

SCHEMA = 'QUANTOS_PROJECT_RESEARCH_EVIDENCE_ARTIFACT_V2'
STATISTICAL_SCHEMA = 'QUANTOS_PROJECT_RESEARCH_EVIDENCE_ARTIFACT_V3'
SCHEMAS = (SCHEMA, STATISTICAL_SCHEMA)
FIELDS = frozenset(('schema', 'search_identity', 'config_hash', 'snapshot', 'assessment'))
ROW_FIELDS = frozenset(('run_id', 'alpha_id', 'dataset_version', 'recorded_ts',
                        'verdict', 'content_hash', 'evaluation_json'))
METADATA_FIELDS = ('run_id', 'dataset_version', 'recorded_ts', 'content_hash')


class _RetainedReports(dict):
    """Subset of owning views; original inputs retain their existing lease."""


def _same(left, right, reason):
    require(canonical(left) == canonical(right), reason)


def _binding(snapshot):
    subject, cut = snapshot['subject'], snapshot['project_cut']
    run, trial = authority._selected(cut, subject['invocation_id'], subject['native_trial_id'])
    require(not run['partial'] and subject['invocation_id'] not in cut['unfinished_runs'],
            'SELECTED_NOT_FINALIZED')
    search = run['plan']['search']
    identity = {k:search[k] for k in ('search_id', 'search_kind', 'source', 'dataset_version')}
    identity['identity_hash'] = search_identity_hash(**identity)
    outcome = authority._original(run, 'outcome.json')
    events = run['ledger']['history'][trial['native_trial_id']]
    terminal = [row for row in cut['registry_history']['events']
                if row['search_id'] == search['search_id'] and row['status'] != 'STARTED']
    require(events[-1]['status'] in ('PASSED', 'FAILED')
        and events[-1]['status'] == outcome['status'] and len(terminal) == 1
        and terminal[0]['status'] == ('COMPLETED' if outcome['status'] == 'PASSED' else 'FAILED')
        and terminal[0]['recorded_at'] == events[-1]['at'] == outcome['completed_at_ms'],
        'SELECTED_TERMINAL')
    return identity, trial, terminal[0]['recorded_at']


def retained_reports(cut, reports):
    """A historical artifact consumes every complete report in its own cut.

    A current lease may also hold newer reports. Those additional originals
    cannot enlarge or replace the historical population bound by the row.
    """
    from quantos.contracts import registered_research_run as native
    expected = {key for key, run in cut['runs'].items()
        if run['plan']['schema'] in native.CHUNKED_GOLD_SCHEMAS
        and run['result_material']['status'] == 'COMPLETE'}
    inputs = {key for key, run in cut['runs'].items()
        if run['plan']['schema'] in native.SOURCE_GOLD_SCHEMAS
        and run['input_material']['status'] == 'COMPLETE'}
    if (expected or inputs) and isinstance(reports, Mapping):
        require(expected <= set(reports), 'PROJECT_CHUNKED_MATERIAL_REQUIRED')
        result = _RetainedReports((key, reports[key]) for key in expected)
        risk_port = getattr(reports, 'review_gold_risk', None)
        if callable(risk_port):
            # Keep the owning callable, not a new engine/lease or a serialized
            # assertion. Nested historical subsets expire with their owner.
            result.review_gold_risk = risk_port
        causality_port = getattr(reports, 'review_gold_causality', None)
        if callable(causality_port):
            result.review_gold_causality = causality_port
        forward_port=getattr(reports,'review_gold_net_forward',None)
        if callable(forward_port):
            result.review_gold_net_forward=forward_port
        maintenance_port=getattr(reports,'review_gold_maintenance',None)
        if callable(maintenance_port):
            result.review_gold_maintenance=maintenance_port
        frozen_port=getattr(reports,'review_gold_frozen_validation',None)
        if callable(frozen_port):
            result.review_gold_frozen_validation=frozen_port
        if inputs:
            originals = getattr(reports, 'source_inputs', None)
            require(isinstance(originals, Mapping) and inputs <= set(originals),
                    'PROJECT_CHUNKED_SOURCE_MATERIAL_REQUIRED')
            result.source_inputs = {key:originals[key] for key in inputs}
        return result
    return reports


def prepare(*, snapshot, assessment, reports=None):
    # Validation of the complete cut precedes using its derived run bindings.
    actual = authority.verify_project_evidence(snapshot, now_ms=assessment['checked_at_ms'],
        reports=retained_reports(snapshot['project_cut'], reports))
    _same(actual, assessment, 'ASSESSMENT')
    identity, trial, _ = _binding(snapshot)
    schema = STATISTICAL_SCHEMA if snapshot['schema'] == authority.STATISTICAL_SNAPSHOT_SCHEMA else SCHEMA
    value = dict(schema=schema, search_identity=identity, config_hash=trial['config_hash'],
                 snapshot=copy.deepcopy(snapshot), assessment=copy.deepcopy(assessment))
    return validate(value, reports=reports)


def last_project_fact_ms(cut):
    """Latest actual fact in an already validated cut; no current authority."""
    times = [0]
    for run in cut['runs'].values():
        times.append(run['plan']['search']['recorded_at_ms'])
        times.extend(row['at'] for events in run['ledger']['history'].values() for row in events)
        # A different run can stop after a durable witness and before its next
        # ledger event. Those actual facts still belong to this signed cut.
        for name, fields in (
            ('invocation.json', ('started_at_ms',)),
            ('outcome.json', ('completed_at_ms',)),
            ('archive/risk-replay.json', ('started_at_ms', 'completed_at_ms')),
        ):
            if name in run['originals']:
                retained = authority._original(run, name)
                times.extend(retained[field] for field in fields)
    for key, field in (('events','recorded_at'), ('heartbeats','recorded_at'),
                       ('recoveries','recovered_at'), ('evaluations','recorded_ts'),
                       ('evaluation_links','recorded_at')):
        times.extend(row[field] for row in cut['registry_history'][key])
    times.extend(row['viewed_at'] for row in cut['registry_population']['oos'])
    times.extend(row['registered_at'] for row in cut['registry_population']['search_candidates'])
    times.extend(row['first_seen_ts'] for row in cut['registry_population']['trials'])
    for row in cut['registry_population']['provenance']:
        times.append(row['started_at'])
        if row['completed_at'] is not None:
            times.append(row['completed_at'])
    return max(times)


def validate(value, *, reports=None):
    authority.shape._fields(value, FIELDS, 'PROJECT_ARTIFACT_FIELDS')
    require(value['schema'] in SCHEMAS, 'PROJECT_ARTIFACT_SCHEMA')
    canonical(value)
    assessment, snapshot = value['assessment'], value['snapshot']
    require(type(assessment) is dict and type(assessment.get('checked_at_ms')) is int, 'ASSESSMENT')
    actual = authority.verify_project_evidence(snapshot, now_ms=assessment['checked_at_ms'],
        reports=retained_reports(snapshot['project_cut'], reports))
    require((value['schema'] == STATISTICAL_SCHEMA) ==
        (snapshot['schema'] == authority.STATISTICAL_SNAPSHOT_SCHEMA), 'PROJECT_ARTIFACT_VERSION')
    _same(actual, assessment, 'ASSESSMENT')
    identity, trial, ended = _binding(snapshot)
    _same(value['search_identity'], identity, 'SEARCH_IDENTITY')
    require(value['config_hash'] == trial['config_hash'], 'CONFIG_BINDING')
    require(assessment['checked_at_ms'] >= ended, 'REVIEW_BEFORE_COMPLETION')
    cut = snapshot['project_cut']
    last_fact = max(ended, last_project_fact_ms(cut))
    require(last_fact <= assessment['checked_at_ms'], 'REVIEW_BEFORE_PROJECT_FACTS')
    for name in ('source_envelope', 'result_envelope'):
        require(snapshot[name]['statement']['issued_at_ms'] >= last_fact,
                'STATEMENT_BEFORE_PROJECT_FACTS')
    return value


def review_identity(value, *, reports=None):
    """Stable retry facts; observing unchanged facts later is not a new review."""
    validate(value, reports=reports)
    result = copy.deepcopy(value)
    del result['assessment']['checked_at_ms']
    del result['snapshot']['read_identity_sha256']
    del result['snapshot']['project_cut']['read_identity_sha256']
    return digest(result)


def row_fields(value, *, reports=None):
    validate(value, reports=reports)
    snapshot, assessment = value['snapshot'], value['assessment']
    subject = snapshot['subject']
    _, trial = authority._selected(snapshot['project_cut'], subject['invocation_id'], subject['native_trial_id'])
    version = 3 if value['schema'] == STATISTICAL_SCHEMA else 2
    run_id = digest(dict(domain=f'QUANTOS_PROJECT_RESEARCH_EVIDENCE_ARTIFACT_ID_V{version}',
        subject=subject, source_envelope=snapshot['source_envelope'], result_envelope=snapshot['result_envelope']))
    raw = canonical(value)
    return dict(run_id=run_id, alpha_id=trial['candidate_id'],
        dataset_version=value['search_identity']['dataset_version'], recorded_ts=assessment['checked_at_ms'],
        verdict=assessment['qualification'],
        content_hash=digest(f'QUANTOS_PROJECT_RESEARCH_EVIDENCE_ARTIFACT_CONTENT_V{version}\0'.encode('ascii')+raw),
        evaluation_json=raw.decode())


def validate_row(row, *, reports=None):
    require(type(row) is dict and set(row) in (ROW_FIELDS, ROW_FIELDS | {'audit_intent'}), 'ROW_FIELDS')
    require(type(row['evaluation_json']) is str, 'ROW_JSON')
    value = decode(row['evaluation_json'].encode())
    expected = row_fields(value, reports=reports)
    _same({k:row[k] for k in ROW_FIELDS}, expected, 'ROW_BINDING')
    return value


def _keyed_prefix(old, current, key, reason):
    indexed = {row[key]:row for row in current}
    require(len(indexed) == len(current), reason)
    for row in old:
        _same(row, indexed.get(row[key]), reason)


def verify_registry_prefix(value, *, population, history):
    """Historical ancestry, including old activities; never current eligibility."""
    population_contract._registry(population, history)
    retained = value['snapshot']['project_cut']
    old = retained['registry_population']
    _keyed_prefix(old['trials'], population['trials'], 'config_hash', 'REGISTRY_TRIAL_PREFIX')
    for name in ('provenance', 'oos', 'search_candidates'):
        _same(old[name], population[name][:len(old[name])], 'REGISTRY_POPULATION_PREFIX')
    for name in ('events', 'heartbeats', 'recoveries', 'evaluation_links'):
        rows = retained['registry_history'][name]
        _same(rows, history[name][:len(rows)], 'REGISTRY_HISTORY_PREFIX')
    _keyed_prefix(retained['registry_history']['evaluations'], history['evaluations'],
                  'run_id', 'REGISTRY_EVALUATION_PREFIX')


def added_review_ids(reviewed_cut, current_cut):
    old = {row['run_id'] for row in reviewed_cut['registry_history']['evaluations']}
    return sorted(row['run_id'] for row in current_cut['registry_history']['evaluations'] if row['run_id'] not in old)


def verify_current_cut(reviewed_cut, current_cut, *, review_rows, reports=None):
    """Only fully verified persisted review metadata may follow a signed cut.

    The DB boundary supplies and verifies each row's unique original audit and
    absence of activity links. This function verifies the actual artifact body
    and exact metadata. It never fetches nested rows or repeats a current read.
    """
    population_contract.validate_project_cut(reviewed_cut)
    population_contract.validate_project_cut(current_cut)
    before, after = copy.deepcopy(reviewed_cut), copy.deepcopy(current_cut)
    old_metadata = before['registry_history'].pop('evaluations')
    current_metadata = after['registry_history'].pop('evaluations')
    for value in (before, after):
        del value['cut_sha256']; del value['read_identity_sha256']
    _same(before, after, 'CURRENT_PROJECT_CHANGED')
    _keyed_prefix(old_metadata, current_metadata, 'run_id', 'CURRENT_EVALUATION_CHANGED')
    new_ids = set(added_review_ids(reviewed_cut, current_cut))
    require(type(review_rows) is dict and set(review_rows) == new_ids, 'CURRENT_REVIEW_ROWS')
    metadata = {row['run_id']:row for row in current_metadata}
    for run_id, row in review_rows.items():
        value = validate_row(row, reports=reports)
        require(row['run_id'] == run_id, 'CURRENT_REVIEW_ID')
        _same({k:row[k] for k in METADATA_FIELDS}, metadata[run_id], 'CURRENT_REVIEW_METADATA')
        verify_registry_prefix(value, population=current_cut['registry_population'],
                               history=current_cut['registry_history'])
