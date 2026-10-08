"""Native full reports must survive the unsigned review transport intact."""
import json
import io
import os
import shutil
import tarfile
import time

import pytest

from quantos.contracts.research_evidence_json import canonical, digest
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_gold_chunked_result import storage


@pytest.fixture
def exported(registered_case, monkeypatch, request):
    from tests.research.test_gold_segmented_registration import request as collection_request
    from tools.gold_bar_research import read_arguments
    from tools.perpetual_replay_research import source_identity
    from tools import registered_research_job as producer
    from tools import registered_research_review_job as review

    f = registered_case
    source = source_identity()
    source_variant = getattr(request, 'param', '') or ''
    root = collection_request(f['root'], monkeypatch, {'release_identity':source}, declared=True, funding=True,
                              long_declared=source_variant in ('source-long-declared','source-risk-declared','source-risk-budgeted','source-net-risk-budgeted','source-net-market-budgeted'))
    f['arguments'] = read_arguments(root, original_source=source)
    f['clock'][0] = time.time_ns() // 1_000_000
    design = json.loads((root/'plan.json').read_bytes())['forward_design']
    if source_variant in ('source-net-risk-budgeted','source-net-market-budgeted'):
        portfolio_plan = dict(quantities={n:'0.1' for n in ('channel','trend','reversion')}, max_notional='100')
        design['configuration']['portfolio_plan'] = portfolio_plan
        f['arguments']['portfolio_plan'] = dict(quantities=dict(portfolio_plan['quantities']), max_notional='100')
    if source_variant in ('source-risk-declared','source-risk-budgeted','source-net-risk-budgeted','source-net-market-budgeted'):
        from quantos.contracts.gold_forward_evaluation_design import declare
        from tests.validation.test_gold_research_risk_policy import policy
        from tests.validation.test_gold_market_states import policy as market_policy
        risk_policy=policy(start=design['metrics']['start_ms'],end=design['metrics']['end_ms'],
            frequency=design['metrics']['frequency_ms'],declared=design['declared_at_ms'])
        design=declare(program_id=design['program_id'],attempt=design['attempt'],
            source_identity=source,configuration=design['configuration'],
            first_close_ms=design['window']['closed_bars'][0],window_bars=len(design['window']['closed_bars']),
            declared_at_ms=design['declared_at_ms'],long_window=True,
            selected_candidate='net_portfolio' if source_variant.startswith('source-net-') else design['selection']['candidate_id'],
            proposal_sha256=design['selection']['proposal_sha256'],research_risk_policy=risk_policy,
            market_state_policy=market_policy(dict(start_ms=design['metrics']['start_ms'],
                end_ms=design['metrics']['end_ms'],frequency_ms=design['metrics']['frequency_ms']),
                declared=design['declared_at_ms'])
                if source_variant == 'source-net-market-budgeted' else None)
    monkeypatch.setattr(producer, 'workspace_paths', lambda: (f['root']/'project', f['root']/'registry.db'))
    source_mode = source_variant.startswith('source')
    if source_mode:
        from tools import gold_chunked_source as originals
        from quantos.contracts.gold_segmented_input import documents
        parts = list(documents(f['arguments']))
        parts[0] = {k:v for k,v in parts[0].items() if k not in ('input_schema', 'continuation_segments')}
        originals.freeze(parts, f['root']/'frozen-input', maximum_bytes=32_000_000)
        for path in f['root'].glob('registry.db*'):
            path.chmod(0o600)
    plans = []
    for name, invocation in (('one', '1'*64), ('two', 'e'*64)):
        options = dict(project_root=f['root']/'project', search_id=name,
            producer_id='REGISTERED_GOLD_COLLECTOR_V1', actor_id='AUTOMATED_TEST',
            dataset_version='native-xau', registered_at_ms=f['clock'][0], forward_design=design,
            invocation_id=invocation, result_storage=storage())
        if source_mode:
            from tools.registered_gold_chunked_job import prepare_source_run
            source_storage = dict(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V1', maximum_bytes=32_000_000,
                                  maximum_preparation_bytes=64_000_000)
            if source_variant in ('source-budgeted','source-risk-budgeted','source-net-risk-budgeted','source-net-market-budgeted'):
                source_storage.update(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2',
                    replay_budget=dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1',
                        maximum_environment_segments=2, maximum_elapsed_ms=30 * 86_400_000))
            plan = prepare_source_run(f['root']/'frozen-input',
                **{k:v for k,v in options.items() if k not in ('project_root', 'producer_id', 'actor_id')},
                history_storage=dict(schema='QUANTOS_GOLD_HISTORY_STORAGE_V1', maximum_bytes=256_000_000),
                source_storage=source_storage)
            arguments = f['run'].gold._decode(json.loads((f['root']/'project/runs'/invocation/'input.json').read_bytes()))
        else:
            plan = f['run'].prepare_gold(f['arguments'], **options)
            arguments = f['arguments']
        f['directories'][name] = f['run'].run_directory(f['root']/'project', plan)
        f['run'].run_gold(arguments, destination=f['directories'][name], registry=f['registry'],
            clock_ms=lambda: f['clock'][0])
        plans.append(plan)
    if source_variant in ('source-unstarted', 'source-partial'):
        from tools.registered_gold_chunked_job import prepare_source_run
        put = f['run']._put
        def interrupt(path, *args, **kwargs):
            if path.parent.name == 'blobs':
                raise RuntimeError('original copy never started')
            return put(path, *args, **kwargs)
        if source_variant == 'source-partial':
            monkeypatch.setattr(f['run'], '_put', interrupt)
        try:
            prepare_source_run(f['root']/'frozen-input', search_id='unstarted', dataset_version='native-xau',
                registered_at_ms=f['clock'][0], invocation_id='d'*64, forward_design=design,
                result_storage=storage(),
                history_storage=dict(schema='QUANTOS_GOLD_HISTORY_STORAGE_V1', maximum_bytes=256_000_000),
                source_storage=dict(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V1', maximum_bytes=32_000_000,
                    maximum_preparation_bytes=64_000_000))
        except RuntimeError:
            assert source_variant == 'source-partial'
        finally:
            monkeypatch.setattr(f['run'], '_put', put)
    before = {str(p):p.read_bytes() for p in (f['root']/'project').rglob('*') if p.is_file()}
    for path in f['root'].glob('registry.db*'):
        path.chmod(0o600)
    report = review.prepare_review_request({'native_invocation_id':plans[0]['invocation_id']})
    packet = review.read_request(report['review_request_id'])
    directory = f['root']/'review_requests'/report['review_request_id']
    yield f, plans, report, packet, directory
    assert {str(p):p.read_bytes() for p in (f['root']/'project').rglob('*') if p.is_file()} == before


