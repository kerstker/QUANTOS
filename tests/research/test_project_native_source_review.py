"""Full native input originals must survive review and signed cold consumption."""
import io
import os
import shutil
import tarfile

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.research.test_project_chunked_review_request import exported as exported

pytestmark = pytest.mark.parametrize('exported', ['source'], indirect=True)


def test_source_review_exports_and_replays_all_originals_without_native_paths(exported):
    from services.research.project_research_review_request import open_request, iter_request_archive
    from services.validation import project_research_statistical_evidence as statistical
    f, plans, _, packet, directory = exported
    assert packet['request']['schema'] == 'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V3'
    for plan in plans:
        invocation = plan['invocation_id']
        original = f['root']/'project/runs'/invocation/'input-parts'
        copied = directory/'inputs'/invocation/'input-parts'
        assert {p.relative_to(copied).as_posix(): p.read_bytes() for p in copied.rglob('*') if p.is_file()} == {
            p.relative_to(original).as_posix(): p.read_bytes() for p in original.rglob('*') if p.is_file()}
    original = f['root']/'project'
    held = f['root']/'held-project'
    original.rename(held)
    try:
        with open_request(directory, request_id=directory.name) as (documents, reports):
            assert statistical.validate(documents['statistical_evidence'], project_cut=documents['project_cut'],
                reports=reports) == packet['statistical_evidence']
            inputs = reports.source_inputs[plans[1]['invocation_id']]
            assert len(inputs) == 2
        with pytest.raises(ValueError, match='CONTEXT_CLOSED'):
            inputs[0]
        raw = b''.join(iter_request_archive(directory, request_id=directory.name))
        originals = {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob('*') if p.is_file()}
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            assert {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()} == originals
    finally:
        held.rename(original)


@pytest.mark.parametrize('damage', ['missing-run', 'extra-run', 'missing-blob', 'extra-blob',
    'bytes', 'symlink', 'hardlink', 'manifest'])
def test_nonselected_input_damage_blocks_the_entire_review(exported, damage):
    from services.research.project_research_review_request import read_request
    _, plans, _, _, directory = exported
    parts = directory/'inputs'/plans[1]['invocation_id']/'input-parts'
    blob = next((parts/'blobs').iterdir())
    if damage == 'missing-run':
        shutil.rmtree(parts.parent)
    elif damage == 'extra-run':
        (directory/'inputs'/('b'*64)).mkdir(mode=0o700)
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
        copy = directory.parent/'copy'
        copy.write_bytes(blob.read_bytes())
        copy.chmod(0o600)
        blob.unlink()
        if damage == 'symlink':
            blob.symlink_to(copy)
        else:
            os.link(copy, blob)
    with pytest.raises(ValueError):
        read_request(directory, request_id=directory.name)


def test_review_needs_physical_source_views_and_rejects_a_rewrite_during_consumption(exported):
    from services.research.project_research_review_request import open_request
    from services.validation import project_research_statistical_evidence as statistical
    _, plans, _, _, directory = exported
    with pytest.raises(ValueError, match='CHANGED'):
        with open_request(directory, request_id=directory.name) as (documents, reports):
            with pytest.raises(ValueError, match='SOURCE_MATERIAL_REQUIRED'):
                statistical.validate(documents['statistical_evidence'], project_cut=documents['project_cut'],
                    reports=dict(reports))
            inputs = reports.source_inputs[plans[1]['invocation_id']]
            assert inputs[1]['observed_funding_records']
            path = next((directory/'inputs'/plans[1]['invocation_id']/'input-parts/blobs').iterdir())
            path.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match='CONTEXT_CLOSED'):
        inputs[1]


def test_interrupted_input_export_has_no_ready_marker_and_retries_exactly(exported, monkeypatch):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader, _ProjectReads
    from tools import registered_research_review_job as review
    f, _, _, packet, _ = exported
    parent = f['root']/'interrupted-input-export'
    parent.mkdir(mode=0o700)
    documents = {name:packet[name] for name in review.LIMITS}
    request = {k:v for k,v in packet['request'].items() if k not in ('review_request_id', 'files_sha256')}
    writer = review._write_original
    calls = []
    def interrupt(path, raw, maximum):
        if 'inputs' in path.parts and path.parent.name == 'blobs':
            calls.append(path)
            if len(calls) == 2:
                raise RuntimeError('original input export interrupted')
        return writer(path, raw, maximum)
    reads = _ProjectReads()
    reader = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')
    with reader._evidence(reads):
        monkeypatch.setattr(review, '_write_original', interrupt)
        with pytest.raises(RuntimeError, match='export interrupted'):
            review._publish(parent, request, documents, project_root=f['root']/'project', reads=reads)
        target = parent/packet['request']['review_request_id']
        assert not (target/'request.json').exists()
        retained = calls[0].read_bytes()
        monkeypatch.setattr(review, '_write_original', writer)
        result = review._publish(parent, request, documents, project_root=f['root']/'project', reads=reads)
        directory_id = packet['request']['review_request_id']
        assert result['request']['review_request_id'] == directory_id
        assert calls[0].read_bytes() == retained
        assert review.read_packet(target, request_id=directory_id) == result


def test_historical_report_filter_keeps_only_its_original_input_views(exported):
    from services.research.project_research_review_request import open_request
    from services.validation.project_research_evidence_artifact import retained_reports
    from services.validation import project_research_statistical_evidence as statistical
    _, plans, _, packet, directory = exported
    with open_request(directory, request_id=directory.name) as (_, reports):
        keys = {plan['invocation_id'] for plan in plans}
        first = plans[0]['invocation_id']
        reports['outside-historical-cut'] = reports[first]
        reports.source_inputs['outside-historical-cut'] = reports.source_inputs[first]
        historical = retained_reports(packet['project_cut'], reports)
        assert set(historical) == set(historical.source_inputs) == keys
        assert historical.source_inputs[first] is reports.source_inputs[first]
        assert statistical.validate(packet['statistical_evidence'], project_cut=packet['project_cut'],
            reports=historical) == packet['statistical_evidence']
    with pytest.raises(ValueError, match='CONTEXT_CLOSED'):
        len(historical.source_inputs[first])
