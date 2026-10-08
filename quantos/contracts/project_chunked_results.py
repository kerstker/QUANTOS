"""Retained original-file bindings; report contents must be supplied separately.

The V2 cut contains every native attempt and every physical result file binding.
It does not substitute this roster for result bytes in a statistical review.
"""
import re
import hashlib
import base64
from collections.abc import Mapping, Sequence

from quantos.contracts import gold_chunked_result as gold
from quantos.contracts import registered_research_run as native
from quantos.contracts.research_evidence_json import canonical, digest, _decode
from quantos.streaming_identity import MAX_BYTES, StreamObject, json_parts, exact_stream_identity_hash

CUT_SCHEMA = 'QUANTOS_PROJECT_RESEARCH_CUT_V2'
SCHEMA = 'QUANTOS_PROJECT_CHUNKED_ORIGINALS_V1'
MANIFEST = 'archive/report-parts/manifest.json'
FAILURE = 'archive/report-parts/failure.json'
ORIGINALS = frozenset((MANIFEST, FAILURE))
LIMITS = dict(schema='QUANTOS_PROJECT_CHUNKED_RESULT_LIMITS_V1',
    maximum_source_bytes=MAX_BYTES, maximum_index_bytes=MAX_BYTES, maximum_nodes=100_000_000)


def require(value, reason):
    if not value:
        raise ValueError('PROJECT_CHUNKED_' + reason)


class PreparedGoldReport(StreamObject, Mapping):
    """One fully verified immutable disk view, confined to its owning lease.

    Construction performs the original complete wire and identity checks. The
    trusted reader supplies an index guard; every reuse checks that same pinned
    index and exact native run. Physical originals remain the reader's exit
    obligation. Ordinary mappings always take the full verification path.
    """
    def __init__(self, run, report, *, index_guard):
        require(isinstance(report, StreamObject) and isinstance(report, Mapping)
            and callable(index_guard), 'PREPARED_READONLY_VIEW')
        index_guard()
        binding = digest(run)
        self.__report = report_for(run, {run['plan']['invocation_id']: report})
        require(digest(run) == binding, 'PREPARED_RUN_CHANGED')
        self.__binding, self.__guard = binding, index_guard
        index_guard()
        super().__init__(self.__report.items)
        self.__sealed = True

    def __setattr__(self, name, value):
        if getattr(self, '_PreparedGoldReport__sealed', False):
            raise AttributeError('PREPARED_REPORT_READONLY')
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if getattr(self, '_PreparedGoldReport__sealed', False):
            raise AttributeError('PREPARED_REPORT_READONLY')
        object.__delattr__(self, name)

    @property
    def index(self):
        return self.__report.index

    def __len__(self):
        return len(self.__report)

    def __iter__(self):
        return iter(self.__report)

    def __getitem__(self, key):
        return self.__report[key]

    def verify_for(self, run):
        self.__guard()
        require(digest(run) == self.__binding, 'PREPARED_RUN_CHANGED')
        return self


def validate_material(plan, originals, documents, material):
    require(plan['schema'] in native.CHUNKED_GOLD_SCHEMAS, 'PLAN')
    require(type(material) is dict and set(material) == {'schema', 'status', 'files', 'bytes'}
        and material['schema'] == SCHEMA and material['status'] in ('COMPLETE', 'INCOMPLETE'), 'FIELDS')
    files = material['files']
    require(type(files) is dict and len(files) <= gold.MAX_PARTS+2, 'FILES')
    for name, binding in files.items():
        blob = re.fullmatch(r'blobs/([0-9a-f]{64})', name) if type(name) is str else None
        require(blob is not None or name in ('manifest.json', 'failure.json'), 'FILE_NAME')
        require(type(binding) is dict and set(binding) == {'sha256', 'bytes'}
            and type(binding['sha256']) is str and re.fullmatch('[0-9a-f]{64}', binding['sha256'])
            and type(binding['bytes']) is int and 0 < binding['bytes'] <= native.MAX_OUTPUT_BYTES, 'FILE_BINDING')
        if blob:
            require(blob[1] == binding['sha256'], 'BLOB_HASH')
        else:
            original = originals.get('archive/report-parts/'+name)
            require(original is not None and original['sha256'] == binding['sha256']
                and binding['bytes'] <= gold.MAX_MANIFEST_BYTES, 'RETAINED_FILE')
    require(type(material['bytes']) is int and material['bytes'] == sum(f['bytes'] for f in files.values()), 'BYTES')
    require(sum(f['bytes'] for n, f in files.items() if n.startswith('blobs/'))
        <= plan['result_storage']['maximum_bytes'], 'SOURCE_BOUND')
    require({n for n in ORIGINALS if n in originals}
        == {'archive/report-parts/'+n for n in files if '/' not in n}, 'METADATA_ROSTER')
    require(not files or 'invocation.json' in documents, 'INVOCATION_MISSING')
    manifest = documents.get(MANIFEST)
    if manifest is not None:
        gold.validate(manifest)
        require(files['manifest.json']['bytes'] == len(canonical(manifest)), 'MANIFEST_BYTES')
        require(manifest['input_sha256'] == plan['input_sha256']
            and manifest['source_identity_sha256'] == digest(plan['source_identity'])
            and manifest['bytes'] <= plan['result_storage']['maximum_bytes'], 'MANIFEST_PLAN')
        expected = {'blobs/'+part['sha256']:part for part in manifest['parts']}
        require({n:f for n,f in files.items() if n.startswith('blobs/')} == expected, 'MANIFEST_FILES')
    if FAILURE in documents:
        failure = documents[FAILURE]
        require(type(failure) is dict and set(failure) ==
            {'status','error_type','bytes','parts','authoritative','economic_eligible','submission_enabled'}
            and failure['status'] == 'INCOMPLETE'
            and all(failure[k] is False for k in ('authoritative','economic_eligible','submission_enabled'))
            and type(failure['error_type']) is str and 0 < len(failure['error_type']) <= 200
            and type(failure['bytes']) is int and 0 <= failure['bytes'] <= MAX_BYTES
            and type(failure['parts']) is list and len(failure['parts']) <= gold.MAX_PARTS,
            'FAILURE')
        require(files['failure.json']['bytes'] == len(canonical(failure)), 'FAILURE_BYTES')
        for part in failure['parts']:
            require(type(part) is dict and set(part) == {'sha256','bytes'}
                and type(part['sha256']) is str
                and files.get('blobs/'+part['sha256']) == part, 'FAILURE_PART')
    reference = documents.get('archive/report.json')
    require((material['status'] == 'COMPLETE') == (reference is not None), 'STATUS')
    if reference is not None:
        gold.validate_reference(reference)
        require(manifest is not None and FAILURE not in documents, 'COMPLETE_MANIFEST')
        require(reference == gold.reference(manifest, originals[MANIFEST]['sha256']), 'REFERENCE')
    return material