def test_native_unsigned_request_exports_all_complete_original_reports(exported):
    f, plans, report, packet, directory = exported
    assert packet['request']['schema'] == 'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V2'
    assert packet['project_cut']['attempt_count'] == 6
    assert set(p.name for p in (directory/'results').iterdir()) == {p['invocation_id'] for p in plans}
    materials = {}
    for plan, name in zip(plans, ('one', 'two'), strict=True):
        original = f['directories'][name]/'archive/report-parts'
        target = directory/'results'/plan['invocation_id']
        actual = {p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()}
        assert actual == {p.relative_to(original).as_posix():p.read_bytes()
            for p in original.rglob('*') if p.is_file()}
        materials[plan['invocation_id']] = packet['project_cut']['runs'][plan['invocation_id']]['result_material']
    assert packet['request']['result_material_sha256'] == digest(materials)


@pytest.mark.parametrize('exported', ['source-long-declared'], indirect=True)
def test_explicit_long_declaration_reaches_original_source_and_complete_unsigned_review(exported):
    from copy import deepcopy
    from quantos.contracts import research_statistical_review as contract

    _, plans, report, packet, _ = exported
    assert all(plan['forward_design']['schema'] == 'QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V4'
               for plan in plans)
    original = packet['statistical_evidence']['report']
    assert original['schema'] == contract.LONG_GOLD_REPORT_SCHEMA
    assert contract.validate_report(original) == original
    assert original['population']['attempt_count'] == 6
    assert len(original['comparison']['included_attempts']) == 6
    assert original['statistical_decision'] != 'PASS'
    assert report['economic_authority'] is report['promotion_approved'] is report['submission_enabled'] is False
    changed = deepcopy(original)
    changed['computation_budget']['maximum_points'] += 1
    with pytest.raises(ValueError):
        contract.validate_report(changed)
    changed = deepcopy(original)
    changed['schema'] = contract.SCOPED_GOLD_REPORT_SCHEMA
    with pytest.raises(ValueError):
        contract.validate_report(changed)
    assert report['phase'] == 'REQUEST_PREPARED'
    assert report['economic_authority'] is report['promotion_approved'] is report['submission_enabled'] is False
    assert len(canonical(packet)) < 64_000_000


