"""Offline entry reads frozen evidence and never overwrites an earlier result."""
import json

import pytest

from tests.research.test_demo_account_recovery import inputs
from tests.research.test_demo_account_days import later_close


def test_offline_cross_day_entry_produces_bound_non_authoritative_report(tmp_path):
    from tools.demo_account_day_replay import main
    source = tmp_path / 'inputs.json'; output = tmp_path / 'report.json'
    source.write_text(json.dumps(later_close(inputs(tmp_path), days=2)))
    original = source.read_bytes()
    assert main(['--input', str(source), '--output', str(output)]) == 0
    report = json.loads(output.read_bytes())
    assert report['status'] == 'REPORTED_ACCOUNT_MATCHED'
    assert report['scope'] == 'TECHNICAL_VALIDATION_ONLY' and report['authoritative'] is False
    assert len(report['daily_cash']) == 3 and report['risk_budget_reset'] is False
    assert source.read_bytes() == original
    retained = output.read_bytes()
    with pytest.raises(FileExistsError):
        main(['--input', str(source), '--output', str(output)])
    assert output.read_bytes() == retained


def test_offline_entry_rejects_duplicate_keys_and_does_not_create_report(tmp_path):
    from tools.demo_account_day_replay import main
    source = tmp_path / 'inputs.json'; output = tmp_path / 'report.json'
    source.write_text('{"opening": {}, "opening": {}}')
    with pytest.raises(ValueError):
        main(['--input', str(source), '--output', str(output)])
    assert not output.exists()


def test_unresolved_days_are_retained_with_nonzero_exit(tmp_path):
    from tools.demo_account_day_replay import main
    source = tmp_path / 'inputs.json'; output = tmp_path / 'report.json'
    source.write_text(json.dumps(later_close(inputs(tmp_path), days=7)))
    assert main(['--input', str(source), '--output', str(output)]) == 2
    report = json.loads(output.read_bytes())
    assert report['status'] == 'UNRESOLVED' and report['daily_cash'] == []
