"""Bounded read-only export of an unsigned, fixed-path project review request."""
import base64
import hashlib
import tempfile
import tarfile
from contextlib import contextmanager, ExitStack
from pathlib import Path, PurePosixPath

from quantos.contracts import project_chunked_results as chunked
from quantos.contracts import registered_research_run as native
from quantos.contracts import gold_native_source as source
from quantos.contracts.research_evidence_json import canonical, decode, digest
from services.research.project_research_evidence_reader import _ProjectReads
from services.research.research_evidence_reader import _path

LIMITS = dict(subject=65_536, project_cut=32_000_000, source_manifest=16_000_000,
    statistical_evidence=16_000_000, projection=16_000_000, source_files=32_000_000)
REQUEST_SCHEMA = 'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V1'
CHUNKED_REQUEST_SCHEMA = 'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V2'
SOURCE_REQUEST_SCHEMA = 'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V3'
CHUNKED_REQUEST_SCHEMAS = (CHUNKED_REQUEST_SCHEMA, SOURCE_REQUEST_SCHEMA)
REQUEST_FIELDS = frozenset(('schema', 'native_invocation_id', 'project_cut_sha256', 'subject_sha256',
    'created_at_ms', 'economic_authority', 'submission_enabled', 'files_sha256', 'review_request_id'))


def _source_files(documents):
    subject, cut = documents['subject'], documents['project_cut']
    identity = cut['runs'][subject['invocation_id']]['plan']['source_identity']
    manifest, files = documents['source_manifest'], documents['source_files']
    native.require(type(manifest) is dict and set(manifest) == {'schema', 'source_identity_sha256', 'files'}
        and manifest['schema'] == 'QUANTOS_RESEARCH_SOURCE_MANIFEST_V1'
        and manifest['source_identity_sha256'] == digest(identity), 'REVIEW_SOURCE_MANIFEST')
    native.require(type(files) is dict and set(files) == {'schema', 'files'}
        and files['schema'] == 'QUANTOS_REVIEW_SOURCE_FILES_V1'
        and type(files['files']) is dict and type(manifest['files']) is dict
        and 0 < len(manifest['files']) <= 20_000, 'REVIEW_SOURCE_FILES')
    lock = 'requirements-ci-linux-aarch64.lock'
    native.require(set(files['files']) == set(manifest['files']) | {lock}, 'REVIEW_SOURCE_MEMBERS')
    fingerprint = hashlib.sha256()
    for name in sorted(files['files']):
        path = PurePosixPath(name)
        native.require(path.as_posix() == name and not path.is_absolute() and '..' not in path.parts
            and '\\' not in name and (name == lock or
                (path.parts[0] in ('services', 'quantos', 'tools') and name.endswith('.py'))), 'REVIEW_SOURCE_PATH')
        encoded = files['files'][name]
        native.require(type(encoded) is str and len(encoded) <= 32_000_000, 'REVIEW_SOURCE_BYTES')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError) as exc:
            raise ValueError('REVIEW_SOURCE_BASE64') from exc
        native.require(base64.b64encode(raw).decode('ascii') == encoded, 'REVIEW_SOURCE_BASE64')
        expected = identity['dependency_lock_sha256'] if name == lock else manifest['files'][name]
        native.require(digest(raw) == expected, 'REVIEW_SOURCE_HASH')
        if name != lock:
            fingerprint.update(name.encode() + b'\0' + raw + b'\0')
    native.require(fingerprint.hexdigest() == identity['python_source_sha256'], 'REVIEW_SOURCE_IDENTITY')


def result_material(cut):
    """All complete original reports, derived from the whole validated cut."""
    from services.validation.project_research_population import validate_project_cut
    validate_project_cut(cut)
    return {invocation:run['result_material'] for invocation, run in cut['runs'].items()
        if run['plan']['schema'] in native.CHUNKED_GOLD_SCHEMAS
        and run['result_material']['status'] == 'COMPLETE'}


def input_material(cut):
    """Whole native source population, including interrupted preparations."""
    from services.validation.project_research_population import validate_project_cut
    validate_project_cut(cut)
    return {invocation:run['input_material'] for invocation, run in cut['runs'].items()
        if run['plan']['schema'] in native.SOURCE_GOLD_SCHEMAS}


