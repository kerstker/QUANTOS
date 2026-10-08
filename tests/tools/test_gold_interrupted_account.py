import json
import os
from pathlib import Path

import pytest

from tests.tools.test_gold_account_chain import setup
from tools.gold_account_chain import advance
from tools import gold_interrupted_account as tool


def evidence(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    advance(tmp_path/'chain', tmp_path/'first', **args)
    # A different start tick identifies an exited process even after PID reuse.
    (tmp_path/'monitor.json').write_text(json.dumps(dict(pid=os.getpid(), start_ticks='0')))
    (tmp_path/'terminal.json').write_text(json.dumps(dict(status='INCOMPLETE',
        completed_sequences=[1], error_type='ValueError', reason='BOUND_SOURCE_STREAM_TERMINATED',
        submission_enabled=False)))
    return dict(chain=tmp_path/'chain', monitor=tmp_path/'monitor.json',
                terminal=tmp_path/'terminal.json', output=tmp_path/'review')


def test_interruption_reproduces_last_account_without_calling_it_complete(tmp_path, monkeypatch):
    args = evidence(tmp_path, monkeypatch)
    before = (tmp_path/'chain/000001/report.json').read_bytes()
    result = tool.finalize(**args)
    assert result['status'] == 'INTERRUPTED_ACCOUNT_REPRODUCED'
    assert result['producer_status'] == 'INCOMPLETE'
    assert result['account_resumed'] is False
    assert result['economic_eligible'] is False and result['submission_enabled'] is False
    report = json.loads(before)
    assert result['loss_control'] == report['loss_control']
    assert (tmp_path/'chain/000001/report.json').read_bytes() == before
    assert (tmp_path/'review/archive/inputs/000001/report.json').read_bytes() == before
    assert (tmp_path/'review/producer-terminal.json').read_bytes() == args['terminal'].read_bytes()


def test_live_original_process_prevents_finalization(tmp_path, monkeypatch):
    args = evidence(tmp_path, monkeypatch)
    fields = Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()
    args['monitor'].write_text(json.dumps(dict(pid=os.getpid(), start_ticks=fields[19])))
    with pytest.raises(ValueError, match='PRODUCER_STILL_RUNNING'):
        tool.finalize(**args)
    assert not args['output'].exists()


def test_unpublished_sequence_cannot_be_reported_as_reproduced(tmp_path, monkeypatch):
    args = evidence(tmp_path, monkeypatch)
    data = json.loads(args['terminal'].read_text())
    data['completed_sequences'].append(2)
    args['terminal'].write_text(json.dumps(data))
    with pytest.raises(ValueError, match='INTERRUPTED_SEQUENCE_MISMATCH'):
        tool.finalize(**args)
    assert not args['output'].exists()
