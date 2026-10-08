"""Independent signatures over whole research populations, without admission.

The subject selects one original attempt. Signatures cover every project run,
including failed and unfinished runs; a native result ID never deduplicates an
attempt. A trusted fixed reader must separately establish filesystem/source
custody. Gold execution-gate diagnostics preserve the missing nine-engine risk
replay and independent economic qualification requirements.
"""
from __future__ import annotations

import copy
import base64
from pathlib import PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from quantos.contracts.registered_research_run import canonical as bounded_json
from quantos.contracts import registered_research_run as plan_contract
from quantos.contracts.research_evidence_json import canonical, digest, _decode
from services.validation import research_evidence_authority as original
from services.validation import research_result_projection as shape

SNAPSHOT_SCHEMA = 'QUANTOS_PROJECT_RESEARCH_EVIDENCE_SNAPSHOT_V2'
STATISTICAL_SNAPSHOT_SCHEMA = 'QUANTOS_PROJECT_RESEARCH_EVIDENCE_SNAPSHOT_V3'
KINDS = {
    'SOURCE_ATTESTATION': ('SOURCE', 'QUANTOS_PROJECT_RESEARCH_SOURCE_ATTESTATION_V2'),
    'RESULT_REVIEW': ('RESULT', 'QUANTOS_PROJECT_RESEARCH_RESULT_REVIEW_V2'),
}
STATISTICAL_KINDS = {purpose: (label, domain.removesuffix('_V2') + '_V3')
    for purpose, (label, domain) in KINDS.items()}
SOURCE_CHECKS = original.SOURCE_CHECKS
RESULT_CHECKS = original.RESULT_CHECKS
SUBJECT_FIELDS = frozenset('schema purpose evidence_kind native_adapter producer_id market invocation_id '
    'native_trial_id config_hash project_cut_sha256 source_identity_sha256'.split())
SNAPSHOT_FIELDS = frozenset('schema subject project_cut source_manifest source_envelope result_envelope '
    'trust_registry expected_registry_sha256 read_identity_sha256'.split())
require = shape._require


def _small(value):
    return bounded_json(value, maximum=original.MAX_ENVELOPE)


def _selected(cut, invocation_id, native_trial_id):
    shape._sha(invocation_id); shape._sha(native_trial_id)
    run = cut['runs'].get(invocation_id)
    require(type(run) is dict, 'PROJECT_INVOCATION')
    trials = [t for t in run['plan']['trials'] if t['native_trial_id'] == native_trial_id]
    require(len(trials) == 1, 'PROJECT_TRIAL')
    return run, trials[0]


