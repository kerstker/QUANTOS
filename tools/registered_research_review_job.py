"""Export complete original review material and consume an independent reply.

No signing or trust-installation operation exists here. Trust pins are supplied
by the controlled process configuration, never a queue request or reply file.
"""
import base64
import os
import time

from quantos.contracts import registered_research_review_job as contract
from quantos.contracts.research_evidence_json import canonical, decode, digest
from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader, _ProjectReads
from services.research.project_research_evidence_snapshot import ProjectResearchEvidenceSnapshotReader
from services.research.project_research_review_request import LIMITS, REQUEST_SCHEMA, read_request as read_packet
from services.research.project_research_review_request import (
    CHUNKED_REQUEST_SCHEMA, SOURCE_REQUEST_SCHEMA, result_material, input_material)
from quantos.contracts import gold_native_source
from quantos.contracts.gold_forward_evaluation_design import FULL_REVIEW_SCHEMAS
from services.research.research_evidence_reader import _source, _path
from services.validation import project_research_evidence_authority as authority
from services.validation import project_research_statistical_evidence as statistical
from services.validation.project_research_evidence_artifact import last_project_fact_ms
from tools import registered_research_job as producer
from tools.perpetual_replay_research import ROOT, source_identity

common = producer.common


def _now():
    return time.time_ns() // 1_000_000


def _directory(parent, name):
    common.require(common.contract._hash(name), 'REVIEW_REQUEST_ID')
    return parent / name


def response_directory(request_id):
    _, database = producer.workspace_paths()
    return _directory(database.parent / 'signed_reviews', request_id)


def _cohort(cut, policy, invocation, *, reports=None):
    from quantos.contracts.gold_reanalysis_statistics import SCHEMA as BACKTEST_POLICY
    if policy.get('schema') == BACKTEST_POLICY:
        from services.validation import gold_project_statistics
        return gold_project_statistics.project_cohort(project_cut=cut, invocation_id=invocation,
            policy=policy, reports=reports)
    if policy.get('schema') in FULL_REVIEW_SCHEMAS:
        from services.validation import gold_project_statistics
        return gold_project_statistics.project_cohort(project_cut=cut, invocation_id=invocation, reports=reports)
    rows = sorted(({key:attempt[key] for key in ('invocation_id', 'native_trial_id',
        'search_id', 'provenance_trial_id', 'config_hash')} for attempt in cut['attempts']),
        key=lambda row:(row['invocation_id'], row['native_trial_id']))
    selection, benchmark = policy['selection_policy'], policy['benchmark_policy']

    def matches(row, candidate, definition, input_hash=None):
        run = cut['runs'][row['invocation_id']]
        trial = next(t for t in run['plan']['trials'] if t['native_trial_id'] == row['native_trial_id'])
        return (trial['candidate_id'] == candidate and trial['config_hash'] == definition
            and (input_hash is None or run['plan']['input_sha256'] == input_hash))

    selected = [row for row in rows if matches(row, selection['candidate_id'], selection['definition_sha256'])]
    baseline = [row for row in rows if matches(row, benchmark['id'], benchmark['definition_sha256'], benchmark['input_sha256'])]
    common.require(selected and baseline and selected[0]['invocation_id'] == invocation, 'REVIEW_SELECTION')
    return dict(schema='QUANTOS_RESEARCH_STATISTICAL_COHORT_V1',
        study_sha256=policy['study_scope']['study_sha256'], policy_sha256=digest(policy),
        project_cut_sha256=cut['cut_sha256'], frozen_at_ms=last_project_fact_ms(cut),
        attempts=rows, selected_attempt=selected[0], benchmark_attempt=baseline[0])


def _source_files(reads, identity):
    paths = sorted(p for folder in ('services', 'quantos', 'tools') for p in (ROOT/folder).rglob('*.py'))
    raw = {p.relative_to(ROOT).as_posix():reads.read(p, 16_000_000) for p in paths}
    manifest = dict(schema='QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',
        source_identity_sha256=digest(identity), files={name:digest(value) for name,value in raw.items()})
    _source(reads, ROOT, dict(source_identity=canonical(identity), source_manifest=canonical(manifest)), identity['commit'])
    lock = 'requirements-ci-linux-aarch64.lock'
    raw[lock] = reads.read(ROOT/lock, 16_000_000)
    return manifest, dict(schema='QUANTOS_REVIEW_SOURCE_FILES_V1',
        files={name:base64.b64encode(value).decode('ascii') for name,value in raw.items()})


