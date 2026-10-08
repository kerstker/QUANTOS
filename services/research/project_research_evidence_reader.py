"""A stable read of all managed attempts and their separate original ledgers.

The bootstrap fixes the project and registry paths. Directory enumeration, not
a caller-selected list, determines the population. Native global sequences are
never merged or renumbered. Missing legacy mappings remain visible as UNMAPPED.
This is a read boundary, not a result reviewer, risk attestation or authority.
"""
from __future__ import annotations

import base64
from contextlib import closing, contextmanager, ExitStack
import os
import re
import stat
import tempfile
from pathlib import Path

from quantos.contracts import registered_research_run as contract
from quantos.contracts import project_chunked_results as chunked
from quantos.contracts import gold_native_source as source
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from quantos.contracts.project_research_binding import require, bind_project_runs as _bind
from services.research.research_evidence_reader import (
    _ReadSet, _decode, _path, _private_database, _trial_snapshot, _open,
)
from services.validation.research_evidence_artifact import canonical, digest

SCHEMA = 'QUANTOS_PROJECT_RESEARCH_CUT_V1'
MAX_RUNS = 10_000
MAX_FILES = 100_000
MAX_ORIGINAL_BYTES = 512 * 1024**2
MAX_RETAINED_BYTES = 32_000_000
MAX_ROWS = 100_000
RUN_FILES = frozenset('plan.json input.json registration.json invocation.json outcome.json archive input-parts'.split())
ARCHIVE_FILES = frozenset('input.json running.json report.json manifest.json trials.db trials.db-wal trials.db-shm'.split())


class _ProjectReads(_ReadSet):
    def __init__(self):
        super().__init__(maximum_directory_entries=MAX_FILES)
        self.used = 0
        self.inodes = {}
        self.result_paths = set()
        self.result_used = 0
        self.input_paths = set()
        self.input_used = 0

    def _unique(self, path, identity):
        inode = tuple(identity[:2])
        require(self.inodes.setdefault(inode, path) == path, 'PATH_ALIAS')
        require(len(self.inodes) <= MAX_FILES, 'FILE_COUNT')

    def directory(self, path):
        names = super().directory(path)
        require(len(names) <= MAX_FILES, 'DIRECTORY_BOUND')
        self._unique(path, self.directories[path][0])
        return names

    def read(self, path, limit):
        if path in self.input_paths:
            return self.read_input(path, limit)
        if path in self.result_paths:
            return self.read_result(path, limit)
        known = path in self.files
        remaining = MAX_ORIGINAL_BYTES - self.used
        raw = super().read(path, limit if known else min(limit, remaining))
        if not known:
            self.used += len(raw)
        self._unique(path, self.files[path][0])
        return raw

    def read_result(self, path, limit):
        known = path in self.files
        raw = super().read(path, limit if known else min(limit,
            chunked.LIMITS['maximum_source_bytes']-self.result_used))
        if not known:
            self.result_used += len(raw)
        self.result_paths.add(path)
        self._unique(path, self.files[path][0])
        return raw

    def read_input(self, path, limit):
        known = path in self.files
        raw = super().read(path, limit if known else min(limit,
            source.LIMITS['maximum_bytes']-self.input_used))
        if not known:
            self.input_used += len(raw)
        self.input_paths.add(path)
        self._unique(path, self.files[path][0])
        return raw


def _retained(raw):
    return dict(sha256=digest(raw), bytes_base64=base64.b64encode(raw).decode('ascii'))


def _lease_custody(reads, database):
    """File custody while the owning SQLite lease verifies changing DB content."""
    reads.directory(database.parent)
    fd = _open(database, os.O_RDONLY)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022, 'REGISTRY_LEASE_CUSTODY')
        identity = (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_nlink)
        reads._unique(database, identity)
        return identity
    finally:
        os.close(fd)


def _registry_history(store):
    # Original semantic checks run before this extraction. Preserve STARTED
    # attempts with no candidates and terminal/heartbeat/recovery history too.
    return dict(events=[dict(row) for row in store._search_event_rows()],
                heartbeats=[dict(row) for row in store._search_heartbeat_rows()],
                recoveries=[dict(row) for row in store._search_recovery_rows()],
                evaluations=[dict(row) for row in store._conn.execute(
                    'SELECT run_id,dataset_version,recorded_ts,content_hash FROM evaluations ORDER BY run_id')],
                evaluation_links=[dict(row) for row in store._conn.execute(
                    'SELECT * FROM search_evaluations ORDER BY link_seq')])


def _ledger_view(snapshot):
    history = snapshot['history']
    ordered = sorted((row for rows in history.values() for row in rows), key=lambda row: row['global_seq'])
    return dict(history=history, event_count=len(ordered),
                head_hash=ordered[-1]['event_hash'] if ordered else '',
                trial_count=len(history), validation_receipts=snapshot['validation_receipts'],
                validation_receipt_head=snapshot['validation_receipt_head'])


