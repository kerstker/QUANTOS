"""Scheduled backups must retain the configured target after argv isolation."""
import json
from pathlib import Path
import sys

import pytest

from tools import l2_backup
from tools.l2_schedule_entrypoint import run_scheduled_module
from tools.render_systemd_l2_units import render_units, SystemdRenderError, main as render_main

ENV = 'QUANTOS_BACKUP_RTO_MS'
ROOT = Path(__file__).resolve().parents[2]


def backup_stub(monkeypatch):
    calls = []
    monkeypatch.setattr(l2_backup, 'resolve_backup_passphrase', lambda **_: 'isolated-test-only')
    monkeypatch.setattr(l2_backup, 'run_recorded_l2_backup',
                        lambda **kwargs: calls.append(kwargs) or {'verdict': 'PASS'})
    monkeypatch.setattr(sys, 'argv', ['l2_backup.py'])
    monkeypatch.delenv(ENV, raising=False)
    return calls


def test_scheduled_backup_keeps_two_hour_environment_target(tmp_path, monkeypatch, capsys):
    calls = backup_stub(monkeypatch)
    monkeypatch.setenv(ENV, '7200000')
    monkeypatch.chdir(tmp_path)
    launch_args = ['l2_schedule_entrypoint.py', 'l2_backup', '--release-commit', 'a'*40]
    monkeypatch.setattr(sys, 'argv', launch_args[:])
    result = run_scheduled_module('l2_backup', code_root=ROOT, expected_release_commit='a'*40,
        release_verifier=lambda _root, commit: commit,
        release_evidence_verifier=lambda _root, _commit: ({}, 'b'*64),
        importer=lambda name: l2_backup)
    assert result == 0
    assert calls[0]['rto_ms'] == 7200000
    assert calls[0]['max_rpo_ms'] == 86400000
    assert sys.argv == launch_args
    assert json.loads(capsys.readouterr().out)['verdict'] == 'PASS'


def test_invalid_scheduled_target_is_failure_not_the_allowed_exit_two(tmp_path, monkeypatch):
    calls = backup_stub(monkeypatch)
    monkeypatch.setenv(ENV, 'invalid')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(l2_backup, 'resolve_backup_passphrase', lambda **_: pytest.fail('credential path reached'))
    result = run_scheduled_module('l2_backup', code_root=ROOT, expected_release_commit='a'*40,
        release_verifier=lambda _root, commit: commit,
        release_evidence_verifier=lambda _root, _commit: ({}, 'b'*64),
        importer=lambda name: l2_backup)
    # The existing systemd unit permits exit 2 for some worker outcomes.
    # Bad restore configuration must remain a failed scheduled invocation.
    assert result == 1
    assert calls == []


@pytest.mark.parametrize('environment,argv,expected', [
    (None, [], 300000), ('7200000', [], 7200000),
    ('7200000', ['--rto-ms', '600000'], 600000),
    ('invalid', ['--rto-ms', '7200000'], 7200000),
])
def test_cli_precedence_preserves_generic_default(monkeypatch, environment, argv, expected):
    calls = backup_stub(monkeypatch)
    if environment is not None:
        monkeypatch.setenv(ENV, environment)
    monkeypatch.setattr(sys, 'argv', ['l2_backup.py', *argv])
    assert l2_backup.main() == 0
    assert calls[0]['rto_ms'] == expected


@pytest.mark.parametrize('value', ['', '0', '-1', '1.5', 'nan', '86400001', '1e9', ' 7200000', '٧٢٠٠٠٠٠'])
def test_invalid_scheduled_target_stops_before_credential_or_backup_access(monkeypatch, value):
    calls = backup_stub(monkeypatch)
    monkeypatch.setenv(ENV, value)
    monkeypatch.setattr(l2_backup, 'resolve_backup_passphrase', lambda **_: pytest.fail('credential path reached'))
    assert l2_backup.main() == 1
    assert calls == []


def test_renderer_binds_target_only_to_backup_and_keeps_full_worker_timeout(tmp_path):
    render_units(output_root=tmp_path/'units', release_commit='a'*40,
                 modules=['l2_backup', 'domain_event_ops'], backup_mount='/mnt/quantos-backup',
                 backup_rto_ms=7200000)
    backup = (tmp_path/'units/quantos-l2@l2_backup.service').read_text()
    other = (tmp_path/'units/quantos-l2@domain_event_ops.service').read_text()
    assert backup.count('Environment=QUANTOS_BACKUP_RTO_MS=7200000') == 1
    assert ENV not in other
    assert 'TimeoutStartSec=6h' in backup
    assert 'Environment=ALLOW_ORDER_SUBMISSION=false' in backup
    assert 'Environment=REQUIRE_HUMAN_APPROVAL=true' in backup


def test_larger_configured_target_is_not_cut_short_by_worker_timeout(tmp_path):
    render_units(output_root=tmp_path/'units', release_commit='a'*40,
                 modules=['l2_backup'], backup_mount='/mnt/quantos-backup',
                 backup_rto_ms=25200000)
    backup = (tmp_path/'units/quantos-l2@l2_backup.service').read_text()
    assert 'TimeoutStartSec=39600s' in backup  # 7h restore plus 4h backup headroom


@pytest.mark.parametrize('value', [True, 0, -1, 86400001, '7200000'])
def test_renderer_rejects_bad_typed_target_before_creating_output(tmp_path, value):
    output = tmp_path/'units'
    with pytest.raises(SystemdRenderError):
        render_units(output_root=output, release_commit='a'*40, modules=['l2_backup'],
                     backup_mount='/mnt/quantos-backup', backup_rto_ms=value)
    assert not output.exists()


def test_renderer_rejects_target_without_backup_worker(tmp_path):
    with pytest.raises(SystemdRenderError):
        render_units(output_root=tmp_path/'units', release_commit='a'*40,
                     modules=['domain_event_ops'], backup_rto_ms=7200000)
    assert not (tmp_path/'units').exists()


def test_renderer_cli_carries_target_and_rejects_other_render_modes(tmp_path):
    args = ['--output', str(tmp_path/'units'), '--release-commit', 'a'*40,
            '--module', 'l2_backup', '--backup-mount', '/mnt/quantos-backup',
            '--backup-rto-ms', '7200000']
    assert render_main(args) == 0
    assert 'Environment=QUANTOS_BACKUP_RTO_MS=7200000' in (tmp_path/'units/quantos-l2@l2_backup.service').read_text()
    for mode in ('--special-units', '--research-runner-unit'):
        with pytest.raises(SystemExit):
            render_main(['--output', str(tmp_path/mode), '--release-commit', 'a'*40,
                         mode, '--backup-rto-ms', '7200000'])
        assert not (tmp_path/mode).exists()