def _write_original(path, raw, maximum):
    if path.exists() or path.is_symlink():
        common.require(common._read(path, maximum=maximum) == raw, 'REVIEW_PACKET_CONFLICT')
        return
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    common._sync(path.parent)


def _publish_results(directory, materials, *, project_root, reads):
    root = common._mkdir(directory/'results')
    common.require(set(p.name for p in root.iterdir()) <= set(materials), 'REVIEW_RESULT_POPULATION')
    for invocation, material in sorted(materials.items()):
        parts = common._mkdir(root/invocation)
        blobs = common._mkdir(parts/'blobs')
        common.require(set(p.name for p in parts.iterdir()) <= {'manifest.json', 'blobs'}
            and set(p.name for p in blobs.iterdir()) <=
                {name.split('/')[1] for name in material['files'] if name.startswith('blobs/')},
            'REVIEW_RESULT_MEMBERS')
        source = project_root/'runs'/invocation/'archive/report-parts'
        # The project reader has already pinned every source in this read set.
        # Re-read using its original finite limit, then copy the complete bytes.
        for name, binding in sorted(material['files'].items()):
            path = source/name
            common.require(path in reads.files, 'REVIEW_RESULT_SOURCE')
            raw = reads.read(path, reads.files[path][2])
            common.require(len(raw) == binding['bytes'] and digest(raw) == binding['sha256'], 'REVIEW_RESULT_BYTES')
            _write_original(parts/name, raw, reads.files[path][2])


def _publish_inputs(directory, materials, *, project_root, reads):
    root = common._mkdir(directory/'inputs')
    common.require(set(p.name for p in root.iterdir()) <= set(materials), 'REVIEW_INPUT_POPULATION')
    for invocation, material in sorted(materials.items()):
        destination = common._mkdir(root/invocation)
        common.require(set(p.name for p in destination.iterdir()) <= {gold_native_source.DIRECTORY}, 'REVIEW_INPUT_MEMBERS')
        parts = common._mkdir(destination/gold_native_source.DIRECTORY)
        blobs = common._mkdir(parts/'blobs')
        common.require(set(p.name for p in parts.iterdir()) <= {'manifest.json', 'blobs'}
            and set(p.name for p in blobs.iterdir()) <=
                {name.split('/')[1] for name in material['files'] if name.startswith('blobs/')}, 'REVIEW_INPUT_MEMBERS')
        source = project_root/'runs'/invocation/gold_native_source.DIRECTORY
        for name, binding in sorted(material['files'].items()):
            path = source/name
            common.require(path in reads.files, 'REVIEW_INPUT_SOURCE')
            raw = reads.read(path, reads.files[path][2])
            common.require(len(raw) == binding['bytes'] and digest(raw) == binding['sha256'], 'REVIEW_INPUT_BYTES')
            _write_original(parts/name, raw, reads.files[path][2])


def _publish(parent, request, documents, *, project_root=None, reads=None):
    common.require(set(documents) == set(LIMITS), 'REVIEW_PACKET_MEMBERS')
    raw = {name:canonical(value) for name,value in documents.items()}
    common.require(all(len(raw[name]) <= LIMITS[name] for name in raw)
        and sum(map(len, raw.values())) <= 64_000_000, 'REVIEW_PACKET_BYTES')
    request = {**request, 'files_sha256':{name:digest(value) for name,value in raw.items()}}
    inputs = documents['project_cut']['schema'] == gold_native_source.CUT_SCHEMA
    new = inputs or documents['project_cut']['schema'] == 'QUANTOS_PROJECT_RESEARCH_CUT_V2'
    common.require(request['schema'] == (SOURCE_REQUEST_SCHEMA if inputs else CHUNKED_REQUEST_SCHEMA if new else REQUEST_SCHEMA),
        'REVIEW_REQUEST_CUT_VERSION')
    if new:
        common.require(project_root is not None and reads is not None, 'REVIEW_RESULT_SOURCE')
        materials = result_material(documents['project_cut'])
        request['result_material_sha256'] = digest(materials)
    if inputs:
        originals = input_material(documents['project_cut'])
        request['input_material_sha256'] = digest(originals)
    request_id = digest(request)
    request['review_request_id'] = request_id
    canonical(dict(request=request, **documents))  # Whole export has the same finite JSON bound.
    directory = _directory(parent, request_id)
    with common._locked(directory):
        common._mkdir(directory)
        common.require(set(p.name for p in directory.iterdir()) <=
            {name+'.json' for name in (*LIMITS, 'request')} | ({'results'} if new else set())
            | ({'inputs'} if inputs else set()),
            'REVIEW_PACKET_MEMBERS')
        if new:
            _publish_results(directory, materials, project_root=project_root, reads=reads)
        if inputs:
            _publish_inputs(directory, originals, project_root=project_root, reads=reads)
        # Publish the immutable ready marker last. Partial attempts remain visible.
        for name, value in {**raw, 'request':canonical(request)}.items():
            if name == 'request' and reads is not None:
                reads.recheck()
            _write_original(directory/(name+'.json'), value, LIMITS.get(name, 65_536))
        common._sync(directory)
    return dict(request=request, **documents)


