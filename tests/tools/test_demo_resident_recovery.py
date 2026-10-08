"""The resident entrypoint has no credential override or implicit state init."""
import threading

from tools import demo_resident_recovery as cli


def test_missing_custody_stops_before_open_or_credentials(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli, 'require_custody', lambda: (_ for _ in ()).throw(ValueError('PRIVATE_DETAIL')))
    monkeypatch.setattr(cli, '_open', lambda *a: opened.append(1))
    assert cli.main(['--source-commit', 'a'*40]) == 3
    result = capsys.readouterr().out
    assert 'RESIDENT_START_OR_CUSTODY_FAILED' in result and 'PRIVATE_DETAIL' not in result
    assert opened == [] and list(tmp_path.iterdir()) == []


def test_once_uses_fixed_paths_and_preserves_unknown_exit(monkeypatch):
    from pathlib import Path
    root = Path('/var/lib/quantos-demo-executor')
    monkeypatch.setattr(cli, 'require_custody', lambda: root)
    monkeypatch.setattr(cli, '_open', lambda *a: {})
    published = []
    monkeypatch.setattr(cli, 'save_status', lambda path, view: published.append((path, view)))
    class Loop:
        def __init__(self, **kwargs):
            assert kwargs == {'evidence_directory': root/'evidence',
                'output_directory': root/'resident-recovery', 'source_commit': 'b'*40}
        def tick(self, *, publish):
            report = {'schema': 'DEMO_RESIDENT_RECOVERY_V1', 'status': 'INPUTS_UNRESOLVED',
                'reason': 'RESIDENT_ORIGINAL_PROOF_REQUIRED', 'reservation_sha256': None,
                'observed_at_ms': 1800000000000, 'attempt_count': None, 'last_attempt_at_ms': None, 'next_query_at_ms': None,
                'query': None, 'resolution': None,
                'new_order_submission_enabled': False, 'cash_reconciled': False, 'economic_authority': False}
            publish(report)
            return report
    monkeypatch.setattr(cli, 'RecoveryLoop', Loop)
    assert cli.run(source_commit='b'*40, serve=False, stopped=threading.Event()) == 2
    assert len(published) == 1 and published[0][1]['attempt_count'] is None


def test_busy_once_does_not_publish_or_report_success(tmp_path, monkeypatch):
    root = tmp_path / 'state'
    monkeypatch.setattr(cli, 'require_custody', lambda: root)
    monkeypatch.setattr(cli, '_open', lambda *a: {})
    published = []
    monkeypatch.setattr(cli, 'save_status', lambda *a: published.append(a))
    class Loop:
        def __init__(self, **kwargs): pass
        def tick(self, *, publish):
            return {'schema': 'DEMO_RESIDENT_RECOVERY_V1', 'status': 'RESIDENT_BUSY',
                    'reason': 'RESIDENT_CYCLE_IN_PROGRESS', 'reservation_sha256': None,
                    'new_order_submission_enabled': False, 'cash_reconciled': False,
                    'economic_authority': False}
    monkeypatch.setattr(cli, 'RecoveryLoop', Loop)
    assert cli.run(source_commit='b'*40, serve=False, stopped=threading.Event()) == 2
    assert published == []