def test_exported_reports_recompute_the_original_statistics_and_expire_on_close(exported):
    from services.research.project_research_review_request import open_request
    from services.validation import project_research_statistical_evidence as statistical
    _, plans, _, packet, directory = exported
    with open_request(directory, request_id=packet['request']['review_request_id']) as (material, reports):
        assert set(reports) == {p['invocation_id'] for p in plans}
        actual = statistical.validate(material['statistical_evidence'], project_cut=material['project_cut'],
            reports=reports)
        assert actual == packet['statistical_evidence']
        retained = reports[plans[0]['invocation_id']]
        assert len(retained['trials']) == 3
    with pytest.raises(ValueError, match='CLOSED'):
        retained['trials']


@pytest.mark.parametrize('damage', ['missing-run', 'extra-run', 'missing-blob', 'extra-blob',
    'bytes', 'symlink', 'hardlink', 'manifest'])
def test_damage_to_any_exported_report_blocks_the_whole_request(exported, damage):
    from services.research.project_research_review_request import read_request
    _, plans, _, packet, directory = exported
    parts = directory/'results'/plans[1]['invocation_id']
    blob = next((parts/'blobs').iterdir())
    if damage == 'missing-run':
        shutil.rmtree(parts)
    elif damage == 'extra-run':
        (directory/'results'/('b'*64)).mkdir(mode=0o700)
    elif damage == 'missing-blob':
        blob.unlink()
    elif damage == 'extra-blob':
        extra = parts/'blobs'/('f'*64)
        extra.write_bytes(b'unknown')
        extra.chmod(0o600)
    elif damage == 'bytes':
        blob.write_bytes(blob.read_bytes()+b' ')
    elif damage == 'manifest':
        path = parts/'manifest.json'
        path.write_bytes(path.read_bytes()+b' ')
    else:
        copied = directory.parent/'copy'
        copied.write_bytes(blob.read_bytes())
        copied.chmod(0o600)
        blob.unlink()
        if damage == 'symlink':
            blob.symlink_to(copied)
        else:
            os.link(copied, blob)
    with pytest.raises(ValueError):
        read_request(directory, request_id=packet['request']['review_request_id'])


@pytest.mark.parametrize('member', ['report', 'metadata'])
def test_same_byte_rewrite_during_review_is_rejected(exported, member):
    from services.research.project_research_review_request import open_request
    _, plans, _, packet, directory = exported
    with pytest.raises(ValueError, match='CHANGED'):
        with open_request(directory, request_id=packet['request']['review_request_id']):
            path = (next((directory/'results'/plans[1]['invocation_id']/'blobs').iterdir())
                if member == 'report' else directory/'source_files.json')
            path.write_bytes(path.read_bytes())