def read_request(request_id):
    _, database = producer.workspace_paths()
    directory = _directory(database.parent/'review_requests', request_id)
    return read_packet(directory, request_id=request_id)


def _report(packet, *, assessment=None, review_id=None):
    facts = packet['projection'] if assessment is None else assessment['projection']
    return contract.validate_report(dict(schema=contract.SCHEMA, status='TECHNICAL_VALIDATION_ONLY',
        phase='REQUEST_PREPARED' if assessment is None else 'REVIEW_RECORDED',
        review_request_id=packet['request']['review_request_id'], native_invocation_id=packet['subject']['invocation_id'],
        attempt_count=packet['project_cut']['attempt_count'],
        statistical_decision=facts['statistical_review']['statistical_decision'],
        authentication='UNSIGNED_REQUEST' if assessment is None else assessment['authentication'],
        qualification='NOT_REVIEWED' if assessment is None else assessment['qualification'], review_id=review_id,
        reasons=facts['limitations'] if assessment is None else assessment['reasons'],
        oos_status=(facts.get('evaluation_core') or {}).get('oos_status', 'UNKNOWN'),
        risk_status=(facts.get('risk_replay') or {}).get('status'), checked_at_ms=_now() if assessment is None else assessment['checked_at_ms'],
        economic_authority=False, promotion_approved=False, submission_enabled=False))


def prepare_review_request(params):
    invocation = contract.validate_params('registered_review_request', params)['native_invocation_id']
    project, database = producer.workspace_paths()
    with producer._registry(database):
        parent = common._mkdir(database.parent/'review_requests')
        common._mkdir(database.parent/'signed_reviews')
        reads = _ProjectReads()
        with ProjectResearchEvidenceReader(project, registry_database=database)._evidence(reads) as (cut, reports):
            packet = _prepare_packet(parent, cut, reports, reads, project, database, invocation)
    return _report(packet)


def _prepare_packet(parent, cut, reports, reads, project, database, invocation):
    common.require(invocation in cut['runs'], 'REVIEW_RUN_MISSING')
    plan = cut['runs'][invocation]['plan']
    gold = plan['schema'] in common.contract.FORWARD_GOLD_SCHEMAS
    gold_backtest = plan['schema'] in common.contract.EVALUATED_SOURCE_REANALYSIS_SCHEMAS
    expected_producer = ('REGISTERED_GOLD_REANALYSIS_RUNNER_V1' if gold_backtest else
        'REGISTERED_GOLD_COLLECTOR_V1' if gold else 'REGISTERED_TECHNICAL_RUNNER_V1')
    common.require((gold or gold_backtest or plan['schema'] == common.contract.RISK_EVALUATED_SCHEMA)
        and plan['search']['source'] == expected_producer
        and canonical(plan['source_identity']) == canonical(source_identity()), 'REVIEW_PRODUCER_SOURCE')
    if gold:
        policy = plan['forward_design']
        common.require(policy['schema'] in FULL_REVIEW_SCHEMAS, 'GOLD_FULL_POLICY_REQUIRED')
    else:
        policy_hash = plan['evaluation_core']['selection']['validation_policy_sha256']
        policies = database.parent/'statistical_policies'
        reads.directory(policies)
        policy = decode(reads.read(policies/(policy_hash+'.json'), 65_536))
        common.require(digest(policy) == policy_hash, 'REVIEW_POLICY')
        if gold_backtest:
            from quantos.contracts.gold_reanalysis_statistics import bind_core
            bind_core(policy, plan['evaluation_core'])
    cohort = _cohort(cut, policy, invocation, reports=reports)
    common.require(cohort['selected_attempt'] is not None, 'REVIEW_PREDECLARED_SELECTION_REQUIRED')
    evidence = statistical.prepare(project_cut=cut, policy=policy, cohort=cohort,
        as_of_ms=cohort['frozen_at_ms'], reports=reports)
    common.require(cohort['frozen_at_ms'] <= _now(), 'REVIEW_FUTURE_FACT')
    subject = authority.project_subject(cut, invocation_id=invocation, native_trial_id=cohort['selected_attempt']['native_trial_id'])
    manifest, files = _source_files(reads, plan['source_identity'])
    projection = authority.project_facts(cut, subject, statistical_evidence=evidence,
        as_of_ms=_now(), reports=reports)
    cut['read_identity_sha256'] = reads.recheck()
    common.require(canonical(plan['source_identity']) == canonical(source_identity()), 'SOURCE_CHANGED')
    return _publish(parent, dict(schema=SOURCE_REQUEST_SCHEMA if cut['schema'] == gold_native_source.CUT_SCHEMA
        else CHUNKED_REQUEST_SCHEMA if cut['schema'] == 'QUANTOS_PROJECT_RESEARCH_CUT_V2'
        else REQUEST_SCHEMA, native_invocation_id=invocation,
        project_cut_sha256=cut['cut_sha256'], subject_sha256=digest(subject), created_at_ms=_now(),
        economic_authority=False, submission_enabled=False),
        dict(subject=subject, project_cut=cut, source_manifest=manifest, source_files=files,
            statistical_evidence=evidence, projection=projection), project_root=project, reads=reads)


