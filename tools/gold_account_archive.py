"""Own a historical account prefix and register its technical reproduction.

This is a retrospective reproduction trial, not a preregistered strategy test.
It uses the existing TrialLedger protocol in a separate research archive.
"""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from services.execution.backtest_authority import exact_identity_hash
from services.research.trial_ledger import TrialLedger, TrialRecord
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_account_chain import _history
from tools.gold_account_review import review_chain
from tools.gold_session_io import read as _read
from tools.perpetual_replay_research import source_identity


def _save(path, value):
    raw = (json.dumps(value, sort_keys=True, allow_nan=False) + '\n').encode()
    with path.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return exact_identity_hash(value, name='gold_account_reproduction_trial')


def archive_review(chain, destination):
    root, dest = Path(chain).absolute(), Path(destination).absolute()
    if root == dest or root in dest.parents or dest in root.parents:
        raise ValueError('ACCOUNT_ARCHIVE_OVERLAPPING_PATHS')
    if root.resolve() != root or dest.resolve() != dest:
        raise ValueError('ACCOUNT_ARCHIVE_UNSAFE_PATH')
    directories, head = _history(root)
    if not directories:
        raise ValueError('ACCOUNT_ARCHIVE_EMPTY')
    source = source_identity()
    dest.mkdir(mode=0o700, parents=True, exist_ok=False)
    frozen = dest/'inputs'
    frozen.mkdir(mode=0o700)
    size = 0
    for directory in directories:
        target = frozen/directory.name
        target.mkdir(mode=0o700)
        transition = _read(directory, 'transition.json')
        row = decode(transition)
        bodies = {name: _read(directory, name) for name in row['files']}
        if any(hashlib.sha256(raw).hexdigest() != row['files'][name]
               for name, raw in bodies.items()):
            raise ValueError('ACCOUNT_ARCHIVE_INPUT_CHANGED')
        bodies['transition.json'] = transition
        size += sum(map(len, bodies.values()))
        if size > 256 * 1024 * 1024:
            raise ValueError('ACCOUNT_ARCHIVE_BYTE_BUDGET')
        for name, raw in bodies.items():
            with (target/name).open('xb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        if any(_read(directory, name) != raw for name, raw in bodies.items()):
            raise ValueError('ACCOUNT_ARCHIVE_INPUT_CHANGED')
    _, copied_head = _history(frozen)
    if copied_head != head:
        raise ValueError('ACCOUNT_ARCHIVE_HEAD_CHANGED')
    policy = dict(mode='RETROSPECTIVE', success='SAME_ENGINE_REPRODUCTION_ONLY',
                  automatic_selection=False, prefix_returns_aggregated=False)
    definition = _digest(dict(reviewed_head=head, reviewer_source=source))
    identity = dict(trial_id=_digest(dict(definition=definition, invocation=uuid.uuid4().hex)),
        alpha_id=definition, exact_definition_id=definition,
        family_id=_digest('GOLD_ACCOUNT_TECHNICAL_REPRODUCTION'),
        validation_policy_digest=_digest(policy))
    manifest = dict(schema='GOLD_ACCOUNT_REPRODUCTION_ARCHIVE_V1', source=source,
        reviewed_head=head, copied_transitions=len(directories), copied_bytes=size,
        trial_id=identity['trial_id'], identity=identity, policy=policy,
        preregistered=False, producer_status='NOT_ASSESSED_BY_PREFIX_REPRODUCTION',
        economic_eligible=False, authoritative=False, submission_enabled=False)
    _save(dest/'running.json', manifest)
    with TrialLedger(str(dest/'trials.db')) as ledger:
        for index, status in enumerate(('PLANNED', 'RUNNING')):
            ledger.append(TrialRecord(**identity, event_index=index, status=status,
                                      at=time.time_ns() // 1000000))
        try:
            result = review_chain(frozen)
            if source_identity() != source or result['reviewed_head'] != head:
                raise ValueError('ACCOUNT_ARCHIVE_REVIEW_SOURCE_OR_HEAD_CHANGED')
            manifest['review_sha256'] = _save(dest/'review.json', result)
        except BaseException as exc:
            ledger.append(TrialRecord(**identity, event_index=2, status='FAILED',
                                      at=time.time_ns() // 1000000))
            _save(dest/'manifest.json', dict(manifest, terminal='FAILED', error_type=type(exc).__name__))
            raise
        ledger.append(TrialRecord(**identity, event_index=2, status='PASSED',
                                  at=time.time_ns() // 1000000))
        _save(dest/'manifest.json', dict(manifest, terminal='PASSED'))
    return dict(status='TECHNICAL_REPRODUCTION_REGISTERED', trial_id=identity['trial_id'],
        reviewed_head=head, result_hash=result['result_hash'], preregistered=False,
        independent_review_status=result['independent_review_status'],
        economic_eligible=False, submission_enabled=False)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chain', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(archive_review(args.chain, args.output)), flush=True)


if __name__ == '__main__':
    main()
