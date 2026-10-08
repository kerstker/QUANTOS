"""Protected pilot handoff, using temporary files and explicit operator fixtures."""
import contextlib
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path

import pytest

from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.tools.test_gold_strategy_policy_install import candidate_for
from live.execution.demo_batch_risk import _digest
from services.validation.demo_submission import registry_sha256

SOURCE = 'c' * 40
STATE_ROOT = '/home/operator/gold-research/state'
DAY = 86_400_000


def bind_feedback(tool, args, registry, tmp_path, monkeypatch):
    """Map only the declared research path and protected profile to fixtures."""
    from tools import demo_feedback_publish as feedback
    root = tmp_path / 'feedback-research' / 'state'
    root.mkdir(parents=True, mode=0o700)
    profile = tmp_path / 'feedback-profile.json'
    cfg = dict(schema='DEMO_RESEARCH_FEEDBACK_PUBLICATION_V1', source_commit=SOURCE,
        executor_uid=registry['executor_uid'], research_uid=registry['research_uids'][0],
        research_gid=registry['research_uids'][0], research_state_root=args['research_state_root'])
    profile.write_text(json.dumps(cfg)); profile.chmod(0o600)
    original = getattr(tool, '_feedback_destination', None)

    def destination(path, *, uid, gid, bar):
        assert path == Path(args['research_state_root'])
        assert original is not None
        assert uid == registry['research_uids'][0]
        # Registered identities are synthetic; the real descriptor/custody
        # checks run against the temporary directory's unprivileged owner.
        return original(root, uid=os.getuid(), gid=os.getgid(), bar=bar)

    read_target = tool._read_target
    def read(path):
        assert path == profile
        path.relative_to(tmp_path)
        return read_target(path, uid=os.getuid())

    monkeypatch.setattr(feedback, 'PROFILE', profile)
    monkeypatch.setattr(feedback.custody, '_read_root_owned_json', read)
    monkeypatch.setattr(tool, '_feedback_destination', destination, raising=False)


@pytest.fixture
def proposal(case, tmp_path):
    from scripts.render_gold_pilot_unit import main
    _, policy, registry = candidate_for(case)
    until = NOW + DAY + 60_000
    for key in registry['keys']:
        key['valid_until_ms'] = until
    policy['valid_until_ms'] = until
    policy['budget_group']['valid_until_ms'] = until
    policy['registry_sha256'] = registry_sha256(registry)
    bundle = tmp_path / 'bundle'
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(['--release-commit', SOURCE, '--account-ref-sha256', policy['account_ref_sha256'],
            '--research-state-root', STATE_ROOT, '--execute-approved', '--output-dir', str(bundle)]) == 0
    review_sha = hashlib.sha256((bundle / 'review.json').read_bytes()).hexdigest()
    return dict(source_commit=SOURCE, bundle_dir=bundle, review_sha256=review_sha,
                research_state_root=STATE_ROOT), registry, policy


def bind_review(proposal, tmp_path, monkeypatch):
    from tools import gold_pilot_install as tool
    args, registry, policy = proposal
    controller, executor, units = (tmp_path / name for name in ('controller', 'executor', 'units'))
    for directory in (controller, executor, units): directory.mkdir(mode=0o700)
    runtime = dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1', source_commit=SOURCE,
        observation_sha256='a' * 64, trial_manifest_sha256='b' * 64)
    (controller / 'strategy-runtime.json').write_text(json.dumps(runtime))
    monkeypatch.setattr(tool.supervisor, 'STATE', controller)
    monkeypatch.setattr(tool.installer, 'EXECUTOR', executor)
    monkeypatch.setattr(tool, 'UNIT', units / 'quantos-gold-pilot.service')
    monkeypatch.setattr(tool.supervisor, 'configuration', lambda source: (deepcopy(registry), deepcopy(policy)))
    monkeypatch.setattr(tool.supervisor, 'now_ms', lambda: NOW)
    monkeypatch.setattr(tool.supervisor, '_read_root_owned_json', lambda p: p.read_bytes())
    monkeypatch.setattr(tool.installer, 'read_private', lambda p, **k: p.read_bytes())
    monkeypatch.setattr(tool.supervisor, '_root_private_directory', lambda p: None)
    monkeypatch.setattr(tool, '_protected_targets', lambda registry: None)
    monkeypatch.setattr(tool, '_unit_absent', lambda: None)
    bind_feedback(tool, args, registry, tmp_path, monkeypatch)
    return tool, args, registry, policy, controller, executor, units