def _reader(packet):
    names = ('QUANTOS_PROJECT_REVIEW_TRUST_FILE', 'QUANTOS_PROJECT_REVIEW_TRUST_SHA256',
        'QUANTOS_PROJECT_REVIEW_MIN_GENERATION')
    values = [os.environ.get(name) for name in names]
    common.require(all(values), 'TRUST_NOT_CONFIGURED')
    trust_file, pin, generation = values
    common.require(common.contract._hash(pin) and generation.isascii() and generation.isdecimal()
        and 1 <= int(generation) <= 2**63-1, 'REVIEW_TRUST_CONFIGURATION')
    trust = _path(trust_file)
    trust_reads = _ProjectReads()
    trust_reads.directory(trust.parent)
    common.require(digest(trust_reads.read(trust, 65_536)) == pin, 'TRUST_PIN')
    trust_reads.recheck()
    project, database = producer.workspace_paths()
    directory = response_directory(packet['request']['review_request_id'])
    for name in ('subject', 'source_manifest', 'statistical_evidence'):
        common.require(common._read(directory/(name+'.json'), maximum=LIMITS[name]) == canonical(packet[name]),
            'REVIEW_REPLY_CHANGED')
    producer_id = packet['project_cut']['runs'][packet['subject']['invocation_id']]['plan']['search']['source']
    common.require(producer_id in ('REGISTERED_TECHNICAL_RUNNER_V1', 'REGISTERED_GOLD_COLLECTOR_V1',
        'REGISTERED_GOLD_REANALYSIS_RUNNER_V1'), 'REVIEW_PRODUCER_SOURCE')
    return ProjectResearchEvidenceSnapshotReader(directory, project_root=project, registry_database=database,
        source_directory=ROOT, source_commit=source_identity()['commit'], producer_id=producer_id,
        trust_registry=trust, expected_registry_sha256=pin, minimum_generation=int(generation))


def consume_review(params):
    request_id = contract.validate_params('registered_review', params)['review_request_id']
    packet = read_request(request_id)
    reader = _reader(packet)
    _, database = producer.workspace_paths()
    with producer._registry(database) as registry:
        common.require(registry._store._schema_version() == 5, 'REVIEW_SCHEMA_MAINTENANCE_REQUIRED')
        reader_argument = ({'read_snapshot_context':reader.evidence_retained}
            if packet['project_cut']['schema'] in ('QUANTOS_PROJECT_RESEARCH_CUT_V2', gold_native_source.CUT_SCHEMA)
            else {'read_snapshot':reader.read_retained})
        row = registry.record_project_research_evidence(reviewed_cut=packet['project_cut'],
            **reader_argument, expected_subject=packet['subject'], clock_ms=_now)
        # This is the exact assessment retained in the original transaction.
        artifact = decode(row['evaluation_json'].encode('utf-8'))
        assessment = artifact['assessment']
    return _report(packet, assessment=assessment, review_id=row['run_id'])


def recover_review(params):
    contract.validate_params('registered_review_recover', params)
    # The original transaction owns idempotence, history and current-cut checks.
    # No second receipt store or signer is introduced for a lost queue result.
    return consume_review(params)