class ProjectResearchEvidenceReader:
    """Capture native originals without initializing or repairing their stores.

    ``cut_sha256`` binds semantic population and original run bytes. The separate
    read identity records this physical observation, including registry DB/WAL.
    A later review changes activity history and therefore this semantic cut.
    A retained signed cut is historical; its consumer must check its relation to
    a new cut explicitly instead of treating a previous review as current.
    """
    def __init__(self, project_root, *, registry_database):
        self.project = _path(project_root)
        self.registry = _path(registry_database)
        require(not self.registry.is_relative_to(self.project / 'runs'), 'REGISTRY_LOCATION')

    def __call__(self):
        return self._capture(_ProjectReads())

    @contextmanager
    def evidence(self):
        """Complete cut and original reports under the same physical read lease.

        Report views cannot escape this context. The original bytes remain in
        their native archive; temporary derived indexes are never evidence.
        """
        with self._evidence(_ProjectReads()) as value:
            yield value

    @contextmanager
    def _evidence(self, reads, *, registry_view_for=None):
        """Internal composition keeps source and project in one read set."""
        from services.research.gold_native_source_reader import SourceReports
        reports = SourceReports()
        with ExitStack() as stack:
            cut = self._capture(reads, registry_view_for=registry_view_for,
                report_stack=stack, reports=reports)
            reports.prepare_report_views(cut)
            try:
                yield cut, reports
            finally:
                try:
                    reads.recheck()
                finally:
                    reports.close_inputs()

    def _capture(self, reads, *, registry_view_for=None, report_stack=None, reports=None):
        """Internal composition seam retaining one original-file read set."""
        reads.directory(self.project)
        managed = self.project / 'runs'
        names = reads.directory(managed)
        ids, locks = [], []
        for name in names:
            if re.fullmatch('[0-9a-f]{64}', name):
                ids.append(name)
            else:
                match = re.fullmatch(r'\.([0-9a-f]{64})\.writer\.lock', name)
                require(match is not None, 'RUN_ROSTER')
                require(reads.read(managed / name, 0) == b'', 'LOCK_CONTENT')
                locks.append(match[1])
        require(len(ids) <= MAX_RUNS and len(locks) <= MAX_RUNS, 'RUN_COUNT')
        runs = {}
        retained_bytes = 0
        output_bytes = 0
        index_bytes = index_nodes = 0
        chunked_population = False
        source_population = False
        for invocation in ids:
            directory = managed / invocation
            members = set(reads.directory(directory))
            require({'plan.json'} <= members <= RUN_FILES, 'RUN_MEMBERS')
            originals, documents = {}, {}
            for name in sorted(members - {'archive', source.DIRECTORY}):
                raw = reads.read(directory / name,
                    min(contract.MAX_PLAN_BYTES if name == 'plan.json' else contract.MAX_OUTPUT_BYTES,
                        MAX_RETAINED_BYTES-retained_bytes))
                retained_bytes += len(raw)
                documents[name] = _decode(raw)
                originals[name] = _retained(raw)
            plan = contract.validate_plan(documents['plan.json'])
            new_result = plan['schema'] in contract.CHUNKED_GOLD_SCHEMAS
            new_source = plan['schema'] in contract.SOURCE_GOLD_SCHEMAS
            require(new_source or source.DIRECTORY not in members, 'SOURCE_DIRECTORY')
            source_population |= new_source
            chunked_population |= new_result
            result_material = dict(schema=chunked.SCHEMA, status='INCOMPLETE', files={}, bytes=0)
            require(plan['invocation_id'] == invocation, 'RUN_IDENTITY')
            require(originals['plan.json']['sha256'] == contract.digest(plan), 'PLAN_BYTES')
            partial = []
            if 'input.json' in originals:
                require(originals['input.json']['sha256'] == plan['input_sha256'], 'INPUT_BINDING')
            else:
                require(members == {'plan.json'}, 'INPUT_MISSING')
                partial.append('INPUT_NOT_PERSISTED')
            if new_source:
                from services.research.gold_native_source_reader import capture_source
                input_material, raw_manifest = capture_source(reads, directory, plan, documents.get('input.json'))
                if raw_manifest is not None:
                    documents[source.MANIFEST] = _decode(raw_manifest)
                    originals[source.MANIFEST] = _retained(raw_manifest)
                    retained_bytes += len(raw_manifest)
                if 'input.json' in documents:
                    source.validate_material(plan, documents['input.json'], originals, input_material)
                if input_material['status'] != 'COMPLETE':
                    partial.append('INPUT_ORIGINALS_NOT_PERSISTED')
            snapshot = dict(history={}, validation_receipts=[], validation_receipt_head=None)
            if 'archive' in members:
                archive = directory / 'archive'
                archived = set(reads.directory(archive))
                allowed = ARCHIVE_FILES | ({'risk-replay.json'} if plan['schema']==contract.RISK_EVALUATED_SCHEMA else set())
                if new_result:
                    allowed = allowed | {'report-parts'}
                require(archived <= allowed, 'ARCHIVE_MEMBERS')
                for name in sorted(archived & {'input.json', 'running.json', 'report.json', 'manifest.json', 'risk-replay.json'}):
                    raw = reads.read(archive / name, min(contract.MAX_OUTPUT_BYTES, MAX_RETAINED_BYTES-retained_bytes))
                    retained_bytes += len(raw)
                    documents['archive/'+name] = _decode(raw)
                    originals['archive/'+name] = _retained(raw)
                if 'report-parts' in archived:
                    parts = archive/'report-parts'
                    part_members = set(reads.directory(parts))
                    require('blobs' in part_members and part_members <= {'blobs', 'manifest.json', 'failure.json'},
                        'RESULT_MEMBERS')
                    blobs = reads.directory(parts/'blobs')
                    require(len(blobs) <= chunked.gold.MAX_PARTS, 'RESULT_FILES')
                    for name in blobs:
                        require(re.fullmatch('[0-9a-f]{64}', name), 'RESULT_NAME')
                        raw = reads.read_result(parts/'blobs'/name, plan['result_storage']['chunk_bytes'])
                        require(digest(raw) == name, 'RESULT_HASH')
                        result_material['files']['blobs/'+name] = dict(sha256=name, bytes=len(raw))
                    for name in sorted(part_members-{'blobs'}):
                        raw = reads.read(parts/name, min(chunked.gold.MAX_MANIFEST_BYTES,
                            MAX_RETAINED_BYTES-retained_bytes))
                        retained_bytes += len(raw)
                        key = 'archive/report-parts/'+name
                        originals[key] = _retained(raw)
                        documents[key] = _decode(raw)
                        result_material['files'][name] = dict(sha256=digest(raw), bytes=len(raw))
                    result_material['bytes'] = sum(f['bytes'] for f in result_material['files'].values())
                if new_result:
                    result_material['status'] = 'COMPLETE' if 'archive/report.json' in documents else 'INCOMPLETE'
                    chunked.validate_material(plan, originals, documents, result_material)
                    if result_material['status'] == 'COMPLETE':
                        from services.research.gold_chunked_result_reader import open_gold_result
                        # Keep all required report views for a composed consumer,
                        # or verify and close immediately for a metadata capture.
                        local_stack = ExitStack() if report_stack is None else report_stack
                        try:
                            temporary = local_stack.enter_context(tempfile.TemporaryDirectory(prefix='quantos-project-result-'))
                            maximum_index = min(plan['result_storage']['maximum_index_bytes'],
                                chunked.LIMITS['maximum_index_bytes']-index_bytes)
                            report = local_stack.enter_context(open_gold_result(parts, Path(temporary)/'index',
                                expected_manifest_sha256=originals[chunked.MANIFEST]['sha256'],
                                maximum_source_bytes=plan['result_storage']['maximum_bytes'],
                                maximum_index_bytes=maximum_index,
                                maximum_nodes=chunked.LIMITS['maximum_nodes']-index_nodes))
                            index_bytes += report.index.path.stat().st_size
                            index_nodes += report.index.nodes
                            if reports is not None:
                                reports[invocation] = report
                        finally:
                            if report_stack is None:
                                local_stack.close()
                if 'archive/input.json' in originals:
                    require(originals['archive/input.json']['sha256'] == plan['input_sha256'], 'NATIVE_INPUT')
                if 'trials.db' in archived:
                    snapshot = _trial_snapshot(reads, archive / 'trials.db', maximum_receipts=MAX_ROWS*3)
                else:
                    require(not archived & {'trials.db-wal', 'trials.db-shm'}, 'NATIVE_DATABASE_MISSING')
                    require(not members & {'invocation.json', 'outcome.json'}
                        and not archived & {'running.json', 'report.json', 'manifest.json', 'risk-replay.json'}, 'NATIVE_DATABASE_MISSING')
            else:
                require(not members & {'invocation.json', 'outcome.json'}, 'NATIVE_DATABASE_MISSING')
            expected = {trial['native_trial_id']: trial for trial in plan['trials']}
            history = snapshot['history']
            require(set(history) <= set(expected), 'NATIVE_TRIAL_POPULATION')
            require(not history or 'archive/input.json' in originals, 'NATIVE_INPUT_MISSING')
            require(not new_source or not history or input_material['status'] == 'COMPLETE', 'NATIVE_SOURCE_MISSING')
            for tid, events in history.items():
                first = events[0]
                trial = expected[tid]
                require(all(first[k] == trial[target] for k, target in (
                    ('alpha_id', 'alpha_id'), ('family_id', 'family_id'),
                    ('exact_definition_id', 'config_hash'), ('validation_policy_digest', 'validation_policy_digest'))),
                    'NATIVE_TRIAL_BINDING')
            runs[invocation] = dict(plan=plan, originals=originals, ledger=_ledger_view(snapshot),
                                    partial=partial, documents=documents)
            if new_result:
                runs[invocation]['result_material'] = result_material
            if new_source:
                runs[invocation]['input_material'] = input_material
                if reports is not None and input_material['status'] == 'COMPLETE':
                    from services.research.gold_native_source_reader import SourceParts
                    reports.source_inputs[invocation] = SourceParts(reports, reads, directory, input_material)
            # Bound cumulative retained output before another input is loaded.
            output_bytes += len(canonical(runs[invocation]))
            require(output_bytes <= 64_000_000, 'OUTPUT_BOUND')

        if registry_view_for is None:
            from services.research.project_result_material_reader import ProjectResultMaterialReader
            with _private_database(reads, self.registry) as private:
                with closing(SqliteExperimentStore(str(private), create=False, read_only=True,
                        project_result_reader=ProjectResultMaterialReader(self.project))) as store:
                    # Bound rows before the original full verifier materializes them.
                    for row in store._conn.execute("SELECT name FROM sqlite_schema WHERE type='table'"):
                        table = row[0]
                        require(re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', table), 'REGISTRY_TABLE')
                        count = store._conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                        require(count <= MAX_ROWS*3, 'REGISTRY_ROWS')
                    population = store.research_evidence_population()
                    history = _registry_history(store)
                    if reports is not None:
                        reports.original_review_rows=store._frozen_validation_rows_locked(runs)
                    attempts, unmapped = _bind(runs, population, history, ExperimentRegistry(store))
        else:
            # Only the original store's active write lease provides this native
            # capability. Its own rollback journal must not be copied or ignored.
            # All project files/native ledgers are still captured above.
            from services.validation.project_research_population import _registry
            registry_custody = _lease_custody(reads, self.registry)
            view = registry_view_for(self.registry)
            require(type(view) is dict and set(view) == {'population', 'history'}, 'REGISTRY_LEASE_VIEW')
            population, history = view['population'], view['history']
            if reports is not None:
                port=getattr(registry_view_for,'frozen_rows_for',None)
                if callable(port):
                    reports.original_review_rows=port(runs)
            registry = _registry(population, history)
            attempts, unmapped = _bind(runs, population, history, registry)

        # Only the exact original files and derived ledger rows leave the reader.
        for run in runs.values():
            del run['documents']
        orphan_locks = sorted(set(locks)-set(ids))
        unmapped['unplanned_invocations'] = orphan_locks
        unfinished_runs = sorted(invocation for invocation, run in runs.items()
            if run['partial'] or any(row['invocation_id'] == invocation
                and row['status'] not in ('PASSED', 'FAILED', 'CANCELLED') for row in attempts))
        cut = dict(schema=source.CUT_SCHEMA if source_population else chunked.CUT_SCHEMA if chunked_population else SCHEMA,
            registry_population=population, registry_history=history,
            runs=runs, attempts=attempts,
            attempt_count=len(attempts)+len(unmapped['search_candidates']),
            mapped_attempt_count=len(attempts), registered_attempt_count=len(population['search_candidates']),
            unique_definition_count=len({row['config_hash'] for row in population['trials']} |
                                        {row['config_hash'] for row in attempts}),
            population_coverage='UNMAPPED' if any(unmapped.values()) else 'COMPLETE', unmapped=unmapped,
            unfinished_attempts=[row['native_trial_id'] for row in attempts
                                 if row['status'] not in ('PASSED', 'FAILED', 'CANCELLED')],
            unfinished_runs=unfinished_runs,
            all_runs_finalized=bool(runs) and not unfinished_runs and not any(unmapped.values()),
            submission_enabled=False, economic_authority=False)
        if chunked_population:
            cut['result_limits'] = dict(chunked.LIMITS)
        if source_population:
            cut['input_limits'] = dict(source.LIMITS)
        canonical(cut)
        cut['cut_sha256'] = digest(cut)
        if registry_view_for is not None:
            require(canonical(registry_view_for(self.registry)) == canonical(view), 'REGISTRY_LEASE_CHANGED')
            require(_lease_custody(reads, self.registry) == registry_custody, 'REGISTRY_LEASE_CUSTODY_CHANGED')
        cut['read_identity_sha256'] = reads.recheck()
        return cut
