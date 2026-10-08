"""Prepare a reviewed strategy input package in a new local output directory.

Copies only the original research/observation allowlist. This is not protected
installation, prospective registration, independent admission or execution.
The requested deployment commit is a target, not a certified source identity.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re

from live.execution.demo_batch_risk import _digest
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_cost_review import _read
from tools.gold_forward_review import review
from tools.gold_strategy_worker import strategy_inputs
from tools.gold_execution_identity import PreparedStrategy

OBSERVATION_FILES = ('registration.json', 'events.jsonl', 'decisions.jsonl', 'result.json')
TRIAL_FILES = ('input.json', 'report.json', 'manifest.json', 'research-origin.json', 'trials.db', 'strategy-definition.json')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_STRATEGY_PUBLICATION_'+reason)


def paths(runtime):
    return {**{f"strategy-observations/{runtime['observation_sha256']}/{name}": ('observation', name)
               for name in OBSERVATION_FILES},
            **{f"strategy-trials/{runtime['trial_manifest_sha256']}/{name}": ('trial', name)
               for name in TRIAL_FILES}}


def verify(directory, *, prepared_strategy=None):
    root = Path(directory).absolute()
    manifest = decode(_read(root, 'strategy-package.json'))
    require(type(manifest) is dict and manifest.get('schema') == 'GOLD_STRATEGY_INPUT_PACKAGE_V1'
        and manifest.get('result_hash') == _digest({k:v for k,v in manifest.items() if k != 'result_hash'})
        and all(manifest.get(k) is False for k in ('installed', 'submission_enabled', 'economic_authority',
                                                  'source_commit_binding_verified')), 'MANIFEST')
    runtime = manifest['runtime']
    # The original worker validates IDs before any file map is traversed.
    _, _, checked = strategy_inputs(root, runtime, source_commit=runtime['source_commit'], uid=root.stat().st_uid,
                                    prepared_strategy=prepared_strategy)
    require(checked == manifest['review'], 'REVIEW_CHANGED')
    expected = set(paths(runtime)) | {'strategy-runtime.json'}
    require(set(manifest['files']) == expected, 'FILE_SCOPE')
    for name in expected:
        raw = _read(root, name)
        require(hashlib.sha256(raw).hexdigest() == manifest['files'][name], 'FILE_CHANGED')
    require(decode(_read(root, 'strategy-runtime.json')) == runtime, 'RUNTIME_CHANGED')
    return manifest


def publish(observation_directory, *, strategy_trial, output_directory, source_commit, prepared_strategy=None):
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}', source_commit) is not None, 'SOURCE_TARGET')
    observation, trial = Path(observation_directory).absolute(), Path(strategy_trial).absolute()
    for suffix in ('-wal', '-shm', '-journal'):
        path = trial/('trials.db'+suffix)
        require(not path.exists() and not path.is_symlink(), 'TRIAL_NOT_FROZEN')
    prepared = {'prepared_strategy': prepared_strategy} if prepared_strategy is not None else {}
    checked = review(observation, strategy_trial=trial, **prepared)
    runtime = dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1', source_commit=source_commit,
        observation_sha256=checked['files']['result.json'],
        trial_manifest_sha256=checked['research_identity']['manifest_sha256'])
    source = {'observation': observation, 'trial': trial}
    mapping = paths(runtime)
    captured = {name: _read(source[kind], leaf) for name,(kind,leaf) in mapping.items()}
    target = Path(output_directory).absolute()
    require(target.parent.resolve() == target.parent and not target.is_symlink(), 'OUTPUT_PATH')
    if target.exists():
        existing = verify(target, **prepared)
        require(existing['runtime'] == runtime and existing['review'] == checked
            and all(existing['files'][name] == hashlib.sha256(raw).hexdigest() for name,raw in captured.items()),
            'EXISTING_PACKAGE_CONFLICT')
        return existing
    target.mkdir(mode=0o700)
    payloads = {**captured, 'strategy-runtime.json': encoded(runtime)}
    for name, raw in payloads.items():
        path = target/name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # pathlib's intermediate parents use the process umask; explicitly
        # make both content namespaces private before publication is complete.
        for parent in path.parents:
            if parent == target: break
            parent.chmod(0o700)
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    require(strategy_inputs(target, runtime, source_commit=source_commit, uid=target.stat().st_uid, **prepared)[2] == checked,
            'COPIED_REVIEW_CHANGED')
    require(review(observation, strategy_trial=trial, **prepared) == checked
        and all(_read(source[kind], leaf) == captured[name] for name,(kind,leaf) in mapping.items()), 'SOURCE_CHANGED')
    body = dict(schema='GOLD_STRATEGY_INPUT_PACKAGE_V1', runtime=runtime, review=checked,
        files={name:hashlib.sha256(raw).hexdigest() for name,raw in payloads.items()},
        installed=False, submission_enabled=False, economic_authority=False, source_commit_binding_verified=False)
    manifest = {**body, 'result_hash': _digest(body)}
    # The final manifest marks completion. Interrupted output is preserved and
    # rejected on reuse; it is never silently repaired or activated.
    fd = os.open(target/'strategy-package.json', os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded(manifest)); stream.flush(); os.fsync(stream.fileno())
    for directory in sorted({target, *(p.parent for p in target.rglob('*'))}, key=lambda p:len(p.parts), reverse=True):
        fd = os.open(directory, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
    return verify(target, **prepared)


async def observe_and_publish(plan, *, observation_directory, strategy_trial, output_directory, source_commit):
    """One bounded original observation followed by automatic input publication."""
    from tools.gold_forward_observe import observe
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}', source_commit) is not None, 'SOURCE_TARGET')
    target = Path(output_directory).absolute()
    require(not target.exists() and not target.is_symlink() and target.parent.resolve() == target.parent,
            'NEW_OUTPUT_REQUIRED')
    prepared = PreparedStrategy(strategy_trial)
    observed = await observe(plan, Path(observation_directory), strategy_trial=strategy_trial, prepared_strategy=prepared)
    result = dict(schema='GOLD_STRATEGY_FORWARD_PUBLICATION_V1', status='OBSERVATION_INCOMPLETE',
        observation_status=observed['status'], package_sha256=None, installed=False, submission_enabled=False)
    if observed['status'] != 'COMPLETE_OBSERVATION':
        return result
    package = publish(observation_directory, strategy_trial=strategy_trial,
                      output_directory=target, source_commit=source_commit, prepared_strategy=prepared)
    result.update(status='INPUT_PACKAGE_PREPARED', package_sha256=package['result_hash'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation-dir', type=Path, required=True)
    parser.add_argument('--strategy-trial', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--plan', type=Path, help='Run the original bounded observation before publication')
    args = parser.parse_args(argv)
    if args.plan is not None:
        from tools.gold_forward_observe import load_document
        plan_path = args.plan.absolute()
        result = asyncio.run(observe_and_publish(load_document(_read(plan_path.parent, plan_path.name)),
            observation_directory=args.observation_dir, strategy_trial=args.strategy_trial,
            output_directory=args.output_dir, source_commit=args.source_commit))
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] == 'INPUT_PACKAGE_PREPARED' else 2
    result = publish(args.observation_dir, strategy_trial=args.strategy_trial,
                     output_directory=args.output_dir, source_commit=args.source_commit)
    print(json.dumps({'status':'INPUT_PACKAGE_PREPARED', 'result_hash':result['result_hash'],
                      'installed':False, 'submission_enabled':False}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
