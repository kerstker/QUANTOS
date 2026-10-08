"""Bounded native research semantics for an externally fixed checkpoint layout.

This module does not discover an installed roster, stop writers, verify a
transport, or authorize a restore. Its caller must establish those boundaries.
Only private copies are opened by SQLite/native readers; original files are
read through descriptors and rechecked. Unknown runs remain unknown.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile

from services.research.research_evidence_reader import _ReadSet, _identity, _open, _path, _trial_snapshot
from quantos.contracts.research_evidence_json import canonical, digest
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode

LAYOUT_SCHEMA = 'GOLD_RESEARCH_CHECKPOINT_LAYOUT_V1'
SCHEMA = 'GOLD_RESEARCH_CHECKPOINT_SEMANTIC_V1'
MAX_FILES = 100_000
MAX_BYTES = 20 * 1024**3
MAX_FILE_BYTES = 128 * 1024**2
MAX_ROWS = 300_000
MAX_VIEW_BYTES = 64_000_000
KINDS = frozenset(('PROJECT', 'FACTORY', 'GOLD_RESEARCH', 'TRIAL_LEDGER',
                   'RUN_REGISTRY', 'AUDIT', 'FEEDBACK'))
TREES = frozenset(('PROJECT', 'FACTORY', 'GOLD_RESEARCH', 'FEEDBACK'))


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_RESEARCH_CHECKPOINT_' + reason)


def _relative(value):
    require(type(value) is str and value and len(value) <= 4096, 'RELATIVE_PATH')
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value
            and all(p not in ('', '.', '..') for p in path.parts), 'RELATIVE_PATH')
    require(not any(p.lower() in ('key', 'keys', '.ssh', '.env') for p in path.parts)
            and not value.endswith(('.pem', '.key')) and 'token' not in path.name.lower(), 'SECRET_SCOPE')
    return value


def validate_layout(layout):
    require(type(layout) is dict and set(layout) == {
        'schema', 'source_commit', 'uid', 'gid', 'components'}, 'LAYOUT_FIELDS')
    require(layout['schema'] == LAYOUT_SCHEMA and type(layout['source_commit']) is str
            and re.fullmatch('[0-9a-f]{40}', layout['source_commit']), 'LAYOUT_SOURCE')
    require(all(type(layout[k]) is int and layout[k] >= 0 for k in ('uid', 'gid')), 'LAYOUT_OWNER')
    items = layout['components']
    require(type(items) is list and 1 <= len(items) <= 1024, 'COMPONENTS')
    names, paths, registries = set(), [], set()
    for item in items:
        require(type(item) is dict and set(item) == {'name', 'kind', 'path', 'registry_path'}, 'COMPONENT_FIELDS')
        require(type(item['name']) is str and re.fullmatch('[a-zA-Z0-9_-]{1,80}', item['name'])
                and item['name'] not in names and type(item['kind']) is str
                and item['kind'] in KINDS, 'COMPONENT_IDENTITY')
        names.add(item['name']); paths.append(_relative(item['path']))
        if item['kind'] == 'PROJECT':
            registries.add(_relative(item['registry_path']))
        else:
            require(item['registry_path'] is None, 'REGISTRY_SCOPE')
        if item['kind'] not in TREES:
            require(PurePosixPath(item['path']).suffix in ('.db', '.sqlite3'), 'DATABASE_PATH')
    for index, path in enumerate(paths):
        require(not any(PurePosixPath(path).is_relative_to(other)
            or PurePosixPath(other).is_relative_to(path) for other in paths[index+1:]), 'COMPONENT_OVERLAP')
    require(not any(PurePosixPath(registry).is_relative_to(path)
        or PurePosixPath(path).is_relative_to(registry) for path in paths for registry in registries), 'REGISTRY_OVERLAP')
    return deepcopy(layout)


class _Capture:
    def __init__(self, root, owner):
        self.root = _path(root); self.owner = owner
        self.reads = _ReadSet(maximum_directory_entries=MAX_FILES)
        self.files = {}; self.identities = {}; self.inodes = {}; self.used = 0
        self.directory(self.root)

    def _check(self, path, info, directory=False):
        require((info.st_uid, info.st_gid) == self.owner and not info.st_mode & 0o022, 'CUSTODY')
        require(stat.S_ISDIR(info.st_mode) if directory else
                stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'FILE_TYPE')
        key = (info.st_dev, info.st_ino)
        require(self.inodes.setdefault(key, path) == path, 'PATH_ALIAS')
        require(len(self.inodes) <= MAX_FILES, 'FILE_COUNT')

    def directory(self, path):
        names = self.reads.directory(path)
        fd = _open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            info = os.fstat(fd)
            self._check(path, info, True)
            require(_identity(info) == self.reads.directories[path][0], 'DIRECTORY_CHANGED')
        finally:
            os.close(fd)
        return names

    def walk(self, relative, tree):
        path = self.root / _relative(relative)
        if path in self.identities or path in self.reads.directories:
            return
        fd = _open(path, os.O_RDONLY)
        try:
            info = os.fstat(fd)
            directory = stat.S_ISDIR(info.st_mode)
            self._check(path, info, directory)
        finally:
            os.close(fd)
        if directory:
            require(tree, 'EXPECTED_DATABASE')
            for name in self.directory(path):
                self.walk((PurePosixPath(relative)/name).as_posix(), True)
        else:
            require(tree or path.suffix in ('.db', '.sqlite3') or relative.endswith(('-wal', '-shm')), 'DATABASE_PATH')
            require(not relative.endswith('-journal'), 'ROLLBACK_JOURNAL')
            require(info.st_size <= MAX_FILE_BYTES, 'FILE_BYTES')
            self.used += info.st_size
            require(self.used <= MAX_BYTES, 'TOTAL_BYTES')
            self.identities[path] = _identity(info)

    def database(self, relative):
        path = self.root / _relative(relative)
        names = self.directory(path.parent)
        require(path.name in names, 'DATABASE_MISSING')
        require(path.name+'-journal' not in names, 'ROLLBACK_JOURNAL')
        self.walk(relative, False)
        for suffix in ('-wal', '-shm'):
            if path.name+suffix in names:
                self.walk(relative+suffix, False)

    def copy(self, destination):
        for path in self.reads.directories:
            (destination/path.relative_to(self.root)).mkdir(parents=True, mode=0o700, exist_ok=True)
        for path, identity in sorted(self.identities.items()):
            relative = path.relative_to(self.root).as_posix()
            target = destination/relative; target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            value = self._stream(path, identity, target)
            self.files[relative] = dict(size=identity[6], sha256=value)

    @staticmethod
    def _stream(path, identity, target=None):
        fd = _open(path, os.O_RDONLY)
        output = None
        try:
            require(_identity(os.fstat(fd)) == identity, 'ORIGINAL_CHANGED')
            if target is not None:
                output = target.open('xb'); target.chmod(0o600)
            sha = hashlib.sha256(); size = 0
            while block := os.read(fd, min(1024**2, identity[6]-size+1)):
                size += len(block); require(size <= identity[6], 'ORIGINAL_CHANGED')
                sha.update(block)
                if output is not None:
                    output.write(block)
            require(size == identity[6] and _identity(os.fstat(fd)) == identity, 'ORIGINAL_CHANGED')
            return sha.hexdigest()
        finally:
            if output is not None:
                output.close()
            os.close(fd)

    def recheck(self):
        for path in tuple(self.reads.directories):
            self.directory(path)
        for path, identity in self.identities.items():
            relative = path.relative_to(self.root).as_posix()
            require(self._stream(path, identity) == self.files[relative]['sha256'], 'ORIGINAL_CHANGED')
        return digest(dict(directories={str(p): [list(v[0]), list(v[1])]
                           for p, v in self.reads.directories.items()},
                           files={str(p): list(v) for p, v in self.identities.items()}))


def _database_tables(path, *, row_budget=MAX_ROWS):
    """Bound SQL populations before native verifiers materialize any rows."""
    require(path.is_file(), 'DATABASE_MISSING')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        require(conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'DATABASE_INTEGRITY')
        tables = {}
        schema = [list(row) for row in conn.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name LIMIT 1025')]
        require(len(schema) <= 1024, 'DATABASE_SCHEMA')
        total = 0
        for name, in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"):
            require(re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', name), 'TABLE_NAME')
            count = conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            total += count; require(total <= row_budget, 'SQL_ROWS')
            sha = hashlib.sha256()
            # PK/rowid ordering survives a private SQLite backup. Original rows
            # are kept in the database; this summary cannot replace that body.
            columns = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            primary = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
            order = ','.join('"'+n.replace('"', '""')+'"' for n in primary) if primary else 'rowid'
            for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY {order}'):
                encoded = [dict(blob_sha256=hashlib.sha256(v).hexdigest(), size=len(v))
                           if isinstance(v, bytes) else v for v in row]
                body = canonical(encoded)
                sha.update(len(body).to_bytes(8, 'big')); sha.update(body)
            tables[name] = dict(count=count, sha256=sha.hexdigest())
        return dict(schema_sha256=digest(schema), tables=tables)


def _gold_archive(path):
    """Re-use native identities/ledger and math, never resume an interrupted run."""
    from tools import gold_bar_experiment as gold
    raw = (path/'input.json').read_bytes()
    arguments = gold._decode(decode(raw))
    source = arguments['release_identity']
    source_hash = hashlib.sha256(raw).hexdigest()
    candidates = gold.trial_candidates(arguments)
    definitions = {gold.gold_trial_definition_id(input_sha256=source_hash, candidate=name, source=source):
                   gold._hash('GOLD_BAR_'+name.upper()) for name in candidates}
    if not (path/'trials.db').is_file():
        require(not any((path/name).exists() for name in ('running.json', 'manifest.json', 'report.json',
                    'trials.db-wal', 'trials.db-shm')), 'GOLD_LEDGER_MISSING')
        return dict(terminal='UNKNOWN', input_sha256=source_hash, partial=['LEDGER_NOT_PERSISTED'])
    snapshot = _trial_snapshot(_ReadSet(maximum_directory_entries=MAX_FILES),
                               path/'trials.db', maximum_receipts=MAX_ROWS)
    history = snapshot['history']
    require(len(history) <= len(definitions), 'GOLD_TRIAL_POPULATION')
    running = decode((path/'running.json').read_bytes()) if (path/'running.json').is_file() else None
    policy = gold._hash({'mode':'TECHNICAL_COMPUTATION_ONLY','automatic_selection':False})
    if running is not None:
        require(type(running) is dict and set(running) == {'schema','manifest','identities'}
            and running['schema'] == 'GOLD_BAR_RUNNING_ARCHIVE_V1', 'GOLD_RUNNING')
        from quantos.contracts.registered_research_registration import gold_archive_policy
        policy = gold_archive_policy(running['manifest'], decode(raw))
        require(running['manifest']['input_sha256'] == source_hash
            and canonical(running['manifest']['source']) == canonical(source), 'GOLD_RUNNING_INPUT')
    seen = set()
    for rows in history.values():
        first = rows[0]; definition = first['exact_definition_id']
        require(definition in definitions and definition not in seen
                and first['alpha_id'] == definition and first['family_id'] == definitions[definition]
                and first['validation_policy_digest'] == policy, 'GOLD_TRIAL_BINDING')
        seen.add(definition)
    if running is not None:
        require(type(running) is dict and set(running) == {'schema','manifest','identities'}
                and running['schema'] == 'GOLD_BAR_RUNNING_ARCHIVE_V1', 'GOLD_RUNNING')
        identities = running['identities']
        require(type(identities) is list and len(identities) == len(candidates), 'GOLD_IDENTITIES')
        require([row['trial_id'] for row in identities] == running['manifest']['trial_ids']
                and set(history) == set(running['manifest']['trial_ids']), 'GOLD_RUNNING_TRIALS')
        for identity in identities:
            require(type(identity) is dict and set(identity) == {'trial_id','alpha_id','exact_definition_id',
                'family_id','validation_policy_digest'} and all(history[identity['trial_id']][0][key] == value
                    for key,value in identity.items()), 'GOLD_RUNNING_IDENTITY')
        expected = dict(schema='GOLD_BAR_EXPERIMENT_V1', source=source,
            trial_ids=[row['trial_id'] for row in identities], input_sha256=source_hash,
            submission_enabled=False,economic_eligible=False)
        if running['manifest']['schema'] == 'QUANTOS_REGISTERED_GOLD_BAR_EXPERIMENT_V2':
            from quantos.contracts.registered_research_registration import initial_archive_manifest
            expected = initial_archive_manifest(running['manifest']['registered_plan'])
        require(canonical(running['manifest']) == canonical(expected), 'GOLD_RUNNING_MANIFEST')
    if not (path/'manifest.json').is_file():
        return dict(terminal='UNKNOWN', ledger=snapshot, input_sha256=source_hash,
                    partial=['TERMINAL_MANIFEST_NOT_PERSISTED'])
    require(running is not None, 'GOLD_RUNNING_MISSING')
    manifest = decode((path/'manifest.json').read_bytes())
    require(all(key in manifest and canonical(manifest[key]) == canonical(value)
                for key,value in expected.items()), 'GOLD_MANIFEST_BINDING')
    terminal = manifest.get('terminal')
    require(terminal in ('PASSED','FAILED') and all(rows[-1]['status'] == terminal for rows in history.values()),
            'GOLD_TERMINAL')
    if terminal == 'PASSED':
        checked, bodies, args = gold.bound_archive(path)
        require(checked == manifest and gold._bytes(gold._execute(args)) == bodies['report'], 'GOLD_REPLAY')
    else:
        require(type(manifest.get('error_type')) is str and bool(manifest['error_type']), 'GOLD_FAILURE')
        # Failure is a native terminal fact; do not call its producer again.
        if 'report_sha256' in manifest:
            require((path/'report.json').is_file() and hashlib.sha256((path/'report.json').read_bytes()).hexdigest()
                    == manifest['report_sha256'], 'GOLD_FAILED_REPORT')
    return dict(terminal=terminal, ledger=snapshot, manifest=manifest, partial=[])


def _jobs(path):
    from services.research.run_registry.store import ResearchRunStore, _COLS
    require(path.is_file(), 'JOB_REGISTRY_MISSING')
    store = object.__new__(ResearchRunStore)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON'); store._c = conn
        return {row.run_id: row.to_dict() for row in (store._row(raw) for raw in
            conn.execute('SELECT '+','.join(_COLS)+' FROM runs ORDER BY run_id'))}


def _source(value, source_commit):
    require(type(value) is dict and value.get('commit') == source_commit, 'ORIGINAL_SOURCE')
    return value


def _gold_tree(path, source_commit):
    from tools.gold_bar_program import verify_retained_program
    from tools.gold_bar_research import read_arguments
    archives = {}; programs = {}; requests = {}; partial = []
    directories = {p.parent for p in path.rglob('input.json')}
    directories.update(p.parent for p in path.rglob('trials.db'))
    for directory in sorted(directories):
        document = decode((directory/'input.json').read_bytes())
        require(type(document) is dict and {'release_identity','quantity','fee_rate','initial_collateral'} <= set(document), 'GOLD_INPUT')
        _source(document['release_identity'], source_commit)
        key = directory.relative_to(path).as_posix()
        archives[key] = _gold_archive(directory)
        partial.extend(dict(path=key,reason=reason) for reason in archives[key]['partial'])
    jobs = _jobs(path.parent/'research_runs.db') if (path.parent/'research_runs.db').is_file() else {}
    program_jobs = {}
    for run_id, row in jobs.items():
        identity = row.get('invocation_id')
        if type(identity) is str and identity.startswith('gold-bar-program-'):
            match = re.fullmatch(r'gold-bar-program-([1-9][0-9]*)-(attempt|review|recovery)-([1-9][0-9]*)', identity)
            require(match is not None, 'PROGRAM_JOB_IDENTITY')
            program_jobs.setdefault(match[1], set()).add(run_id)
    require(all((path/'programs'/program/'plan.json').is_file() for program in program_jobs), 'PROGRAM_MISSING')
    if (path/'programs').exists():
        for directory in sorted((path/'programs').iterdir()):
            require(directory.is_dir() and directory.name.isdigit(), 'PROGRAM_DIRECTORY')
            if not (directory/'plan.json').exists():
                require(not any(directory.iterdir()) and directory.name not in program_jobs, 'PROGRAM_PLAN_MISSING')
                programs[directory.name] = dict(plan=None,summary=None,events=[],jobs=[],partial=['PROGRAM_PLAN_NOT_PERSISTED'])
                partial.append(dict(path='programs/'+directory.name,reason='PROGRAM_PLAN_NOT_PERSISTED'))
                continue
            plan = decode((directory/'plan.json').read_bytes())
            value = verify_retained_program(directory, requests_root=path/'requests', results_root=path/'results',
                jobs=jobs, original_source=_source(plan.get('source'), source_commit))
            programs[directory.name] = value
            missing_jobs = program_jobs.get(directory.name, set()) - set(value['jobs'])
            if missing_jobs:
                require(value['summary'] is None or value['summary']['status'] == 'RESEARCH_PROGRAM_INCOMPLETE', 'PROGRAM_UNBOUND_JOB')
                value['partial'].append(dict(reason='JOB_NOT_RECORDED_IN_PROGRAM', run_ids=sorted(missing_jobs)))
            partial.extend(dict(path='programs/'+directory.name,reason=reason) for reason in value['partial'])
    if (path/'requests').exists():
        for directory in sorted((path/'requests').iterdir()):
            require(directory.is_dir() and directory.name.isdigit() and 1 <= int(directory.name) <= 1000, 'REQUEST_DIRECTORY')
            if not (directory/'plan.json').exists():
                require(not (path/'results'/directory.name).exists(), 'REQUEST_PLAN_MISSING')
                partial.append(dict(path='requests/'+directory.name, reason='REQUEST_CAPTURE_INCOMPLETE'))
                continue
            plan = decode((directory/'plan.json').read_bytes())
            args = read_arguments(directory, original_source=_source(plan.get('release_identity'), source_commit))
            from tools.gold_bar_experiment import _bytes, _encode
            raw = _bytes(_encode(args)); output = path/'results'/directory.name
            if (output/'input.json').exists():
                require((output/'input.json').read_bytes() == raw, 'REQUEST_RESULT_BINDING')
            else:
                partial.append(dict(path='requests/'+directory.name,reason='REQUEST_NOT_COMPUTED'))
            requests[directory.name] = dict(input_sha256=hashlib.sha256(raw).hexdigest())
    return dict(archives=archives, programs=programs, requests=requests), partial


def _feedback_tree(path, source_commit, original_path, *, factory=False):
    from tools import demo_feedback_publish as publisher
    from tools.gold_bar_feedback_research import review_retained_hypothesis
    from tools.gold_bar_feedback_experiment import review_retained_experiment
    artifacts = {}; hypotheses = {}; experiments = {}; partial = []
    # A FEEDBACK component may be the publication directory itself or the full
    # factory feedback tree. Other native factory members are handled by Factory.
    publication_roots = [path/name for name in ('bar-execution-feedback','execution-feedback') if (path/name).exists()]
    if not publication_roots and not any((path/name).exists() for name in ('bar-feedback-research','bar-feedback-experiments')) and not factory:
        publication_roots = [path]
    elif not factory:
        require(set(p.name for p in path.iterdir()) <= {'bar-execution-feedback','execution-feedback',
            'bar-feedback-research','bar-feedback-experiments'}, 'FEEDBACK_UNKNOWN_MEMBER')
    for root in publication_roots:
        for item in sorted(root.iterdir()):
            require(item.is_file() and item.suffix == '.json', 'FEEDBACK_MEMBER')
            value = decode(item.read_bytes())
            require(type(value) is dict, 'FEEDBACK_VALUE')
            schema = value.get('schema', '')
            bar = schema in (*publisher.REPORTED_BAR_SCHEMAS, 'GOLD_BAR_CASH_RESEARCH_FEEDBACK_V1')
            require(bar or schema in ('DEMO_EXECUTION_RESEARCH_FEEDBACK_V1','DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1'), 'FEEDBACK_SCHEMA')
            cash = value.get('cash_feedback', value); at = cash.get('available_at_ms')
            require(cash.get('source_commit') == source_commit and type(at) is int and at >= 0, 'FEEDBACK_IDENTITY')
            require(item.name == value['result_hash']+'.json', 'FEEDBACK_FILENAME')
            if bar:
                publisher.validate_bar_export(value, source_commit=source_commit, feedback_sha256=value['result_hash'],
                    now=at, cycle=value.get('bundle_sha256'))
            else:
                publisher.validate_export(value, source_commit=source_commit, feedback_sha256=value['result_hash'], now=at)
            artifacts[item.relative_to(path).as_posix()] = dict(schema=schema,result_hash=value['result_hash'],
                source_commit=source_commit,publication_contract_verified=True, historical_content_only=True,
                execution_ledger_join_verified=False)
    parent = path/'bar-feedback-research'
    if parent.exists():
        for directory in sorted(parent.iterdir()):
            if directory.name.startswith('.') and directory.name.endswith('.lock'):
                require(directory.is_file() and directory.stat().st_size == 0, 'FEEDBACK_LOCK'); continue
            require(directory.is_dir(), 'HYPOTHESIS_DIRECTORY')
            if (directory/'result.json').exists():
                source = _source(decode((directory/'result.json').read_bytes()).get('source'),source_commit)
            else:
                # Incomplete hypothesis has no source-bearing result. Native
                # publication is bound to the layout commit and remains partial.
                source = dict(commit=source_commit)
            value = review_retained_hypothesis(path,directory.name,original_source=source,original_base=original_path)
            hypotheses[directory.name] = value
            partial.extend(dict(path=directory.relative_to(path).as_posix(),reason=r) for r in value['partial'])
    parent = path/'bar-feedback-experiments'
    if parent.exists():
        for directory in sorted(parent.iterdir()):
            if directory.name.startswith('.') and directory.name.endswith('.lock'):
                require(directory.is_file() and directory.stat().st_size == 0, 'FEEDBACK_LOCK'); continue
            require(directory.is_dir(), 'EXPERIMENT_DIRECTORY')
            binding = decode((directory/'binding.json').read_bytes())
            source = _source(binding.get('source'),source_commit)
            db = directory/'state/data/cache/research_runs.db'
            jobs = _jobs(db) if db.is_file() else {}
            value = review_retained_experiment(path,directory.name,original_source=source,original_base=original_path,jobs=jobs)
            research = directory/'state/data/cache/gold_bar_research'
            if research.exists():
                value['research'], more = _gold_tree(research, source_commit)
                value['partial'].extend(more)
            experiments[directory.name] = value
            partial.extend(dict(path=directory.relative_to(path).as_posix(),reason=r) for r in value['partial'])
    return dict(artifacts=artifacts, hypotheses=hypotheses, experiments=experiments), partial


def _shared_registries(layout, components):
    """Join project membership, never merge or renumber the separate ledgers."""
    from services.validation.project_research_population import _registry, _runs
    from quantos.contracts.project_research_binding import bind_project_runs
    groups = {}
    for item in layout['components']:
        if item['kind'] != 'PROJECT':
            continue
        cut = components[item['name']]['native']
        key = item['registry_path']
        if key not in groups:
            groups[key] = dict(projects=[],registry_population=cut['registry_population'],
                               registry_history=cut['registry_history'],runs={},unplanned=[])
        group = groups[key]
        require(canonical(group['registry_population']) == canonical(cut['registry_population'])
                and canonical(group['registry_history']) == canonical(cut['registry_history']), 'SHARED_REGISTRY_CHANGED')
        require(not set(group['runs']) & set(cut['runs']), 'DUPLICATE_NATIVE_INVOCATION')
        group['runs'].update(deepcopy(cut['runs'])); group['projects'].append(item['name'])
        group['unplanned'].extend(cut['unmapped']['unplanned_invocations'])
    for group in groups.values():
        runs = _runs(group['runs'])
        attempts, unmapped = bind_project_runs(runs, group['registry_population'], group['registry_history'],
            _registry(group['registry_population'],group['registry_history']))
        require(len(group['unplanned']) == len(set(group['unplanned']))
                and not set(group['unplanned']) & set(runs), 'DUPLICATE_NATIVE_INVOCATION')
        unmapped['unplanned_invocations'] = sorted(group.pop('unplanned'))
        for invocation, run in runs.items():
            require(canonical(run['partial']) == canonical(group['runs'][invocation]['partial']), 'SHARED_RUN_CHANGED')
        group.update(attempts=attempts,unmapped=unmapped,
                     population_coverage='UNMAPPED' if any(unmapped.values()) else 'COMPLETE')
    return groups


def _native(stage, item, *, source_commit, original_root):
    path = stage/item['path']; kind = item['kind']; partial = []
    if kind == 'PROJECT':
        from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
        value = ProjectResearchEvidenceReader(path, registry_database=stage/item['registry_path'])()
        value.pop('read_identity_sha256')
        partial = [dict(invocation=key, reasons=run['partial']) for key, run in value['runs'].items() if run['partial']]
        if any(value['unmapped'].values()):
            partial.append(dict(reason='UNMAPPED', original=value['unmapped']))
    elif kind == 'FACTORY':
        from tools.ai_factory_checkpoint import _verify_state
        _verify_state(path)
        value = _database_tables(path/'factory.db')
        if any((path/name).exists() for name in ('bar-execution-feedback','execution-feedback','bar-feedback-research','bar-feedback-experiments')):
            value['feedback'], partial = _feedback_tree(path, source_commit, original_root/item['path'], factory=True)
    elif kind == 'TRIAL_LEDGER':
        value = _trial_snapshot(_ReadSet(maximum_directory_entries=MAX_FILES), path, maximum_receipts=MAX_ROWS)
    elif kind == 'RUN_REGISTRY':
        from services.research.run_registry.store import ResearchRunStore, _COLS
        store = object.__new__(ResearchRunStore)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
            store._c = conn
            rows = [asdict(store._row(row)) for row in conn.execute(
                'SELECT '+','.join(_COLS)+' FROM runs ORDER BY run_id')]
        value = dict(rows=rows, status_counts=dict(Counter(row['status'] for row in rows)))
    elif kind == 'AUDIT':
        from services.monitoring.audit_store import DurableAuditLog
        log = DurableAuditLog(str(path), create=False, read_only=True)
        try:
            require(log.chain_valid(), 'AUDIT_CHAIN')
            value = dict(length=log.seal().length, head_hash=log.head_hash)
        finally:
            log.close()
    elif kind == 'GOLD_RESEARCH':
        value, partial = _gold_tree(path, source_commit)
    elif kind == 'FEEDBACK':
        value, partial = _feedback_tree(path, source_commit, original_root/item['path'])
    else:
        raise ValueError('GOLD_RESEARCH_CHECKPOINT_UNKNOWN_KIND')
    return dict(kind=kind, native=value, partial=partial)


_VERIFIED = object()


class VerifiedResearchCut:
    """Same-process original read set, not a writer or transport capability."""
    __slots__ = ('_capture', '_view')

    def __init__(self, token, capture, view):
        require(token is _VERIFIED, 'VERIFIED_CAPTURE_REQUIRED')
        self._capture = capture
        self._view = deepcopy(view)

    def view(self):
        return deepcopy(self._view)

    def files_manifest(self):
        return deepcopy(self._view['files'])

    def recheck(self):
        require(self._capture.owner == (os.geteuid(), os.getegid()), 'PROCESS_OWNER')
        require(self._capture.recheck() == self._view['read_identity_sha256'], 'ORIGINAL_CHANGED')
        return self


def capture_verified(root, *, layout, isolated_restore_owner=None, original_root=None):
    view, captured = _verify(root, layout=layout, isolated_restore_owner=isolated_restore_owner,
                             original_root=original_root)
    return VerifiedResearchCut(_VERIFIED, captured, view)


def verify(root, *, layout, isolated_restore_owner=None, original_root=None):
    return capture_verified(root, layout=layout, isolated_restore_owner=isolated_restore_owner,
                            original_root=original_root).view()


def _verify(root, *, layout, isolated_restore_owner=None, original_root=None):
    """Read one bounded research cut; never prove writer or transport authority.

    An isolated restore retains the original layout identity. Only its actual
    current process owner may be supplied, by the already verified transport.
    The return deliberately does not claim that transport was verified here.
    """
    layout = validate_layout(layout)
    owner = (layout['uid'], layout['gid']) if isolated_restore_owner is None else isolated_restore_owner
    require(type(owner) is tuple and len(owner) == 2 and all(type(v) is int for v in owner)
            and owner == (os.geteuid(), os.getegid()), 'PROCESS_OWNER')
    root = _path(root)
    original_root = root if original_root is None else Path(original_root)
    require(original_root.is_absolute() and '..' not in original_root.parts, 'ORIGINAL_ROOT')
    require(isolated_restore_owner is not None or original_root == root, 'CAPTURE_ROOT')
    capture = _Capture(root, owner)
    for item in layout['components']:
        if item['kind'] in TREES:
            capture.walk(item['path'], True)
        else:
            capture.database(item['path'])
        if item['registry_path'] is not None:
            capture.database(item['registry_path'])
    with tempfile.TemporaryDirectory(prefix='quantos-research-checkpoint-') as temporary:
        stage = Path(temporary)
        capture.copy(stage)
        databases = {}; total_rows = 0; components = {}; view_bytes = 0; ledgers = {}
        for name in capture.files:
            if Path(name).suffix not in ('.db', '.sqlite3'):
                continue
            database = _database_tables(stage/name, row_budget=MAX_ROWS-total_rows)
            total_rows += sum(table['count'] for table in database['tables'].values())
            databases[name] = database
        for name, database in databases.items():
            if 'trial_events' in database['tables']:
                ledgers[name] = _trial_snapshot(_ReadSet(maximum_directory_entries=MAX_FILES), stage/name,
                    maximum_receipts=MAX_ROWS)
                view_bytes += len(canonical(ledgers[name])); require(view_bytes <= MAX_VIEW_BYTES, 'VIEW_BYTES')
        for item in layout['components']:
            value = _native(stage, item, source_commit=layout['source_commit'], original_root=original_root)
            view_bytes += len(canonical(value)); require(view_bytes <= MAX_VIEW_BYTES, 'VIEW_BYTES')
            components[item['name']] = value
        registries = _shared_registries(layout, components)
        # A legacy helper may attempt a private migration. Never silently accept
        # altered rows or a new schema as verification of the original database.
        require(databases == {name: _database_tables(stage/name) for name in databases}, 'NATIVE_DATABASE_CHANGED')
        observed = capture.recheck()
    semantic = dict(schema=SCHEMA, layout_sha256=digest(layout), original_root=str(original_root), components=components, registries=registries,
        files=capture.files, databases=databases, trial_ledgers=ledgers, bytes=capture.used,
        writer_barrier_verified=False, transport_verified=False, whole_set_verified=False,
        submission_enabled=False, economic_authority=False)
    require(len(canonical(semantic)) <= MAX_VIEW_BYTES, 'VIEW_BYTES')
    return ({**semantic, 'semantic_sha256': digest(semantic), 'read_identity_sha256': observed,
             'custody_mode': 'CAPTURE' if isolated_restore_owner is None else 'ISOLATED_RESTORE'}, capture)
