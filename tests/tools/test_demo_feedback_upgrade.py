"""A feedback upgrade review binds complete materials and observed preimages."""
from copy import deepcopy

import pytest

from scripts.render_demo_feedback_units import render
from tools import demo_feedback_upgrade as upgrade


def materials(commit='a' * 40):
    return {name: body.encode() for name, body in render(
        release_commit=commit, research_state_root='/srv/research/state',
        executor_uid=2001, research_uid=2003, research_gid=2004,
    ).items()}


def test_first_install_review_requires_explicit_absence_for_every_target():
    files = materials()
    before = dict.fromkeys(files)
    review = upgrade.review(files=files, preimages=before, release_commit='a' * 40)
    assert not review['installed'] and not review['activation_authorized']
    assert review['preimages_verified'] is False
    upgrade.recheck(review, files=files, preimages=before)
    before['research-feedback.json'] = b'concurrent configuration'
    with pytest.raises(ValueError, match='FEEDBACK_UPGRADE_PREIMAGE_CHANGED'):
        upgrade.recheck(review, files=files, preimages=before)


def test_existing_review_preserves_exact_before_and_after_identities():
    before, files = materials('b' * 40), materials()
    original = deepcopy(before)
    review = upgrade.review(files=files, preimages=before, release_commit='a' * 40)
    upgrade.recheck(review, files=files, preimages=before)
    assert before == original
    assert all(row['before_sha256'] != row['after_sha256'] for name, row in review['targets'].items()
               if name != 'quantos-demo-feedback.timer')
    assert len(review['targets']) == 4


@pytest.mark.parametrize('damage', ['missing_profile', 'mixed_release', 'extra_path', 'order_command', 'wrong_uid'])
def test_incoherent_or_expanded_payload_is_rejected(damage):
    files = materials()
    before = dict.fromkeys(files)
    if damage == 'missing_profile':
        del files['research-feedback.json']
    elif damage == 'mixed_release':
        files['feedback-automation.json'] = materials('b' * 40)['feedback-automation.json']
    elif damage == 'extra_path':
        files['signing-key.json'] = b'not a deployment target'
    elif damage == 'order_command':
        files['quantos-demo-feedback.service'] = files['quantos-demo-feedback.service'].replace(
            b'--feedback-pending', b'--resume')
    else:
        files['research-feedback.json'] = files['research-feedback.json'].replace(b'2003', b'2001')
    with pytest.raises(ValueError):
        upgrade.review(files=files, preimages=before, release_commit='a' * 40)


def test_unknown_preimages_are_not_assumed_absent():
    with pytest.raises(ValueError, match='FEEDBACK_UPGRADE_PREIMAGES'):
        upgrade.review(files=materials(), preimages={}, release_commit='a' * 40)


def test_modified_review_cannot_change_destination_or_claim_authority():
    files = materials()
    before = dict.fromkeys(files)
    original = upgrade.review(files=files, preimages=before, release_commit='a' * 40)
    for field, value in [('installed', True), ('activation_authorized', True),
                         ('preimages_verified', True), ('release_commit', 'b' * 40)]:
        changed = deepcopy(original)
        changed[field] = value
        with pytest.raises(ValueError):
            upgrade.recheck(changed, files=files, preimages=before)


def test_reviewed_payload_cannot_change_before_installation():
    files = materials()
    before = dict.fromkeys(files)
    review = upgrade.review(files=files, preimages=before, release_commit='a' * 40)
    changed = materials('b' * 40)
    with pytest.raises(ValueError):
        upgrade.recheck(review, files=changed, preimages=before)


def test_renderer_emits_upgrade_review_bound_to_supplied_previous_directory(tmp_path, capsys):
    import json
    from scripts.render_demo_feedback_units import main
    previous = tmp_path / 'previous'
    previous.mkdir()
    before = materials('b' * 40)
    for name, body in before.items():
        (previous / name).write_bytes(body)
    output = tmp_path / 'output'
    assert main(['--release-commit', 'a' * 40, '--research-state-root', '/srv/research/state',
                 '--executor-uid', '2001', '--research-uid', '2003', '--research-gid', '2004',
                 '--upgrade-preimages-dir', str(previous), '--output-dir', str(output)]) == 0
    capsys.readouterr()
    review = json.loads((output / 'upgrade-review.json').read_text())
    upgrade.recheck(review, files={name: (output / name).read_bytes() for name in before},
                    preimages=before)


@pytest.mark.parametrize('kind', ['missing_directory', 'symlink', 'oversize'])
def test_renderer_rejects_unreadable_or_unsafe_preimage_before_output(tmp_path, kind):
    from scripts.render_demo_feedback_units import main
    previous = tmp_path / 'previous'
    if kind != 'missing_directory':
        previous.mkdir()
        target = previous / 'research-feedback.json'
        if kind == 'symlink':
            target.symlink_to(tmp_path / 'absent')
        else:
            target.write_bytes(b'x' * 65537)
    output = tmp_path / 'output'
    with pytest.raises((ValueError, OSError)):
        main(['--release-commit', 'a' * 40, '--research-state-root', '/srv/research/state',
              '--executor-uid', '2001', '--research-uid', '2003', '--research-gid', '2004',
              '--upgrade-preimages-dir', str(previous), '--output-dir', str(output)])
    assert not output.exists()
