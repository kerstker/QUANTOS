"""Fixed protected locations for retained bar episodes, without account access."""
import hashlib
from pathlib import Path
import re
import stat

from live.execution.demo_batch_risk import _fields
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.demo_resident_recovery import _check_path
from tools.gold_bar_demo_entry import restore_engine
from tools.gold_cost_review import _read

OBSERVATION_FILES = ('registration.json', 'result.json', 'selected-decisions.jsonl', 'live-signals.jsonl')
JOURNAL_FILES = ('plan.json', 'summary.json', 'warmup.json', 'warmup-receipt.json', 'messages.jsonl', 'decisions.jsonl')
TRIAL_FILES = ('manifest.json', 'input.json', 'report.json', 'independent-request.json', 'trials.db')


def protected_tree(root, uid):
    pending = [(root, '.')]
    facts = []
    while pending:
        path, relative = pending.pop()
        info = path.lstat()
        directory = stat.S_ISDIR(info.st_mode)
        _check_path(path, uid, directory=directory)
        facts.append((relative, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        require(len(facts)+len(pending) <= 10000, 'BAR_WORKER_SOURCE_BUDGET')
        if directory:
            for child in path.iterdir():
                name = child.name if relative == '.' else relative + '/' + child.name
                pending.append((child, name))
                require(len(facts)+len(pending) <= 10000, 'BAR_WORKER_SOURCE_BUDGET')
    return sorted(facts)


def load(root, bundle, *, source_commit, uid):
    _fields(bundle, 'schema source_commit observation_sha256 trial_manifest_sha256 archive_source_commit entry_decision_ms')
    require(bundle['schema'] == 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1'
            and bundle['source_commit'] == source_commit, 'BAR_WORKER_SOURCE')
    for key, length in (('observation_sha256', 64), ('trial_manifest_sha256', 64), ('archive_source_commit', 40)):
        require(type(bundle[key]) is str and re.fullmatch('[a-f0-9]{'+str(length)+'}', bundle[key]) is not None,
                'BAR_WORKER_INPUT_ID')
    require(type(bundle['entry_decision_ms']) is int and 0 <= bundle['entry_decision_ms'] <= 2**63-1, 'BAR_WORKER_DECISION_TIME')
    root = Path(root).absolute()
    require(root.resolve() == root, 'BAR_WORKER_ROOT')
    observation = root/'strategy-bar-observations'/bundle['observation_sha256']
    trial = root/'strategy-bar-trials'/bundle['trial_manifest_sha256']
    source = root/'strategy-bar-sources'/bundle['archive_source_commit']
    for path in (root, observation.parent, observation, observation/'journal', trial.parent, trial, source.parent):
        _check_path(path, uid, directory=True)
    for area, names in ((observation, OBSERVATION_FILES), (observation/'journal', JOURNAL_FILES), (trial, TRIAL_FILES)):
        for name in names:
            _check_path(area/name, uid, directory=False)
    for suffix in ('-wal', '-shm', '-journal'):
        path = trial/('trials.db'+suffix)
        require(not path.exists() and not path.is_symlink(), 'BAR_WORKER_TRIAL_NOT_FROZEN')
    raw_result = _read(observation, 'result.json')
    raw_manifest = _read(trial, 'manifest.json')
    require(hashlib.sha256(raw_result).hexdigest() == bundle['observation_sha256']
        and hashlib.sha256(raw_manifest).hexdigest() == bundle['trial_manifest_sha256']
        and decode(raw_manifest)['source']['commit'] == bundle['archive_source_commit'], 'BAR_WORKER_ARCHIVE_BINDING')
    facts = protected_tree(source, uid)
    engine = restore_engine(observation, archive=trial, source_directory=source)
    definition = engine.verified_definition()
    require(definition['archive_files']['manifest.json'] == bundle['trial_manifest_sha256']
        and definition['source']['commit'] == bundle['archive_source_commit'], 'BAR_WORKER_DEFINITION_BINDING')
    require(protected_tree(source, uid) == facts and _read(observation, 'result.json') == raw_result
        and _read(trial, 'manifest.json') == raw_manifest, 'BAR_WORKER_INPUT_CHANGED')
    return engine, dict(definition_hash=definition['definition_hash'], observation_sha256=bundle['observation_sha256'],
        trial_manifest_sha256=bundle['trial_manifest_sha256'], archive_source_commit=bundle['archive_source_commit'])