def test_interrupted_export_has_no_ready_marker_and_retry_keeps_the_original_identity(exported, monkeypatch):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader, _ProjectReads
    from tools import registered_research_review_job as review
    f, _, _, packet, _ = exported
    parent = f['root']/'interrupted'
    parent.mkdir(mode=0o700)
    documents = {name:packet[name] for name in review.LIMITS}
    request = {k:v for k,v in packet['request'].items() if k not in ('review_request_id', 'files_sha256')}
    writer = review._write_original
    calls = []
    def interrupt(path, raw, maximum):
        if path.parent.name == 'blobs':
            calls.append(path)
            if len(calls) == 2:
                raise RuntimeError('transport interrupted')
        return writer(path, raw, maximum)
    reads = _ProjectReads()
    reader = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')
    with reader._evidence(reads):
        monkeypatch.setattr(review, '_write_original', interrupt)
        with pytest.raises(RuntimeError, match='interrupted'):
            review._publish(parent, request, documents, project_root=f['root']/'project', reads=reads)
        target = parent/packet['request']['review_request_id']
        assert not (target/'request.json').exists()
        retained = calls[0].read_bytes()
        monkeypatch.setattr(review, '_write_original', writer)
        result = review._publish(parent, request, documents, project_root=f['root']/'project', reads=reads)
        assert result['request']['review_request_id'] == packet['request']['review_request_id']
        assert calls[0].read_bytes() == retained
        assert review.read_packet(target, request_id=result['request']['review_request_id']) == result


