"""Export a retained bar hypothesis for future adapter review, never admission.

The original archive and original source are required. No new forward signal,
execution task, strategy selection, registration or approval is constructed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess

from quantos.contracts.gold_cost_input_limits import MAX_BYTES
from services.research.research_evidence_reader import _identity, _open

ROOT = Path(__file__).resolve().parents[1]
SEMANTICS = ('services/research/gold_bar_candidates.py',
             'services/research/gold_bar_v2_strategy.py', 'tools/gold_bar_cost_replay.py')


def _python_originals(root):
    """Read fresh source bytes through held directories, without following links."""
    if root.resolve() != root:
        raise ValueError('GOLD_BAR_PREPARED_PATH')
    result = []
    entries = 0
    size = 0
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK

    def walk(fd, path, depth):
        nonlocal entries, size
        if depth > 256:
            raise ValueError('GOLD_BAR_PREPARED_BUDGET')
        before = _identity(os.fstat(fd))
        with os.scandir(fd) as iterator:
            children = []
            for entry in iterator:
                entries += 1
                if entries > 10000:
                    raise ValueError('GOLD_BAR_PREPARED_BUDGET')
                children.append((entry.name, entry.is_dir(follow_symlinks=False)))
        for name, directory in children:
            if not directory and not name.endswith('.py'):
                continue
            child = os.open(name, directory_flags if directory else file_flags, dir_fd=fd)
            try:
                info = os.fstat(child)
                if directory:
                    walk(child, path/name, depth+1)
                else:
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError('GOLD_BAR_PREPARED_PATH')
                    size += info.st_size
                    if info.st_size > MAX_BYTES or size > 256*1024*1024:
                        raise ValueError('GOLD_BAR_PREPARED_BUDGET')
                    with os.fdopen(child, 'rb', closefd=False) as stream:
                        raw = stream.read(MAX_BYTES+1)
                    if len(raw) != info.st_size or len(raw) > MAX_BYTES:
                        raise ValueError('GOLD_BAR_PREPARED_CHANGED')
                    result.append((path/name, raw))
                if (_identity(os.fstat(child)) != _identity(info)
                        or _identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != _identity(info)):
                    raise ValueError('GOLD_BAR_PREPARED_CHANGED')
            finally:
                os.close(child)
        if _identity(os.fstat(fd)) != before:
            raise ValueError('GOLD_BAR_PREPARED_CHANGED')

    root_fd = _open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        root_info = _identity(os.fstat(root_fd))
        for folder in ('services', 'quantos', 'tools'):
            child = os.open(folder, directory_flags, dir_fd=root_fd)
            try:
                info = _identity(os.fstat(child))
                walk(child, root/folder, 0)
                if _identity(os.stat(folder, dir_fd=root_fd, follow_symlinks=False)) != info:
                    raise ValueError('GOLD_BAR_PREPARED_CHANGED')
            finally:
                os.close(child)
        checked = _open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            if _identity(os.fstat(root_fd)) != root_info or _identity(os.fstat(checked)) != root_info:
                raise ValueError('GOLD_BAR_PREPARED_CHANGED')
        finally:
            os.close(checked)
    except OSError as exc:
        raise ValueError('GOLD_BAR_PREPARED_PATH') from exc
    finally:
        os.close(root_fd)
    # Match Path's component order, computing the comparison key once per file.
    return sorted(result, key=lambda item: os.path.normcase(str(item[0])).split(os.sep))


def verify_source(directory, expected):
    root = Path(directory).absolute()
    if root.resolve() != root:
        raise ValueError('GOLD_BAR_DEFINITION_SOURCE_PATH')
    digest = hashlib.sha256()
    try:
        originals = _python_originals(root)
    except ValueError as exc:
        if str(exc).startswith('GOLD_BAR_PREPARED_'):
            raise ValueError(str(exc).replace('GOLD_BAR_PREPARED_', 'GOLD_BAR_DEFINITION_SOURCE_', 1)) from exc
        raise
    root_parts = root.parts
    for path, raw in originals:
        # The held-directory walk constructed every descendant from this root.
        # Slice checked components instead of searching its ancestors per leaf.
        if path.parts[:len(root_parts)] != root_parts:
            raise ValueError('GOLD_BAR_DEFINITION_SOURCE_PATH')
        relative = '/'.join(path.parts[len(root_parts):])
        digest.update(relative.encode() + b'\0' + raw + b'\0')
    observed = dict(commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root,
                   text=True).strip(), python_source_sha256=digest.hexdigest(),
                   dependency_lock_sha256=hashlib.sha256((root/'requirements-ci-linux-aarch64.lock').read_bytes()).hexdigest(),
                   python_version=platform.python_version(), architecture=platform.machine())
    if observed != expected:
        raise ValueError('GOLD_BAR_DEFINITION_SOURCE_IDENTITY')
    files = {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in SEMANTICS}
    if any((root/name).read_bytes() != (ROOT/name).read_bytes() for name in SEMANTICS):
        raise ValueError('GOLD_BAR_DEFINITION_SEMANTICS_CHANGED')
    return files


def define(directory, *, candidate, source_directory):
    from services.execution.backtest_authority import exact_identity_hash
    from services.research.gold_bar_candidates import CANDIDATES, candidates
    from tools.gold_bar_experiment import bound_archive
    from tools.gold_bar_review import retained_request
    from tools.gold_cost_review import _read

    if candidate not in CANDIDATES:
        raise ValueError('GOLD_BAR_DEFINITION_CANDIDATE')
    root = Path(directory).absolute()
    request = retained_request(root)
    manifest, bodies, args = bound_archive(root)
    source_files = verify_source(source_directory, manifest['source'])
    report = json.loads(bodies['report'])
    trial = report['trials'][CANDIDATES.index(candidate)]
    if trial['candidate'] != candidate or trial['loss_control']['policy']['max_loss_fraction'] != '0.05':
        raise ValueError('GOLD_BAR_DEFINITION_TRIAL')
    body = dict(schema='GOLD_BAR_RESEARCH_DEFINITION_V1', candidate=candidate,
        symbol='XAUUSDT', sides=['BUY', 'SELL'],
        rules=candidates()[CANDIDATES.index(candidate)],
        size_and_timing=dict(quantity=str(args['quantity']), entry_timeout_ms=5000,
                             max_quote_age_ms=30000),
        risk=trial['loss_control']['policy'],
        research_costs=dict(fee_rate=str(args['fee_rate']), fee_policy=report['fee_policy']),
        trial_id=manifest['trial_ids'][CANDIDATES.index(candidate)],
        source=manifest['source'], semantic_files=source_files,
        archive_files={**request['files'], 'independent-request.json':
                       hashlib.sha256(_read(root, 'independent-request.json')).hexdigest()},
        result_hash=report['result_hash'], recorded_outcome=trial['outcome'],
        scope='RETAINED_HYPOTHESIS_PARAMETERS_NOT_EXECUTION_AUTHORITY',
        execution_adapter_status='BAR_DEMO_ADAPTER_NOT_IMPLEMENTED',
        independent_review_status='INDEPENDENT_AUTHORITY_UNCONFIGURED',
        required_next_steps=['FRESH_FORWARD_BAR_OBSERVATION', 'BAR_DEMO_ENTRY_AND_EXIT_ADAPTER',
                             'INDEPENDENT_STRATEGY_QUALIFICATION', 'EXPLICIT_DEMO_ENROLLMENT_AND_APPROVAL'],
        selected_for_execution=False, authoritative=False, economic_eligible=False,
        submission_enabled=False)
    if retained_request(root) != request or verify_source(source_directory, manifest['source']) != source_files:
        raise ValueError('GOLD_BAR_DEFINITION_SOURCE_CHANGED')
    return dict(body, definition_hash=exact_identity_hash(body, name='gold_bar_research_definition'))



class PreparedBarDefinition:
    """Full validation before observation; exact bytes rechecked on every use.

    Process-local only. Never accepts a serialized verification claim and never
    supplies economic authority. Changes require a fresh full construction.
    """
    def __init__(self, directory, *, candidate, source_directory):
        self._archive = Path(directory).absolute()
        self._source = Path(source_directory).absolute()
        self._candidate = candidate
        before = self._snapshot()
        definition = define(directory, candidate=candidate, source_directory=source_directory)
        if self._snapshot() != before:
            raise ValueError('GOLD_BAR_PREPARED_CHANGED')
        self._files = before
        self._definition = json.dumps(definition, sort_keys=True, allow_nan=False)

    def _snapshot(self):
        from tools.gold_cost_review import _read

        paths = [self._archive/name for name in
            ('input.json', 'report.json', 'manifest.json', 'independent-request.json', 'trials.db')]
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = self._archive/('trials.db'+suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise ValueError('GOLD_BAR_PREPARED_TRIAL_NOT_FROZEN')
        originals = _python_originals(self._source)
        archive_count = len(paths)
        paths += [self._source/'requirements-ci-linux-aarch64.lock', Path(__file__)]
        paths += [ROOT/name for name in SEMANTICS]
        if len(paths) + len(originals) > 10000:
            raise ValueError('GOLD_BAR_PREPARED_BUDGET')
        result = []
        size = 0
        for index, path in enumerate(paths):
            if index == archive_count:
                result.extend((str(name), raw) for name, raw in originals)
                size += sum(len(raw) for _, raw in originals)
            if path.resolve() != path or not path.is_file():
                raise ValueError('GOLD_BAR_PREPARED_PATH')
            size += path.stat().st_size
            if size > 256*1024*1024:
                raise ValueError('GOLD_BAR_PREPARED_BUDGET')
            result.append((str(path), _read(path.parent, path.name)))
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self._source, text=True).strip()
        return commit, tuple(result)

    def definition(self, directory, *, candidate, source_directory):
        if (Path(directory).absolute() != self._archive or Path(source_directory).absolute() != self._source
                or candidate != self._candidate or self._snapshot() != self._files):
            raise ValueError('GOLD_BAR_PREPARED_CHANGED')
        return json.loads(self._definition)


def main(argv=None):
    from services.research.gold_bar_candidates import CANDIDATES

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--candidate', choices=CANDIDATES, required=True)
    parser.add_argument('--source-directory', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    result = define(args.archive, candidate=args.candidate, source_directory=args.source_directory)
    output = Path(args.output).absolute()
    if output.parent.resolve() != output.parent or output.is_symlink():
        raise ValueError('GOLD_BAR_DEFINITION_OUTPUT_PATH')
    with output.open('x') as handle:
        json.dump(result, handle, sort_keys=True, allow_nan=False, indent=2)
        handle.write('\n')
    print(json.dumps(dict(status='RESEARCH_DEFINITION_EXPORTED', definition_hash=result['definition_hash'],
                         output=str(output), submission_enabled=False)))


if __name__ == '__main__':
    main()
