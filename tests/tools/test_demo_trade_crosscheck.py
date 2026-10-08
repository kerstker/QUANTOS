"""Offline comparison does not write to its source journals or issue requests."""
import json

import pytest

from services.research.demo_statement import save_statement
from services.research.demo_user_stream import UserStreamJournal, normalize_message
from tests.research.test_demo_trade_crosscheck import inputs, NOW
from tools.demo_trade_crosscheck import inspect_order, main


def setup(tmp_path):
    statement, messages = inputs(tmp_path)
    save_statement(tmp_path/'statement', statement)
    path = tmp_path/'stream.db'
    with UserStreamJournal(path, account_ref_sha256='a'*64) as stream:
        stream.start_session(source_sha256='b'*64, at_ms=NOW-1000)
        stream.record('GAP', at_ms=NOW-99, reason='MESSAGE_REJECTED', response_sha256='c'*64)
        stream.record('MESSAGE', at_ms=NOW-98, message=normalize_message(messages[0]['raw_utf8']))
    return path


def test_offline_file_integration_retains_gap_and_does_not_change_journal(tmp_path):
    path = setup(tmp_path); before = path.read_bytes()
    report = inspect_order(statement_directory=tmp_path/'statement', stream_path=path,
        symbol='BTCUSDT', venue_order_id='21', start_ms=NOW-1000, end_ms=NOW, as_of_ms=NOW)
    assert report['status'] == 'MATCHED_WITH_STREAM_GAPS'
    assert report['stream_account_ref_sha256'] == 'a'*64
    assert report['account_binding'] == 'UNKNOWN'
    assert path.read_bytes() == before


def test_cli_retains_failed_diagnostic_and_rejects_existing_output(tmp_path, monkeypatch, capsys):
    path = setup(tmp_path)
    monkeypatch.setattr('tools.demo_trade_crosscheck.time.time_ns', lambda: NOW*1_000_000)
    args = ['--statement-directory', str(tmp_path/'statement'), '--stream-path', str(path),
        '--symbol', 'BTCUSDT', '--venue-order-id', '21', '--start-ms', str(NOW-1000),
        '--end-ms', str(NOW), '--output', str(tmp_path/'result.json')]
    assert main(args) == 2
    raw = (tmp_path/'result.json').read_bytes()
    assert json.loads(raw)['orders_submitted'] is False
    with pytest.raises(SystemExit): main(args)
    assert (tmp_path/'result.json').read_bytes() == raw
    assert 'raw_utf8' not in capsys.readouterr().out


@pytest.mark.parametrize('start,end', [(NOW-150001,NOW), (NOW,NOW), (NOW-1,NOW+1)])
def test_window_rejection_precedes_any_source_open(tmp_path, start, end):
    with pytest.raises(ValueError, match='WINDOW_INVALID'):
        inspect_order(statement_directory=tmp_path/'missing', stream_path=tmp_path/'missing',
            symbol='BTCUSDT', venue_order_id='21', start_ms=start, end_ms=end, as_of_ms=NOW)
