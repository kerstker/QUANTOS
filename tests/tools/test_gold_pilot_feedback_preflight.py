"""Original pilot installation consumes a protected feedback preflight.

All profiles, state and research directories are temporary fixtures. Source,
operator and protected ownership are synthetic; original configuration parsing,
destination custody, lease, exclusive install writes and readback stay native.
"""

from copy import deepcopy
import json
import os

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_pilot_install import (
    case as case, proposal as proposal, installation as installation,
)
from tools import demo_feedback_publish as feedback


def profile():
    return json.loads(feedback.PROFILE.read_bytes())


def change_profile(**changes):
    value = profile() | changes
    feedback.PROFILE.write_text(json.dumps(value))
    return value


def assert_uninstalled(installation):
    tool, _, _, _, controller, executor, _ = installation
    assert not tool.UNIT.exists()
    assert not (executor / 'gold-pilot-request.json').exists()
    assert not (executor / 'gold-pilot').exists()
    assert not (controller / 'tasks').exists()


def publisher_directories(root, leaf):
    path = root
    for name in ('data', 'cache', 'ai_factory', leaf):
        path = path / name
        path.mkdir(mode=0o700)
    return path


def test_review_binds_actual_profile_before_confirmation_without_publication(installation, tmp_path, monkeypatch):
    tool, args, registry, policy, _, _, _ = installation
    for name in ('read_export', 'publish', 'run'):
        monkeypatch.setattr(feedback, name, lambda *a, **kw: pytest.fail('preflight attempted publication'))
    monkeypatch.setattr(feedback.custody, 'drop_identity', lambda *a: pytest.fail('preflight dropped identity'))
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    candidate = tool.review(**args)
    checked = candidate['feedback_preflight']
    assert checked['configuration'] == profile()
    assert checked['configuration_sha256'] == _digest(profile())
    assert checked['configuration']['executor_uid'] == registry['executor_uid']
    assert checked['configuration']['research_uid'] in registry['research_uids']
    assert candidate['account_ref_sha256'] == policy['account_ref_sha256']
    assert checked['delivery_verified'] is False and checked['submission_enabled'] is False
    assert checked['destination_directories']
    assert {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == before
    assert_uninstalled(installation)


@pytest.mark.parametrize('damage', [
    'missing', 'source', 'executor', 'research', 'issuer_gid', 'root', 'extra_account',
    'malformed', 'writable', 'symlink', 'hardlink',
])
def test_invalid_fixed_profile_is_rejected_before_confirmation_or_audit(installation, tmp_path, monkeypatch, damage):
    tool, args, registry, _, controller, _, _ = installation
    if damage == 'missing':
        feedback.PROFILE.unlink()
    elif damage == 'source':
        change_profile(source_commit='d' * 40)
    elif damage == 'executor':
        change_profile(executor_uid=registry['executor_uid'] + 10)
    elif damage == 'research':
        change_profile(research_uid=registry['research_uids'][0] + 100)
    elif damage == 'issuer_gid':
        change_profile(research_gid=registry['issuer_uid'])
    elif damage == 'root':
        change_profile(research_state_root='/home/other/research/state')
    elif damage == 'extra_account':
        change_profile(account_ref_sha256='e' * 64)
    elif damage == 'malformed':
        feedback.PROFILE.write_text('{')
    elif damage == 'writable':
        feedback.PROFILE.chmod(0o666)
    elif damage == 'symlink':
        target = tmp_path / 'other-profile.json'
        feedback.PROFILE.rename(target)
        feedback.PROFILE.symlink_to(target)
    else:
        os.link(feedback.PROFILE, tmp_path / 'profile-alias.json')
    monkeypatch.setattr(tool, 'confirmation', lambda *a: pytest.fail('invalid feedback reached human confirmation'))
    with pytest.raises((ValueError, OSError)):
        tool.install_approved(**args)
    assert_uninstalled(installation)
    assert not (controller / 'gold-pilot-installations').exists()


@pytest.mark.parametrize('damage', ['missing_root', 'not_writable', 'writable_by_others',
                                     'child_symlink', 'child_file', 'child_mode', 'wrong_owner'])
def test_unusable_destination_fails_before_installation(installation, tmp_path, monkeypatch, damage):
    tool, args, _, _, controller, _, _ = installation
    root = tmp_path / 'feedback-research' / 'state'
    if damage == 'missing_root':
        root.rmdir()
    elif damage == 'not_writable':
        root.chmod(0o500)
    elif damage == 'writable_by_others':
        root.chmod(0o777)
    elif damage == 'child_symlink':
        elsewhere = tmp_path / 'elsewhere'
        elsewhere.mkdir()
        (root / 'data').symlink_to(elsewhere)
    elif damage == 'child_file':
        (root / 'data').write_bytes(b'not a directory')
    elif damage == 'child_mode':
        (root / 'data').mkdir(mode=0o500)
    else:
        original = feedback.custody._owned
        def wrong_owner(info, uid, **kwargs):
            return original(info, uid + 1, **kwargs)
        monkeypatch.setattr(feedback.custody, '_owned', wrong_owner)
    with pytest.raises((ValueError, OSError)):
        tool.review(**args)
    assert_uninstalled(installation)
    assert not (controller / 'gold-pilot-installations').exists()


@pytest.mark.parametrize('moment', ['confirmation', 'staging', 'target_write'])
def test_changed_profile_never_reports_success_and_preserves_partial_audit(installation, monkeypatch, moment):
    tool, args, _, _, controller, executor, _ = installation
    original = deepcopy(profile())
    seen = []
    if moment == 'confirmation':
        confirm = tool.confirmation
        def changing(candidate, operator):
            seen.append(candidate)
            answer = confirm(candidate, operator)
            change_profile(research_gid=original['research_gid'] + 100)
            return answer
        monkeypatch.setattr(tool, 'confirmation', changing)
    else:
        write = tool.installer.write_new
        def changing(path, body, **kwargs):
            write(path, body, **kwargs)
            if (moment == 'staging' and path.name == 'operator.json'
                    or moment == 'target_write' and path == tool.UNIT):
                change_profile(research_gid=original['research_gid'] + 100)
        monkeypatch.setattr(tool.installer, 'write_new', changing)
    with pytest.raises(ValueError) as error:
        tool.install_approved(**args)
    if moment == 'confirmation':
        assert seen[0]['feedback_preflight']['configuration'] == original
        assert not (controller / 'gold-pilot-installations').exists()
        assert_uninstalled(installation)
    else:
        assert isinstance(error.value, tool.InstallIncomplete)
        assert error.value.result['installed'] is None
        assert error.value.result['failure_record_retained'] is True
        assert len(list((controller / 'gold-pilot-installations').glob('*/failure.json'))) == 1
        assert not list((controller / 'gold-pilot-installations').glob('*/result.json'))
        assert tool.UNIT.exists() is (moment == 'target_write')
        assert (executor / 'gold-pilot-request.json').exists() is (moment == 'target_write')


def test_replaced_research_directory_after_confirmation_is_rejected(installation, tmp_path, monkeypatch):
    tool, args, _, _, controller, _, _ = installation
    root = tmp_path / 'feedback-research' / 'state'
    confirm = tool.confirmation
    def replace(candidate, operator):
        result = confirm(candidate, operator)
        root.rename(root.with_name('original-state'))
        root.mkdir(mode=0o700)
        return result
    monkeypatch.setattr(tool, 'confirmation', replace)
    with pytest.raises(ValueError, match='INPUTS_CHANGED'):
        tool.install_approved(**args)
    assert_uninstalled(installation)
    assert not (controller / 'gold-pilot-installations').exists()


def test_profile_changed_during_destination_review_is_rejected(installation, monkeypatch):
    tool, args, *_ = installation
    original = feedback.configuration
    calls = []
    def changed(source_commit):
        config = original(source_commit)
        calls.append(config)
        if len(calls) == 1:
            change_profile(research_gid=config['research_gid'] + 100)
        return config
    monkeypatch.setattr(feedback, 'configuration', changed)
    with pytest.raises(ValueError, match='FEEDBACK_CHANGED'):
        tool.review(**args)
    assert len(calls) == 2
    assert_uninstalled(installation)


@pytest.mark.parametrize('bar', [False, True])
def test_existing_publisher_directories_are_bound_without_content_reads(installation, tmp_path, bar):
    tool, args, _, _, controller, _, _ = installation
    if bar:
        path = controller / 'strategy-runtime.json'
        runtime = json.loads(path.read_bytes())
        runtime.update(schema='GOLD_BAR_RETAINED_EPISODE_INPUT_V1', archive_source_commit='d' * 40,
                       entry_decision_ms=1)
        path.write_text(json.dumps(runtime))
    root = tmp_path / 'feedback-research' / 'state'
    leaf = 'bar-execution-feedback' if bar else 'execution-feedback'
    destination = publisher_directories(root, leaf)
    # No evidence body is needed or read by the installation preflight.
    (destination / 'unrelated.json').write_bytes(b'not a feedback input')
    result = tool.review(**args)['feedback_preflight']
    assert [row['path'] for row in result['destination_directories']] == [
        '.', 'data', 'data/cache', 'data/cache/ai_factory',
        'data/cache/ai_factory/' + leaf,
    ]
    assert result['bar'] is bar
    assert result['delivery_verified'] is False
    assert_uninstalled(installation)


def test_legacy_runtime_checks_its_original_feedback_destination(installation, tmp_path):
    tool, args, *_ = installation
    destination = publisher_directories(tmp_path / 'feedback-research/state', 'execution-feedback')
    destination.rmdir()
    elsewhere = tmp_path / 'legacy-feedback-elsewhere'
    elsewhere.mkdir()
    destination.symlink_to(elsewhere)
    with pytest.raises((ValueError, OSError)):
        tool.review(**args)
    assert_uninstalled(installation)


def test_original_installer_retains_preflight_and_keeps_feedback_unchanged(installation):
    tool, args, _, _, controller, _, _ = installation
    original = feedback.PROFILE.read_bytes()
    candidate = tool.review(**args)
    result = tool.install_approved(**args)
    assert result['status'] == 'PILOT_INSTALLED_NOT_STARTED'
    stage = controller / 'gold-pilot-installations' / result['candidate_sha256']
    saved = json.loads((stage / 'candidate.json').read_bytes())
    assert saved == candidate and saved['feedback_preflight']['configuration'] == profile()
    assert json.loads((stage / 'feedback-configuration.json').read_bytes()) == profile()
    assert feedback.PROFILE.read_bytes() == original
    assert result['started'] is False and result['orders_submitted'] is False