def _original(run, name):
    entry = run['originals'].get(name)
    require(type(entry) is dict and set(entry) == {'sha256', 'bytes_base64'}, 'PROJECT_ORIGINAL')
    encoded = entry['bytes_base64']
    require(type(encoded) is str and len(encoded) <= (16_000_000+2)//3*4, 'PROJECT_ORIGINAL_BOUND')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('RESEARCH_EVIDENCE_ORIGINAL_BASE64') from exc
    require(digest(raw) == entry['sha256'] and base64.b64encode(raw).decode() == encoded, 'PROJECT_ORIGINAL_HASH')
    return _decode(raw)


def _market(run):
    inputs = _original(run, 'input.json')
    if run['plan']['adapter'] == 'TECHNICAL_LINEAR_PERPETUAL_V2':
        from quantos.contracts.registered_technical_input import project_manifest_inputs
        from quantos.contracts.backtest_identity import decode_identity_payload
        material = project_manifest_inputs(inputs)
        universe = decode_identity_payload(material['values']['universe'])
        require(type(universe) is dict and type(universe.get('value')) in (tuple, list)
            and tuple(universe['value']) == ('XAUUSDT',), 'PROJECT_MARKET')
    elif run['plan']['schema'] in plan_contract.SOURCE_GOLD_SCHEMAS:
        from quantos.contracts.gold_native_source import input_document
        input_document(inputs)
        require(run['input_material']['status'] == 'COMPLETE', 'PROJECT_MARKET')
        # This explicit input schema fixes XAUUSDT/15m; physical readers verify
        # every original before any statistics or signed evidence is consumed.
    else:
        require(type(inputs.get('capture')) is dict and inputs['capture'].get('symbol') == 'XAUUSDT',
                'PROJECT_MARKET')
    return 'XAUUSDT'


def project_subject(cut, *, invocation_id, native_trial_id):
    run, trial = _selected(cut, invocation_id, native_trial_id)
    plan = run['plan']
    return dict(schema='QUANTOS_PROJECT_RESEARCH_SUBJECT_V2', purpose='CANDIDATE_RESEARCH_REVIEW',
        evidence_kind=plan['search']['search_kind'], native_adapter=plan['adapter'],
        producer_id=plan['search']['source'], market=_market(run), invocation_id=invocation_id,
        native_trial_id=native_trial_id, config_hash=trial['config_hash'],
        project_cut_sha256=cut['cut_sha256'], source_identity_sha256=digest(plan['source_identity']))


def _subject(subject, cut):
    shape._fields(subject, SUBJECT_FIELDS, 'PROJECT_SUBJECT_FIELDS')
    require(_small(subject) == _small(project_subject(cut,
        invocation_id=subject['invocation_id'], native_trial_id=subject['native_trial_id'])),
        'PROJECT_SUBJECT_BINDING')


def material_hashes(cut, source_manifest, *, statistical_evidence=None):
    # Physical DB pages may change after retaining a review. They are recorded
    # by each read identity, never used as a circular semantic receipt identity.
    result = dict(project_cut=cut['cut_sha256'], source_manifest=digest(source_manifest))
    if statistical_evidence is not None:
        result['statistical_evidence'] = digest(statistical_evidence)
    return result


def _native_projection(run, trial, subject, *, reports=None, model_context=None):
    if not {'archive/report.json', 'archive/manifest.json'} <= set(run['originals']):
        return None, None, None, set(), None
    from quantos.contracts import registered_research_run as native
    chunked = run['plan']['schema'] in native.CHUNKED_GOLD_SCHEMAS
    if chunked:
        from quantos.contracts.project_chunked_results import report_for
        report = report_for(run, reports)
    else:
        report = _original(run, 'archive/report.json')
    if run['plan']['adapter'] != 'TECHNICAL_LINEAR_PERPETUAL_V2':
        from quantos.contracts.backtest_identity import exact_identity_hash
        if chunked:
            from quantos.streaming_identity import exact_stream_identity_hash
            identity_hash = exact_stream_identity_hash
        else:
            identity_hash = exact_identity_hash
        require((chunked or type(report) is dict) and report.get('schema') == 'GOLD_BAR_PUBLIC_COMPARISON_V1'
            and all(report.get(k) is False for k in ('authoritative', 'economic_eligible',
                'submission_enabled', 'historical_oos_evaluated'))
            and report.get('release_identity') == run['plan']['source_identity']
            and report.get('result_hash') == identity_hash(
                {k:v for k,v in report.items() if k != 'result_hash'}, name='gold_bar_public_comparison'), 'GOLD_RESULT')
        if 'forward_design' in run['plan'] and not run['partial']:
            from services.validation.gold_native_statistics import project_gold_trial
            return project_gold_trial(run, trial, reports=reports, include_execution_risk=True,
                                      model_context=model_context)
        if run['plan']['schema'] in plan_contract.EVALUATED_SOURCE_REANALYSIS_SCHEMAS and not run['partial']:
            from services.validation.gold_native_statistics import project_gold_reanalysis_trial
            return project_gold_reanalysis_trial(run, trial, reports=reports)
        return report['result_hash'], None, None, {'FORWARD_NOT_HISTORICAL_OOS'}, None
    from quantos.contracts.registered_technical_input import project_manifest_inputs
    from quantos.contracts.backtest_identity import decode_identity_payload
    manifest = _original(run, 'archive/manifest.json')
    transport = project_manifest_inputs(_original(run, 'input.json'), manifest=manifest)
    inputs = decode_identity_payload(transport['values']['data_identity'])
    docs = dict(record=report, manifest=manifest, input=transport,
        source_identity=run['plan']['source_identity'], dataset_manifest=inputs['dataset_identity'],
        candidate_definition=trial['definition_preimage']['candidate_definition'],
        trial_history=run['ledger']['history'])
    native_subject = dict(candidate_id=trial['candidate_id'], market=subject['market'],
        native_run_id=report.get('run_id'), original_record_sha256=run['originals']['archive/report.json']['sha256'],
        original_manifest_sha256=run['originals']['archive/manifest.json']['sha256'])
    simulation, selected, result_hash, _, claims = shape._technical(native_subject, docs)
    require(selected == trial['native_trial_id'], 'PROJECT_NATIVE_TRIAL')
    core = run['plan'].get('evaluation_core')
    if core is None:
        return result_hash, None, claims, set(), None
    metrics, risk, limitations = shape._native_metrics(simulation, core['sample'], claims, subject['evidence_kind'])
    return result_hash, metrics, risk, limitations, None


def project_facts(cut, subject, *, statistical_evidence=None, as_of_ms=None, reports=None):
    from services.validation.project_research_population import validate_project_cut
    validate_project_cut(cut)
    from quantos.contracts.project_chunked_results import full_report_population
    full_report_population(cut, reports)
    _subject(subject, cut)
    run, trial = _selected(cut, subject['invocation_id'], subject['native_trial_id'])
    attempt, = [a for a in cut['attempts'] if a['invocation_id'] == subject['invocation_id']
                and a['native_trial_id'] == trial['native_trial_id']]
    reasons = {'RISK_REPLAY_ADAPTER_UNAVAILABLE', 'PROJECT_NATIVE_RESULT_PROJECTION_UNAVAILABLE',
               'NO_PROMOTION_OR_EXECUTION_APPROVAL'}
    # V1 registration records only TECHNICAL_COMPUTATION_ONLY. Do not invent a
    # past evaluation plan from a later report or current registry timestamp.
    core = run['plan'].get('evaluation_core')
    if core is None:
        core_state = None
        if 'forward_design' in run['plan']:
            reasons.update(('FORWARD_WINDOW_DECLARED_NOT_INDEPENDENTLY_VERIFIED',
                            'GOLD_EVALUATION_CORE_ADAPTER_UNAVAILABLE'))
        else:
            reasons.add('EVALUATION_PLAN_NOT_PREREGISTERED')
    else:
        from quantos.contracts.research_evaluation_plan import core_status
        core_state = core_status(core)
        reasons.update(core_state['reasons'])
    if cut['population_coverage'] != 'COMPLETE':
        reasons.add('PROJECT_POPULATION_UNMAPPED')
    if not cut['all_runs_finalized']:
        reasons.add('PROJECT_RUNS_UNFINISHED')
    if attempt['status'] != 'PASSED' or run['partial']:
        reasons.add('SELECTED_NATIVE_ATTEMPT_NOT_FINALIZED')
    validated_statistics = None
    if statistical_evidence is not None:
        from services.validation import project_research_statistical_evidence as statistics
        validated_statistics = statistics.validate(statistical_evidence, project_cut=cut, as_of_ms=as_of_ms, reports=reports)
        selected = validated_statistics['cohort']['selected_attempt']
        require(selected is not None and all(selected[key] == subject[key]
            for key in ('invocation_id', 'native_trial_id', 'config_hash')), 'STATISTICAL_SUBJECT_BINDING')
    model_context = dict(project_cut=cut, subject=subject, verified_statistics=validated_statistics,
                         checked_at_ms=as_of_ms)
    result_hash, metrics, risk, limitations, execution_risk = _native_projection(run, trial, subject,
        reports=reports, model_context=model_context)
    reasons.update(limitations)
    if metrics is not None:
        reasons.discard('PROJECT_NATIVE_RESULT_PROJECTION_UNAVAILABLE')
        reasons.add('MULTIPLE_TESTING_AND_BENCHMARK_REVIEW_NOT_COMPLETED')
    facts = dict(schema='QUANTOS_PROJECT_RESEARCH_FACTS_V2', subject_sha256=digest(subject),
        project_cut_sha256=cut['cut_sha256'], selected_attempt=copy.deepcopy(attempt),
        original_record_sha256=run['originals'].get('archive/report.json', {}).get('sha256'),
        original_manifest_sha256=run['originals'].get('archive/manifest.json', {}).get('sha256'),
        attempt_count=cut['attempt_count'], unique_definition_count=cut['unique_definition_count'],
        unfinished_attempt_count=len(cut['unfinished_attempts']),
        unfinished_run_count=len(cut['unfinished_runs']), population_coverage=cut['population_coverage'],
        native_result_hash=result_hash, metrics=metrics, original_risk_claims=risk,
        evaluation_core=core_state, limitations=sorted(reasons),
        promotion_approved=False, submission_enabled=False)
    from quantos.contracts.registered_research_run import RISK_EVALUATED_SCHEMA
    if run['plan']['schema']==RISK_EVALUATED_SCHEMA:
        facts['schema']='QUANTOS_PROJECT_RESEARCH_FACTS_V3'
        diagnostic=None
        if 'archive/risk-replay.json' in run['originals']:
            artifact=_original(run,'archive/risk-replay.json')
            replay=artifact['replay']
            diagnostic=dict(policy_preregistered=True,policy_sha256=digest(run['plan']['risk_review_policy']),
                artifact_sha256=run['originals']['archive/risk-replay.json']['sha256'],
                status=replay['status'],replay_sha256=artifact['replay_sha256'],
                mathematical_independence=False,limitations=copy.deepcopy(replay['limitations']))
            reasons.discard('RISK_REPLAY_ADAPTER_UNAVAILABLE')
            reasons.add('RISK_REPLAY_'+replay['status'])
            # The adapter's original limitations are historical diagnostics;
            # this separate fact proves the actual V3 policy preregistration.
            reasons.update(replay['limitations'])
        else:
            reasons.add('REGISTERED_RISK_REPLAY_NOT_RETAINED')
        facts['risk_replay']=diagnostic
        facts['limitations']=sorted(reasons)
    if statistical_evidence is not None:
        from services.validation import project_research_statistical_evidence as statistics
        value = validated_statistics
        selected = value['cohort']['selected_attempt']
        require(selected is not None and all(selected[key] == subject[key] for key in ('invocation_id', 'native_trial_id', 'config_hash')),
            'STATISTICAL_SUBJECT_BINDING')
        facts['schema'] = 'QUANTOS_PROJECT_RESEARCH_FACTS_V4'
        facts['statistical_review'] = statistics.projection(value)
        reasons.discard('MULTIPLE_TESTING_AND_BENCHMARK_REVIEW_NOT_COMPLETED')
        from quantos.contracts.research_statistical_review import GOLD_REPORT_SCHEMAS
        if value['report']['schema'] in GOLD_REPORT_SCHEMAS:
            reasons.discard('STATISTICAL_REVIEW_REQUIRES_COMPLETE_PROJECT_CUT')
            reasons.discard('STATISTICAL_REVIEW_NOT_IMPLEMENTED')
        reasons.update(value['report']['reasons'])
        reasons.update(value['report']['limitations'])
        if value['report']['statistical_decision'] != 'PASS':
            reasons.add('STATISTICAL_REVIEW_' + value['report']['statistical_decision'])
        facts['limitations'] = sorted(reasons)
    if execution_risk is not None:
        facts['schema'] = ('QUANTOS_PROJECT_RESEARCH_FACTS_V13'
                           if execution_risk.get('schema') == 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V10' else
                           'QUANTOS_PROJECT_RESEARCH_FACTS_V12'
                           if execution_risk.get('schema') == 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V9' else
                           'QUANTOS_PROJECT_RESEARCH_FACTS_V11'
                           if execution_risk.get('schema') == 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V8' else
                           'QUANTOS_PROJECT_RESEARCH_FACTS_V10'
                           if execution_risk.get('schema') == 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V7' else
                           'QUANTOS_PROJECT_RESEARCH_FACTS_V9'
                           if execution_risk.get('schema') == 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V6' else
                           'QUANTOS_PROJECT_RESEARCH_FACTS_V8'
                           if execution_risk.get('schema') == 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V5' else
                           'QUANTOS_PROJECT_RESEARCH_FACTS_V7'
                           if execution_risk.get('schema') == 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V4' else
                           'QUANTOS_PROJECT_RESEARCH_FACTS_V6' if 'account_risk_inputs' in execution_risk
                           else 'QUANTOS_PROJECT_RESEARCH_FACTS_V5')
        facts['execution_risk_review'] = execution_risk
        reasons.update(execution_risk['limitations'])
        facts['limitations'] = sorted(reasons)
    return facts


def signing_payload(unsigned_envelope):
    shape._fields(unsigned_envelope, ('schema', 'purpose', 'authority_id', 'key_id', 'statement'), 'ENVELOPE_FIELDS')
    purpose = unsigned_envelope['purpose']
    require(type(purpose) is str and purpose in KINDS, 'PURPOSE')
    version = next((version for version in (2, 3) if unsigned_envelope['schema'] ==
        f'QUANTOS_PROJECT_RESEARCH_{KINDS[purpose][0]}_ENVELOPE_V{version}'), None)
    require(version is not None, 'ENVELOPE_SCHEMA')
    _, domain = (STATISTICAL_KINDS if version == 3 else KINDS)[purpose]
    shape._text(unsigned_envelope['authority_id']); shape._text(unsigned_envelope['key_id'])
    return domain.encode('ascii') + b'\0' + _small(unsigned_envelope)


def _verify(envelope, *, purpose, registry, subject, materials, now_ms, version=2):
    shape._fields(envelope, ('schema', 'purpose', 'authority_id', 'key_id', 'statement', 'signature_base64url'), 'SIGNED_FIELDS')
    _small(envelope)
    require(envelope['purpose'] == purpose, 'SIGNED_PURPOSE')
    require(envelope['schema'] == f'QUANTOS_PROJECT_RESEARCH_{KINDS[purpose][0]}_ENVELOPE_V{version}',
        'SIGNED_VERSION')
    payload = signing_payload({k:v for k,v in envelope.items() if k != 'signature_base64url'})
    keys = [k for k in registry['keys'].values() if k['authority_id'] == envelope['authority_id']
            and k['key_id'] == envelope['key_id']]
    require(len(keys) == 1, 'KEY_NOT_TRUSTED')
    key = keys[0]
    require(key['status'] == 'ACTIVE' and purpose in key['purposes']
        and subject['evidence_kind'] in key['evidence_kinds'] and subject['market'] in key['markets']
        and key['not_before_ms'] <= now_ms <= key['not_after_ms'], 'KEY_NOT_AUTHORIZED')
    statement = envelope['statement']
    fields = set('schema subject_sha256 materials_sha256 issued_at_ms expires_at_ms revocation_checked_at_ms '
                 'revocation_status checks'.split())
    if purpose == 'RESULT_REVIEW':
        fields |= {'source_envelope_sha256', 'projection_sha256', 'review_verdict'}
    shape._fields(statement, fields, 'STATEMENT_FIELDS')
    require(statement['schema'] == f'QUANTOS_PROJECT_RESEARCH_{KINDS[purpose][0]}_STATEMENT_V{version}'
        and statement['subject_sha256'] == digest(subject)
        and _small(statement['materials_sha256']) == _small(materials), 'STATEMENT_BINDING')
    for field in ('issued_at_ms', 'expires_at_ms', 'revocation_checked_at_ms'):
        shape._integer(statement[field])
    require(statement['issued_at_ms'] <= now_ms <= statement['expires_at_ms']
        and 0 < statement['expires_at_ms']-statement['issued_at_ms'] <= original.MAX_LIFETIME_MS,
        'STATEMENT_TIME')
    require(statement['revocation_status'] == 'GOOD'
        and 0 <= now_ms-statement['revocation_checked_at_ms'] <= original.MAX_LIFETIME_MS, 'REVOCATION_TIME')
    checks = set(SOURCE_CHECKS if purpose == 'SOURCE_ATTESTATION' else RESULT_CHECKS)
    statistical_check = ('STATISTICAL_POLICY_PREREGISTRATION' if purpose == 'SOURCE_ATTESTATION'
        else 'STATISTICAL_REVIEW_REPLAY')
    if version == 3:
        checks.add(statistical_check)
    shape._fields(statement['checks'], checks, 'CHECKS')
    for check in statement['checks'].values():
        shape._fields(check, ('status', 'evidence_roles'), 'CHECK')
        roles = check['evidence_roles']
        require(check['status'] in ('PASS', 'FAIL', 'UNKNOWN') and type(roles) is list
            and len(roles) <= len(materials) and all(type(r) is str and r in materials for r in roles)
            and len(set(roles)) == len(roles) and (roles or check['status'] == 'UNKNOWN'), 'CHECK_EVIDENCE')
    if version == 3:
        check = statement['checks'][statistical_check]
        require(check['status'] == 'UNKNOWN' or set(check['evidence_roles']) ==
            {'project_cut', 'statistical_evidence'}, 'STATISTICAL_CHECK_EVIDENCE')
    if purpose == 'RESULT_REVIEW':
        shape._sha(statement['source_envelope_sha256']); shape._sha(statement['projection_sha256'])
        require(statement['review_verdict'] in ('PASS', 'REJECTED', 'INSUFFICIENT'), 'REVIEW_VERDICT')
    try:
        Ed25519PublicKey.from_public_bytes(original._b64(key['public_key_base64url'], 32)).verify(
            original._b64(envelope['signature_base64url'], 64), payload)
    except InvalidSignature as exc:
        raise ValueError('RESEARCH_EVIDENCE_SIGNATURE') from exc


def verify_project_evidence(snapshot, *, now_ms, reports=None):
    shape._integer(now_ms)
    require(type(snapshot) is dict and snapshot.get('schema') in (SNAPSHOT_SCHEMA, STATISTICAL_SNAPSHOT_SCHEMA),
        'PROJECT_SNAPSHOT_SCHEMA')
    version = 3 if snapshot['schema'] == STATISTICAL_SNAPSHOT_SCHEMA else 2
    shape._fields(snapshot, SNAPSHOT_FIELDS | ({'statistical_evidence'} if version == 3 else set()),
        'PROJECT_SNAPSHOT_FIELDS')
    shape._sha(snapshot['read_identity_sha256'])
    subject, cut = snapshot['subject'], snapshot['project_cut']
    evidence = snapshot.get('statistical_evidence')
    require(version != 3 or type(evidence) is dict, 'STATISTICAL_EVIDENCE_REQUIRED')
    projected = project_facts(cut, subject, statistical_evidence=evidence, as_of_ms=now_ms, reports=reports)
    manifest = snapshot['source_manifest']
    shape._fields(manifest, ('schema', 'source_identity_sha256', 'files'), 'SOURCE_MANIFEST')
    require(manifest['schema'] == 'QUANTOS_RESEARCH_SOURCE_MANIFEST_V1'
        and manifest['source_identity_sha256'] == subject['source_identity_sha256']
        and type(manifest['files']) is dict and 0 < len(manifest['files']) <= shape.MAX_ITEMS, 'SOURCE_MANIFEST')
    for name, value in manifest['files'].items():
        require(type(name) is str and 0 < len(name) <= 1024 and '\\' not in name and '\0' not in name
            and not name.startswith('/') and all(p not in ('', '.', '..') for p in name.split('/'))
            and str(PurePosixPath(name)) == name, 'SOURCE_PATH')
        shape._sha(value)
    registry = original._registry(snapshot['trust_registry'], snapshot['expected_registry_sha256'], subject)
    materials = material_hashes(cut, manifest, statistical_evidence=evidence)
    for purpose, name in (('SOURCE_ATTESTATION', 'source_envelope'), ('RESULT_REVIEW', 'result_envelope')):
        _verify(snapshot[name], purpose=purpose, registry=registry, subject=subject, materials=materials,
            now_ms=now_ms, version=version)
        if version == 3:
            require(snapshot[name]['statement']['issued_at_ms'] >= evidence['report']['as_of_ms'],
                'STATEMENT_BEFORE_STATISTICAL_REVIEW')
    result = snapshot['result_envelope']['statement']
    require(result['source_envelope_sha256'] == digest(snapshot['source_envelope'])
        and result['projection_sha256'] == digest(projected), 'REVIEW_BINDING')
    reasons = set(projected['limitations'])
    rejected = result['review_verdict'] == 'REJECTED'
    rejected |= (projected.get('risk_replay') or {}).get('status') == 'REJECTED'
    rejected |= (projected.get('execution_risk_review') or {}).get('status') == 'REJECTED'
    rejected |= (projected.get('statistical_review') or {}).get('statistical_decision') == 'FAIL'
    if result['review_verdict'] != 'PASS':
        reasons.add('INDEPENDENT_REVIEW_' + result['review_verdict'])
    for name in ('source_envelope', 'result_envelope'):
        for check_name, check in snapshot[name]['statement']['checks'].items():
            if check['status'] != 'PASS':
                reasons.add(check_name + '_' + check['status'])
            rejected |= check['status'] == 'FAIL'
    return dict(schema=f'QUANTOS_PROJECT_RESEARCH_ASSESSMENT_V{version}', subject=copy.deepcopy(subject),
        authentication='VERIFIED_SOURCE_AND_RESULT', qualification='REJECTED' if rejected else 'INSUFFICIENT',
        reasons=sorted(reasons), projection=projected, projection_sha256=digest(projected),
        source_envelope_sha256=digest(snapshot['source_envelope']), result_envelope_sha256=digest(snapshot['result_envelope']),
        trust_registry_sha256=snapshot['expected_registry_sha256'], checked_at_ms=now_ms,
        promotion_approved=False, submission_enabled=False)


def consume_project_evidence(*, read_snapshot, expected_subject, clock_ms, reports_for_snapshot=None):
    first = read_snapshot()
    identity = canonical(first)
    require(_small(first['subject']) == _small(expected_subject), 'PROJECT_EXPECTED_SUBJECT')
    started = shape._integer(clock_ms())
    verify_project_evidence(first, now_ms=started,
        reports=None if reports_for_snapshot is None else reports_for_snapshot(first))
    second = read_snapshot()
    require(canonical(second) == identity, 'READ_CHANGED')
    result = verify_project_evidence(second, now_ms=started,
        reports=None if reports_for_snapshot is None else reports_for_snapshot(second))
    # Sample after both whole-population checks and signature verifications.
    finished = shape._integer(clock_ms())
    require(finished >= started, 'CLOCK_ROLLBACK')
    for name in ('source_envelope', 'result_envelope'):
        envelope = second[name]
        statement = envelope['statement']
        key = next(k for k in second['trust_registry']['keys'].values()
            if k['authority_id'] == envelope['authority_id'] and k['key_id'] == envelope['key_id'])
        require(statement['issued_at_ms'] <= finished <= statement['expires_at_ms']
            and key['not_before_ms'] <= finished <= key['not_after_ms']
            and 0 <= finished-statement['revocation_checked_at_ms'] <= original.MAX_LIFETIME_MS,
            'DELIVERY_EXPIRED')
    result['checked_at_ms'] = finished
    return result
