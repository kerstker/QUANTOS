"""Complete native input originals in an owning project read set; never execution."""
from pathlib import Path
from collections.abc import Sequence

from quantos.contracts import gold_native_source as shape
from quantos.contracts.research_evidence_json import digest
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode


class SourceReports(dict):
    """Complete reports plus trusted original inputs, valid only inside their cut."""
    def __init__(self):
        super().__init__()
        self.source_inputs = {}
        self.closed = False

    def close_inputs(self):
        self.closed = True

    def prepare_report_views(self, cut):
        """Verify complete immutable indexes once; never cache across leases."""
        from quantos.contracts.project_chunked_results import PreparedGoldReport
        from quantos.contracts.perpetual_chunked_result import require
        for invocation, report in list(self.items()):
            index = report.index
            def guard(index=index):
                require(not self.closed, 'REPORT_CONTEXT_CLOSED')
                index.connection()
                require(index.pin() == index.bound, 'INDEX_CHANGED')
            self[invocation] = PreparedGoldReport(cut['runs'][invocation], report, index_guard=guard)


class SourceParts(Sequence):
    def __init__(self, owner, reads, directory, material):
        self.owner, self.reads = owner, reads
        self.root = Path(directory)/shape.DIRECTORY
        self.descriptors = material['parts']

    def _check(self):
        shape.require(not self.owner.closed, 'INPUT_CONTEXT_CLOSED')

    def __len__(self):
        self._check()
        return len(self.descriptors)

    def __getitem__(self, index):
        self._check()
        if isinstance(index, slice):
            raise ValueError('GOLD_NATIVE_SOURCE_PART_SLICE')
        descriptor = self.descriptors[index]
        def read(ref):
            path = self.root/'blobs'/ref['sha256']
            raw = self.reads.read_input(path, self.reads.files[path][2])
            shape.require(len(raw) == ref['bytes'] and digest(raw) == ref['sha256'], 'INPUT_CHANGED')
            return decode(raw)
        value = dict(capture=read(descriptor['capture']))
        if 'observed_funding_records' in descriptor:
            value['observed_funding_records'] = read(descriptor['observed_funding_records'])
        return value


def capture_source(reads, directory, plan, document):
    root = Path(directory)/shape.DIRECTORY
    material = dict(schema=shape.MATERIAL_SCHEMA, status='INCOMPLETE', files={}, bytes=0, part_count=0, parts=[])
    if not root.exists():
        shape.require(not root.is_symlink(), 'SOURCE_PATH')
        return material, None
    members = set(reads.directory(root))
    shape.require('blobs' in members and members <= {'blobs', 'manifest.json'}, 'SOURCE_MEMBERS')
    names = set(reads.directory(root/'blobs'))
    shape.require(len(names) <= shape.source.MAX_BLOBS, 'SOURCE_FILES')
    for name in sorted(names):
        shape.require(shape.re.fullmatch('[0-9a-f]{64}', name) is not None, 'SOURCE_NAME')
        raw = reads.read_input(root/'blobs'/name, shape.source.MAX_INPUT_BYTES)
        shape.require(digest(raw) == name, 'SOURCE_BLOB_CHANGED')
        material['files']['blobs/'+name] = dict(sha256=name, bytes=len(raw))
    raw_manifest = None
    if 'manifest.json' in members:
        raw_manifest = reads.read_input(root/'manifest.json', shape.source.MAX_MANIFEST_BYTES)
        manifest = shape.source.manifest(decode(raw_manifest))
        reference, parts, files = shape.inspect(manifest, names,
            lambda ref: reads.read_input(root/'blobs'/ref['sha256'],
                reads.files[root/'blobs'/ref['sha256']][2]),
            manifest_sha256=digest(raw_manifest))
        reference = shape.bound_reference(reference, document)
        shape.bind_plan(plan, document)
        shape.require(reference == document and files == material['files'], 'SOURCE_REFERENCE')
        material.update(status='COMPLETE', part_count=manifest['part_count'], parts=parts)
        material['files']['manifest.json'] = dict(sha256=digest(raw_manifest), bytes=len(raw_manifest))
    material['bytes'] = sum(value['bytes'] for value in material['files'].values())
    shape.require(material['bytes'] <= plan['source_storage']['maximum_bytes'], 'SOURCE_BYTES')
    return material, raw_manifest