def _read_request(reads, directory, request_id):
    native.require(native._hash(request_id), 'REVIEW_REQUEST_ID')
    members = set(reads.directory(directory))
    request = decode(reads.read(directory/'request.json', 65_536))
    new = type(request) is dict and request.get('schema') in CHUNKED_REQUEST_SCHEMAS
    inputs = type(request) is dict and request.get('schema') == SOURCE_REQUEST_SCHEMA
    native.require(type(request) is dict and set(request) ==
        REQUEST_FIELDS | ({'result_material_sha256'} if new else set()) | ({'input_material_sha256'} if inputs else set()), 'REVIEW_REQUEST_FIELDS')
    native.require(members == {name+'.json' for name in (*LIMITS, 'request')} |
        ({'results'} if new else set()) | ({'inputs'} if inputs else set()), 'REVIEW_PACKET_MEMBERS')
    raw = {name:reads.read(directory/(name+'.json'), LIMITS.get(name, 65_536)) for name in (*LIMITS, 'request')}
    native.require(sum(map(len, raw.values())) <= 64_000_000, 'REVIEW_PACKET_BYTES')
    documents = {name:decode(value) for name,value in raw.items()}
    request = documents['request']
    native.require(request['schema'] == (SOURCE_REQUEST_SCHEMA if inputs else CHUNKED_REQUEST_SCHEMA if new else REQUEST_SCHEMA)
        and request['review_request_id'] == request_id
        and digest({k:v for k,v in request.items() if k != 'review_request_id'}) == request_id
        and request['economic_authority'] is request['submission_enabled'] is False
        and type(request['created_at_ms']) is int and 0 <= request['created_at_ms'] <= 2**63-1
        and type(request['files_sha256']) is dict and set(request['files_sha256']) == set(LIMITS),
        'REVIEW_REQUEST_BINDING')
    for name in LIMITS:
        native.require(digest(raw[name]) == request['files_sha256'][name], 'REVIEW_PACKET_CHANGED')
    native.require(request['native_invocation_id'] == documents['subject']['invocation_id']
        and request['project_cut_sha256'] == documents['project_cut']['cut_sha256']
        == documents['subject']['project_cut_sha256'] and request['subject_sha256'] == digest(documents['subject']),
        'REVIEW_REQUEST_SUBJECT')
    native.require(documents['project_cut']['schema'] ==
        (source.CUT_SCHEMA if inputs else chunked.CUT_SCHEMA if new else 'QUANTOS_PROJECT_RESEARCH_CUT_V1'),
        'REVIEW_REQUEST_CUT_VERSION')
    if new:
        native.require(request['result_material_sha256'] == digest(result_material(documents['project_cut'])),
            'REVIEW_RESULT_MATERIAL_BINDING')
    if inputs:
        native.require(request['input_material_sha256'] == digest(input_material(documents['project_cut'])),
            'REVIEW_INPUT_MATERIAL_BINDING')
    _source_files(documents)
    canonical(documents)  # The exported whole packet obeys the same JSON budget.
    return documents