def test_full_archive_contains_every_original_file_not_only_metadata(exported):
    from services.research.project_research_review_request import iter_request_archive
    _, _, _, packet, directory = exported
    raw = b''.join(iter_request_archive(directory, request_id=packet['request']['review_request_id']))
    originals = {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        files = [m for m in archive.getmembers() if m.isfile()]
        directories = [m for m in archive.getmembers() if m.isdir()]
        assert len(files)+len(directories) == len(archive.getmembers())
        assert all(m.mode == 0o600 for m in files) and all(m.mode == 0o700 for m in directories)
        assert {m.name for m in directories} == {p.relative_to(directory).as_posix()
            for p in directory.rglob('*') if p.is_dir()}
        assert {m.name:archive.extractfile(m).read() for m in files} == originals


def test_archive_http_exports_all_originals_and_refuses_a_corrupt_nonselected_run(exported, monkeypatch):
    from fastapi.testclient import TestClient
    from services.console.main import app
    from services.console.api import research_run_views as views
    f, plans, _, packet, directory = exported
    state = f['root']/'console-state'
    target = state/'data/cache/registered_research/review_requests'/directory.name
    target.parent.mkdir(parents=True, mode=0o700)
    shutil.copytree(directory, target)
    monkeypatch.setattr(views, 'runtime_state_root', lambda: str(state))
    client = TestClient(app)
    params = {'review_request_id':packet['request']['review_request_id']}
    url = '/api/v1/console/registered-review-archive'
    response = client.get(url, params=params)
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/x-tar'
    assert response.headers['x-quantos-read-only'] == 'true'
    assert response.headers['x-quantos-authoritative'] == 'false'
    assert response.headers['x-quantos-review-request'] == directory.name
    originals = {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    with tarfile.open(fileobj=io.BytesIO(response.content), mode='r:') as archive:
        assert {m.name:archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()} == originals
    blob = next((target/'results'/plans[1]['invocation_id']/'blobs').iterdir())
    blob.write_bytes(blob.read_bytes()+b' ')
    assert client.get(url, params=params).status_code == 409
    metadata = client.get('/api/v1/console/registered-review-request', params=params).json()
    assert metadata['review_request'] is None and metadata['material_status'] == 'INVALID'


@pytest.mark.parametrize('exported', ['', 'source-risk-declared', 'source-net-risk-budgeted','source-net-market-budgeted'], indirect=True)
def test_exported_originals_support_signed_statistical_replay_without_native_paths(exported):
    from tests.research.test_registered_research_review_job import independent_response
    from services.research.project_research_review_request import open_request
    from services.validation import project_research_evidence_authority as authority
    f, _, _, packet, directory = exported
    material = f['root']/'synthetic-independent-reply'
    trust = f['root']/'synthetic-governance/trust.json'
    independent_response(packet, material, trust)
    registry = json.loads(trust.read_bytes())
    registry['producer_ids'] = ['REGISTERED_GOLD_COLLECTOR_V1']
    registry['keys']['fixture']['evidence_kinds'] = ['BACKTEST', 'FORWARD']
    snapshot = dict(schema=authority.STATISTICAL_SNAPSHOT_SCHEMA,
        **{k:packet[k] for k in ('subject', 'project_cut', 'source_manifest', 'statistical_evidence')},
        source_envelope=json.loads((material/'source_envelope.json').read_bytes()),
        result_envelope=json.loads((material/'result_envelope.json').read_bytes()),
        trust_registry=registry, expected_registry_sha256=digest(registry),
        read_identity_sha256=digest('isolated complete transport test'))
    original = f['root']/'project'
    held = f['root']/'held-project'
    original.rename(held)
    try:
        with open_request(directory, request_id=directory.name) as (_, reports):
            verified = authority.verify_project_evidence(snapshot, now_ms=f['clock'][0], reports=reports)
            assert verified['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
            assert verified['qualification'] == 'INSUFFICIENT'
            if verified['projection']['schema'] in ('QUANTOS_PROJECT_RESEARCH_FACTS_V8','QUANTOS_PROJECT_RESEARCH_FACTS_V9','QUANTOS_PROJECT_RESEARCH_FACTS_V10'):
                projected=verified['projection']['execution_risk_review']['account_risk_inputs']
                assert projected['source_groups']['risk_config']['status']=='AVAILABLE'
                if verified['projection']['schema'] in ('QUANTOS_PROJECT_RESEARCH_FACTS_V9','QUANTOS_PROJECT_RESEARCH_FACTS_V10'):
                    assert projected['schema']==('QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V5' if 'market_states' in projected
                        else 'QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V4')
                    assert projected['net_attribution']['cash_conservation_residual']=='0'
                    assert projected['net_attribution']['independent_strategy_pnl'] is False
                    assert projected['net_attribution']['status']=='UNAVAILABLE_SAMPLE_COVERAGE'
                if verified['projection']['schema']=='QUANTOS_PROJECT_RESEARCH_FACTS_V10':
                    assert projected['market_states']['status']=='UNAVAILABLE_ACCOUNT_SERIES'
                    assert projected['market_states']['label_values_sha256'] is None
                    assert projected['source_groups']['regime_labels']['status']=='UNAVAILABLE_ACCOUNT_SERIES'
                assert projected['risk_policy']['preregistration_time_authority'] is False
                assert projected['full_research_risk_ready'] is False
            assert 'STATISTICAL_REVIEW_REQUIRES_COMPLETE_PROJECT_CUT' not in verified['reasons']
            assert verified['promotion_approved'] is verified['submission_enabled'] is False
            with pytest.raises(ValueError, match='MATERIAL_REQUIRED'):
                authority.verify_project_evidence(snapshot, now_ms=f['clock'][0])
    finally:
        held.rename(original)


@pytest.mark.parametrize('exported', ['source-long-declared'], indirect=True)
def test_long_declared_transport_recomputes_with_synthetic_signatures_and_keeps_qualification_insufficient(exported):
    test_exported_originals_support_signed_statistical_replay_without_native_paths(exported)


def test_source_rewrite_before_ready_publication_leaves_no_ready_marker(exported, monkeypatch):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader, _ProjectReads
    from tools import registered_research_review_job as review
    f, _, _, packet, _ = exported
    parent = f['root']/'source-changed'
    parent.mkdir(mode=0o700)
    documents = {name:packet[name] for name in review.LIMITS}
    request = {k:v for k,v in packet['request'].items() if k not in ('review_request_id', 'files_sha256')}
    blob = next((f['directories']['two']/'archive/report-parts/blobs').iterdir())
    writer = review._write_original
    def rewrite(path, raw, maximum):
        writer(path, raw, maximum)
        if path.name == 'projection.json':
            blob.write_bytes(blob.read_bytes())
    monkeypatch.setattr(review, '_write_original', rewrite)
    reader = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')
    reads = _ProjectReads()
    with pytest.raises(ValueError, match='CHANGED'):
        with reader._evidence(reads):
            review._publish(parent, request, documents, project_root=f['root']/'project',
                reads=reads)
    assert not (parent/packet['request']['review_request_id']/'request.json').exists()
