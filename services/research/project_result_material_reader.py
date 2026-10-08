"""Fixed-path original report leases for historical project review rows.

No registry is opened here. That prevents recursive evidence-store verification
and lets the owning SQL transaction supply its own exact registry view.
"""
import tempfile
from contextlib import contextmanager, ExitStack
from pathlib import Path

from quantos.contracts import project_chunked_results as chunked
from quantos.contracts import registered_research_run as native
from quantos.contracts import gold_native_source as source
from quantos.contracts.research_evidence_json import digest
from services.research.project_research_evidence_reader import _ProjectReads
from services.research.gold_chunked_result_reader import open_gold_result
from services.research.research_evidence_reader import _path
from services.validation.project_research_population import validate_project_cut


class ProjectResultMaterialReader:
    """Bootstrap fixes project_root; signed cuts carry only native identities.

    This verifies all complete reports in a historical cut. Current admission
    separately requires the full current project/source/trust snapshot reader.
    """
    def __init__(self, project_root):
        self.project = _path(str(project_root))

    @contextmanager
    def reports(self, cut, *, review_rows=None):
        validate_project_cut(cut)
        materials = {key:run['result_material'] for key, run in cut['runs'].items()
            if run['plan']['schema'] in native.CHUNKED_GOLD_SCHEMAS
            and run['result_material']['status'] == 'COMPLETE'}
        reads = _ProjectReads()
        from services.research.gold_native_source_reader import SourceReports, SourceParts
        reports = SourceReports()
        reports.original_review_rows = {} if review_rows is None else dict(review_rows)
        with ExitStack() as stack:
            source_runs = {key: run for key, run in cut['runs'].items()
                           if run['plan']['schema'] in native.SOURCE_GOLD_SCHEMAS}
            if source_runs:
                from services.research.gold_native_source_reader import capture_source
                import base64
                from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
                reads.directory(self.project)
                managed = self.project/'runs'
                native.require(set(source_runs) <= set(reads.directory(managed)), 'PROJECT_SOURCE_RUN_MISSING')
                for invocation, run in sorted(source_runs.items()):
                    directory = managed/invocation
                    reads.directory(directory)
                    original = run['originals'].get('input.json')
                    document = None if original is None else decode(base64.b64decode(original['bytes_base64'], validate=True))
                    material, manifest = capture_source(reads, directory, run['plan'], document)
                    native.require(material == run['input_material'], 'PROJECT_SOURCE_CHANGED')
                    if material['status'] == 'COMPLETE':
                        reports.source_inputs[invocation] = SourceParts(reports, reads, directory, material)
                    if manifest is not None:
                        native.require(digest(manifest) == run['originals'][source.MANIFEST]['sha256'], 'PROJECT_SOURCE_CHANGED')
            if materials:
                reads.directory(self.project)
                managed = self.project/'runs'
                native.require(set(materials) <= set(reads.directory(managed)), 'PROJECT_REPORT_RUN_MISSING')
                index_bytes = index_nodes = 0
                for invocation, material in sorted(materials.items()):
                    directory = managed/invocation
                    reads.directory(directory)
                    reads.directory(directory/'archive')
                    parts = directory/'archive/report-parts'
                    native.require(set(reads.directory(parts)) == {'manifest.json', 'blobs'},
                        'PROJECT_REPORT_MEMBERS')
                    native.require(set(reads.directory(parts/'blobs')) ==
                        {name.split('/')[1] for name in material['files'] if name.startswith('blobs/')},
                        'PROJECT_REPORT_BLOBS')
                    plan = cut['runs'][invocation]['plan']
                    for name, binding in sorted(material['files'].items()):
                        raw = (reads.read_result(parts/name, plan['result_storage']['chunk_bytes'])
                            if name.startswith('blobs/') else reads.read(parts/name, chunked.gold.MAX_MANIFEST_BYTES))
                        native.require(len(raw) == binding['bytes'] and digest(raw) == binding['sha256'],
                            'PROJECT_REPORT_BYTES')
                    temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix='quantos-retained-result-'))
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
                yield reports
            finally:
                try:
                    reads.recheck()
                finally:
                    reports.close_inputs()