def report_for(run, reports):
    """Verify actual complete report bytes against the retained native reference.

    A caller can supply repeatable immutable disk views or complete JSON data,
    but cannot substitute a summary, digest assertion or selected account.
    """
    plan = run['plan']
    require(isinstance(reports, Mapping) and plan['invocation_id'] in reports, 'MATERIAL_REQUIRED')
    report = reports[plan['invocation_id']]
    if type(report) is PreparedGoldReport:
        return report.verify_for(run)
    documents = {name:_decode(base64.b64decode(run['originals'][name]['bytes_base64'], validate=True))
        for name in ('invocation.json', 'archive/report.json', MANIFEST, FAILURE) if name in run['originals']}
    validate_material(plan, run['originals'], documents, run['result_material'])
    require(run['result_material']['status'] == 'COMPLETE', 'REPORT_INCOMPLETE')
    reference = documents['archive/report.json']
    hashed = hashlib.sha256()
    size = 0
    for raw in json_parts(report, maximum_bytes=plan['result_storage']['maximum_bytes']):
        size += len(raw)
        hashed.update(raw)
    require(size == reference['bytes'] and hashed.hexdigest() == reference['sha256'], 'REPORT_BYTES')
    require(isinstance(report, Mapping), 'REPORT_ROOT')
    gold.bind_result(report, documents[MANIFEST])
    if plan['schema'] in native.SOURCE_GOLD_SCHEMAS:
        from quantos.contracts import gold_native_source as source
        document = _decode(base64.b64decode(run['originals']['input.json']['bytes_base64'], validate=True))
        source.validate_material(plan, document, run['originals'], run['input_material'])
        identity = report['dataset_identity']
        source.result_budget(plan['source_storage'], document, report)
        binding = _decode(b''.join(json_parts(identity['source_binding'], maximum_bytes=65_536)))
        require(identity['schema'] == ('GOLD_CHUNKED_BAR_QUOTE_BINDING_V3' if 'replay_budget' in document
                                      else 'GOLD_CHUNKED_BAR_QUOTE_BINDING_V2')
            and identity['input_transport_sha256'] == plan['input_sha256']
            and canonical(binding) == canonical(document['source_binding']), 'SOURCE_REPORT_BINDING')
    require(exact_stream_identity_hash({k:v for k,v in report.items() if k != 'result_hash'})
        == reference['result_hash'], 'REPORT_IDENTITY')
    return report


def full_report_population(cut, reports):
    inputs = {invocation:run['input_material']['part_count'] for invocation, run in cut['runs'].items()
        if run['plan']['schema'] in native.SOURCE_GOLD_SCHEMAS
        and run['input_material']['status'] == 'COMPLETE'}
    if inputs:
        originals = getattr(reports, 'source_inputs', None)
        require(isinstance(originals, Mapping) and set(originals) == set(inputs), 'SOURCE_MATERIAL_REQUIRED')
        require(all(isinstance(originals[key], Sequence) and len(originals[key]) == count
            for key, count in inputs.items()), 'SOURCE_MATERIAL_REQUIRED')
    expected = {invocation for invocation, run in cut['runs'].items()
        if run['plan']['schema'] in native.CHUNKED_GOLD_SCHEMAS
        and run['result_material']['status'] == 'COMPLETE'}
    if expected:
        require(isinstance(reports, Mapping) and set(reports) == expected, 'MATERIAL_REQUIRED')
        for invocation in sorted(expected):
            report_for(cut['runs'][invocation], reports)