def test_default_review_validates_original_render_without_writing(proposal, tmp_path, monkeypatch):
    tool, args, _, _, controller, executor, units = bind_review(proposal, tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = tool.review(**args)
    assert result['status'] == 'PILOT_INSTALL_REQUIRES_LOCAL_OPERATOR'
    assert result['duration_seconds'] == 86400 and result['max_loss_fraction'] == '0.05'
    assert result['installed'] is False and result['started'] is False
    assert result['remaining_budget_verified'] is False and result['economic_authority'] is False
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_rehashed_service_injection_is_rejected(proposal, tmp_path, monkeypatch):
    tool, args, *_ = bind_review(proposal, tmp_path, monkeypatch)
    path = args['bundle_dir'] / 'quantos-gold-pilot.service'
    path.write_bytes(path.read_bytes() + b'\nExecStartPost=/bin/unsafe\n')
    review = json.loads((args['bundle_dir'] / 'review.json').read_bytes())
    review['files'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (args['bundle_dir'] / 'review.json').write_text(json.dumps(review))
    args['review_sha256'] = hashlib.sha256((args['bundle_dir'] / 'review.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError): tool.review(**args)


def test_short_approval_never_becomes_a_24_hour_pilot(proposal, tmp_path, monkeypatch):
    tool, args, registry, policy, *_ = bind_review(proposal, tmp_path, monkeypatch)
    policy['valid_until_ms'] = NOW + 3_600_000
    policy['budget_group']['valid_until_ms'] = policy['valid_until_ms']
    with pytest.raises(ValueError, match='AUTHORIZATION_WINDOW'):
        tool.review(**args)


@pytest.fixture
def installation(proposal, tmp_path, monkeypatch):
    tool, args, registry, policy, controller, executor, units = bind_review(proposal, tmp_path, monkeypatch)
    # Virtual operator/custody only. Original exclusive writes, fsync and lease remain real.
    monkeypatch.setattr(tool, 'local_operator', lambda: 'isolated-operator')
    monkeypatch.setattr('builtins.input', lambda prompt: prompt.removeprefix('请输入 ').strip())
    monkeypatch.setattr(tool.installer.os, 'chown', lambda *a: None)
    monkeypatch.setattr(tool.installer.os, 'fchown', lambda *a: None)
    monkeypatch.setattr(tool, '_check_path', lambda *a, **k: None)
    original = tool._target_metadata
    monkeypatch.setattr(tool, '_target_metadata', lambda info, *, uid, maximum:
        original(info, uid=info.st_uid, maximum=maximum))
    return tool, args, registry, policy, controller, executor, units


def test_install_handoff_is_consumed_by_original_enrollment_without_reset(installation, tmp_path, monkeypatch):
    tool, args, registry, policy, controller, executor, units = installation
    result = tool.install_approved(**args)
    assert result['status'] == 'PILOT_INSTALLED_NOT_STARTED'
    assert result['installed'] is True and result['started'] is False and result['orders_submitted'] is False
    assert tool.UNIT.read_bytes() == (args['bundle_dir'] / tool.UNIT.name).read_bytes()
    stage = controller / 'gold-pilot-installations' / result['candidate_sha256']
    assert json.loads((stage / 'policy.json').read_bytes()) == policy
    assert json.loads((stage / 'registry.json').read_bytes()) == registry
    assert json.loads((stage / 'result.json').read_bytes()) == result
    from live.execution import demo_gold_pilot_enrollment as enrollment, demo_position_context as position
    from tests.tools.test_gold_demo_execution_origin import capture
    from tests.live.test_demo_pre_submit import stores
    origin, _ = capture()
    assert origin['account_ref_sha256'] == policy['account_ref_sha256']
    ds, recovery, oms = stores(executor)
    monkeypatch.setattr(position, '_ROOT', executor)
    monkeypatch.setattr(position, '_custody', lambda *a, **k: None)
    kwargs = dict(root=executor, source_commit=SOURCE, origin=origin,
        dispatch=ds, recovery=recovery, oms=oms)
    try:
        first = enrollment.enroll_requested(**kwargs, now_ms=NOW)
        assert first['initial_equity'] == '5000' and first['ends_at_ms'] == NOW + DAY
        assert enrollment.enroll_requested(**kwargs, now_ms=NOW + 1) == first
        with pytest.raises(ValueError, match='EXISTING_OR_PARTIAL'):
            tool.install_approved(**args)
        assert json.loads((executor / 'gold-pilot' / 'configuration.json').read_bytes()) == first
        assert ds.snapshot() == [] and recovery.latest_records() == () and oms.orders() == []
    finally:
        for store in (ds, recovery, oms): store.close()


def test_cli_default_never_installs_or_confirms(proposal, tmp_path, monkeypatch, capsys):
    tool, args, *_ = bind_review(proposal, tmp_path, monkeypatch)
    monkeypatch.setattr(tool, 'install_approved', lambda **k: pytest.fail('default command installed'))
    monkeypatch.setattr(tool, 'confirmation', lambda *a: pytest.fail('default command prompted'))
    assert tool.main(['--source-commit', SOURCE, '--bundle-dir', str(args['bundle_dir']),
        '--review-sha256', args['review_sha256'], '--research-state-root', STATE_ROOT]) == 0
    assert json.loads(capsys.readouterr().out)['installed'] is False


@pytest.mark.parametrize('gate', ['nonroot', 'non_tty', 'no_sudo', 'wrong_operator'])
def test_operator_gate_precedes_protected_reads(proposal, monkeypatch, gate):
    from tools import gold_pilot_install as tool
    from tools import gold_strategy_policy_install as original
    from types import SimpleNamespace
    args, *_ = proposal
    monkeypatch.setattr(original.os, 'geteuid', lambda: 1 if gate == 'nonroot' else 0)
    monkeypatch.setattr(original.sys.stdin, 'isatty', lambda: gate != 'non_tty')
    monkeypatch.setattr(original.sys.stdout, 'isatty', lambda: gate != 'non_tty')
    monkeypatch.setenv('SUDO_UID', '' if gate == 'no_sudo' else '7000')
    monkeypatch.setenv('SUDO_USER', 'mismatch' if gate == 'wrong_operator' else 'isolated-operator')
    monkeypatch.setattr(original.pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_name='isolated-operator'))
    monkeypatch.setattr(tool, 'review', lambda **k: pytest.fail('read protected inputs before operator gate'))
    with pytest.raises(ValueError): tool.install_approved(**args)


def test_existing_shared_controller_work_blocks_installation(installation):
    tool, args, *_ = installation
    with tool.supervisor.controller_lease():
        with pytest.raises(ValueError, match='MAINTENANCE_BUSY'):
            tool.install_approved(**args)


def test_unconfirmed_install_does_not_create_audit_or_targets(installation, monkeypatch):
    tool, args, _, _, controller, executor, _ = installation
    monkeypatch.setattr('builtins.input', lambda prompt: 'decline')
    with pytest.raises(ValueError, match='NOT_CONFIRMED'): tool.install_approved(**args)
    assert not tool.UNIT.exists() and not (executor / 'gold-pilot-request.json').exists()
    assert not (controller / 'gold-pilot-installations').exists()


def test_changed_runtime_after_prompt_is_rejected_before_writes(installation, monkeypatch):
    tool, args, _, _, controller, executor, _ = installation
    def confirm(prompt):
        path = controller / 'strategy-runtime.json'
        data = json.loads(path.read_bytes()); data['trial_manifest_sha256'] = 'e' * 64
        path.write_text(json.dumps(data))
        return prompt.removeprefix('请输入 ').strip()
    monkeypatch.setattr('builtins.input', confirm)
    with pytest.raises(ValueError, match='INPUTS_CHANGED'): tool.install_approved(**args)
    assert not tool.UNIT.exists() and not (executor / 'gold-pilot-request.json').exists()


def test_expiry_during_durable_staging_prevents_target_install(installation, monkeypatch):
    tool, args, _, _, controller, executor, _ = installation
    write = tool.installer.write_new
    def slow(path, body, **kwargs):
        write(path, body, **kwargs)
        if path.name == 'operator.json':
            monkeypatch.setattr(tool.supervisor, 'now_ms', lambda: NOW + 60_001)
    monkeypatch.setattr(tool.installer, 'write_new', slow)
    with pytest.raises(tool.InstallIncomplete) as error: tool.install_approved(**args)
    assert error.value.result['installed'] is None
    assert not tool.UNIT.exists() and not (executor / 'gold-pilot-request.json').exists()
    assert len(list((controller / 'gold-pilot-installations').glob('*/failure.json'))) == 1


def test_partial_request_install_is_retained_and_never_replayed(installation, monkeypatch):
    tool, args, _, _, controller, executor, _ = installation
    write = tool.installer.write_new
    def fail_unit(path, body, **kwargs):
        if path == tool.UNIT: raise OSError('isolated interrupted unit write')
        return write(path, body, **kwargs)
    monkeypatch.setattr(tool.installer, 'write_new', fail_unit)
    with pytest.raises(tool.InstallIncomplete) as error: tool.install_approved(**args)
    result = error.value.result
    assert result['installed'] is None and result['failure_record_retained'] is True
    request = executor / 'gold-pilot-request.json'; before = request.read_bytes()
    assert not tool.UNIT.exists()
    with pytest.raises(ValueError, match='EXISTING_OR_PARTIAL'): tool.install_approved(**args)
    assert request.read_bytes() == before
    stage = controller / 'gold-pilot-installations' / result['candidate_sha256']
    assert json.loads((stage / 'failure.json').read_bytes()) == result


def test_readback_failure_reports_unknown_after_possible_install(installation, monkeypatch):
    tool, args, *_ = installation
    monkeypatch.setattr(tool, '_read_target', lambda *a, **k: (_ for _ in ()).throw(OSError('readback')))
    with pytest.raises(tool.InstallIncomplete) as error: tool.install_approved(**args)
    assert error.value.result['installed'] is None and tool.UNIT.exists()


@pytest.mark.parametrize('state', ['loaded', 'active', 'unknown', 'transport_error'])
def test_readonly_unit_probe_rejects_loaded_or_unknown_status(monkeypatch, state):
    from tools import gold_pilot_install as tool
    from types import SimpleNamespace
    def run(command, **kwargs):
        assert command == ['systemctl', 'show', '--no-pager', '--property=LoadState,ActiveState', tool.UNIT.name]
        assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'SYSTEMD_COLORS': '0'}
        raw = {'loaded': b'LoadState=loaded\nActiveState=inactive\n',
               'active': b'LoadState=not-found\nActiveState=active\n',
               'unknown': b'ActiveState=inactive\n', 'transport_error': b''}[state]
        return SimpleNamespace(returncode=5 if state == 'transport_error' else 0, stdout=raw)
    monkeypatch.setattr(tool.subprocess, 'run', run)
    with pytest.raises(ValueError): tool._unit_absent()


def test_final_target_reader_rejects_symlinks_before_reading(tmp_path):
    from tools import gold_pilot_install as tool
    target = tmp_path / 'unrelated'; target.write_text('must not read')
    link = tmp_path / 'request'; link.symlink_to(target)
    with pytest.raises(OSError): tool._read_target(link, uid=0)


def test_policy_change_during_target_write_reports_unknown(installation, monkeypatch):
    tool, args, _, policy, *_ = installation
    write = tool.installer.write_new
    def changed(path, body, **kwargs):
        write(path, body, **kwargs)
        if path == tool.UNIT: policy['max_orders'] += 1
    monkeypatch.setattr(tool.installer, 'write_new', changed)
    with pytest.raises(tool.InstallIncomplete) as error: tool.install_approved(**args)
    assert error.value.result['installed'] is None and tool.UNIT.exists()


def test_reader_rejects_hardlinked_or_writable_targets(tmp_path):
    from tools import gold_pilot_install as tool
    import os
    path = tmp_path / 'request'; path.write_bytes(b'public fixture')
    alias = tmp_path / 'alias'; os.link(path, alias)
    with pytest.raises(ValueError, match='TARGET_CUSTODY'): tool._read_target(path, uid=os.geteuid())
    alias.unlink(); path.chmod(0o666)
    with pytest.raises(ValueError, match='TARGET_CUSTODY'): tool._read_target(path, uid=os.geteuid())


def test_directory_creation_failure_never_reports_no_install(installation, monkeypatch):
    tool, args, *_ = installation
    original = tool.installer.directory
    def interrupted(path, *a, **k):
        original(path, *a, **k)
        raise OSError('isolated directory fsync failure')
    monkeypatch.setattr(tool.installer, 'directory', interrupted)
    with pytest.raises(tool.InstallIncomplete) as error: tool.install_approved(**args)
    assert error.value.result['installed'] is None
    assert error.value.result['failure_record_retained'] is False


def test_cli_partial_result_remains_unknown(installation, monkeypatch, capsys):
    tool, args, *_ = installation
    monkeypatch.setattr(tool, '_read_target', lambda *a, **k: (_ for _ in ()).throw(OSError('readback')))
    assert tool.main(['--source-commit', SOURCE, '--bundle-dir', str(args['bundle_dir']),
        '--review-sha256', args['review_sha256'], '--research-state-root', STATE_ROOT, '--install-approved']) == 3
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['status'] == 'INSPECT_PARTIAL_PILOT_INSTALL' and result['installed'] is None
    assert result['started'] is False and result['orders_submitted'] is False