@contextmanager
def _open_request(directory, *, request_id):
    """Hold every exported original and complete report through consumption.

    Directory is fixed by the application; the queue carries only request_id.
    Returned report views expire when this context exits.
    """
    from services.research.gold_chunked_result_reader import open_gold_result
    directory = _path(str(directory))
    reads = _ProjectReads()
    documents = _read_request(reads, directory, request_id)
    from services.research.gold_native_source_reader import SourceReports, SourceParts, capture_source
    reports = SourceReports()
    with ExitStack() as stack:
        cut = documents['project_cut']
        if documents['request']['schema'] == SOURCE_REQUEST_SCHEMA:
            materials = input_material(cut)
            root = directory/'inputs'
            native.require(set(reads.directory(root)) == set(materials), 'REVIEW_INPUT_POPULATION')
            for invocation, expected in sorted(materials.items()):
                parts = root/invocation
                native.require(set(reads.directory(parts)) == {source.DIRECTORY}, 'REVIEW_INPUT_MEMBERS')
                run = cut['runs'][invocation]
                original = run['originals'].get('input.json')
                document = None if original is None else decode(base64.b64decode(original['bytes_base64'], validate=True))
                material, manifest = capture_source(reads, parts, run['plan'], document)
                native.require(material == expected, 'REVIEW_INPUT_CHANGED')
                if manifest is not None:
                    native.require(manifest == base64.b64decode(run['originals'][source.MANIFEST]['bytes_base64'],
                        validate=True), 'REVIEW_INPUT_MANIFEST')
                if material['status'] == 'COMPLETE':
                    reports.source_inputs[invocation] = SourceParts(reports, reads, parts, material)
        if documents['request']['schema'] in CHUNKED_REQUEST_SCHEMAS:
            materials = result_material(cut)
            root = directory/'results'
            native.require(set(reads.directory(root)) == set(materials), 'REVIEW_RESULT_POPULATION')
            index_bytes = index_nodes = 0
            for invocation, material in sorted(materials.items()):
                parts = root/invocation
                native.require(set(reads.directory(parts)) == {'manifest.json', 'blobs'}, 'REVIEW_RESULT_MEMBERS')
                native.require(set(reads.directory(parts/'blobs')) ==
                    {name.split('/')[1] for name in material['files'] if name.startswith('blobs/')},
                    'REVIEW_RESULT_BLOBS')
                plan = cut['runs'][invocation]['plan']
                for name, binding in sorted(material['files'].items()):
                    raw = (reads.read_result(parts/name, plan['result_storage']['chunk_bytes'])
                        if name.startswith('blobs/') else reads.read(parts/name, chunked.gold.MAX_MANIFEST_BYTES))
                    native.require(len(raw) == binding['bytes'] and digest(raw) == binding['sha256'],
                        'REVIEW_RESULT_BYTES')
                temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix='quantos-review-result-'))
                report = stack.enter_context(open_gold_result(parts, Path(temporary)/'index',
                    expected_manifest_sha256=material['files']['manifest.json']['sha256'],
                    maximum_source_bytes=plan['result_storage']['maximum_bytes'],
                    maximum_index_bytes=min(plan['result_storage']['maximum_index_bytes'],
                        chunked.LIMITS['maximum_index_bytes']-index_bytes),
                    maximum_nodes=chunked.LIMITS['maximum_nodes']-index_nodes))
                index_bytes += report.index.path.stat().st_size
                index_nodes += report.index.nodes
                reports[invocation] = report
            reports.prepare_report_views(cut)
            chunked.full_report_population(cut, reports)
        try:
            yield documents, reports, reads
        finally:
            try:
                reads.recheck()
            finally:
                reports.close_inputs()


@contextmanager
def open_request(directory, *, request_id):
    """Complete immutable report views are valid only during this lease."""
    with _open_request(directory, request_id=request_id) as (documents, reports, _):
        yield documents, reports


def iter_request_archive(directory, *, request_id):
    """Stream all original files as a deterministic tar, with request last.

    No extraction, path selection or writing occurs. A complete footer is
    emitted only after a physical recheck of every source file.
    """
    directory = _path(str(directory))
    with _open_request(directory, request_id=request_id) as (documents, _, reads):
        names = [name+'.json' for name in sorted(LIMITS)]
        if documents['request']['schema'] in CHUNKED_REQUEST_SCHEMAS:
            names += ['results/'+invocation+'/'+name
                for invocation, material in sorted(result_material(documents['project_cut']).items())
                for name in sorted(material['files'])]
        if documents['request']['schema'] == SOURCE_REQUEST_SCHEMA:
            names += ['inputs/'+invocation+'/'+source.DIRECTORY+'/'+name
                for invocation, material in sorted(input_material(documents['project_cut']).items())
                for name in sorted(material['files'])]
        names.append('request.json')
        total = 0
        # Preserve private directories, including empty interrupted inputs.
        # File-only archives lose both those members and report custody modes
        # when restored under an ordinary umask.
        for path in sorted(reads.directories):
            if path == directory:
                continue
            reads.directory(path)
            member = tarfile.TarInfo(path.relative_to(directory).as_posix()+'/')
            member.type, member.mode, member.mtime = tarfile.DIRTYPE, 0o700, 0
            header = member.tobuf(format=tarfile.PAX_FORMAT)
            yield header
            total += len(header)
        for name in names:
            path = directory/name
            raw = reads.read(path, reads.files[path][2])
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(raw), 0o600, 0
            header = member.tobuf(format=tarfile.PAX_FORMAT)
            yield header
            for offset in range(0, len(raw), 1_048_576):
                yield raw[offset:offset+1_048_576]
            padding = (-len(raw)) % 512
            if padding:
                yield b'\0'*padding
            total += len(header)+len(raw)+padding
        reads.recheck()
        yield b'\0'*(1024+(-(total+1024)) % tarfile.RECORDSIZE)


def read_request(directory, *, request_id):
    """Read-only metadata export after validating all required original bytes.

    Complete reports are deliberately not serialized into this JSON response.
    Statistical or signature consumers must use open_request within its lease.
    """
    with open_request(directory, request_id=request_id) as (documents, _):
        return documents
