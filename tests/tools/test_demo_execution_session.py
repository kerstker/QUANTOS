"""Finite stdio handoff: no endpoint/path override or retry on EOF/timeout."""
import io
import os
import time
import json
import threading

import pytest

from tools import demo_execution_session as cli


def test_missing_custody_stops_without_opening_stores_or_echoing_private_details(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, 'require_custody', lambda: (_ for _ in ()).throw(ValueError('PRIVATE_DETAIL')))
    monkeypatch.setattr(cli, '_open', lambda *_: calls.append(1))
    assert cli.main(['--source-commit', 'a'*40]) == 3
    assert calls == []
    assert 'PRIVATE_DETAIL' not in capsys.readouterr().out


@pytest.mark.parametrize('raw', [b'no newline', b'', b'x'*20+b'\n'])
def test_bounded_frame_rejects_eof_partial_or_excess_before_any_command(raw):
    reader, writer = os.pipe()
    try:
        os.write(writer, raw); os.close(writer); writer = None
        with pytest.raises(ValueError): cli.read_frame(reader, maximum=16, deadline=time.monotonic()+1)
    finally:
        os.close(reader)
        if writer is not None: os.close(writer)


def test_wait_deadline_and_extra_same_frame_content_do_not_wait_forever():
    reader, writer = os.pipe()
    try:
        with pytest.raises(ValueError): cli.read_frame(reader, maximum=16, deadline=time.monotonic()+.01)
        os.write(writer, b'{}\n{}\n')
        with pytest.raises(ValueError): cli.read_frame(reader, maximum=16, deadline=time.monotonic()+1)
    finally:
        os.close(reader); os.close(writer)


def test_grant_fields_are_exact_and_signed_envelope_is_not_replaced():
    command = b'{"schema":"DEMO_EXECUTION_SESSION_GRANT_V1","proposal_sha256":"' + b'a'*64 + b'","envelope":{"original":true}}\n'
    identity, envelope = cli.parse_grant(command)
    assert identity == 'a'*64 and envelope == b'{"original":true}'
    with pytest.raises(ValueError): cli.parse_grant(command.replace(b'"envelope":', b'"public_key":"untrusted","envelope":'))


def test_actual_pipe_proposal_then_external_grant_stays_in_one_process(tmp_path, monkeypatch):
    from tests.live.test_demo_execution_session import fixture, request, NOW
    from tests.live.test_demo_pre_submit import signed
    from live.exchange import binance_demo_exact as transport
    _, opened, private, envelope, values = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, 'require_custody', lambda: tmp_path)
    monkeypatch.setattr(cli, '_open', lambda *a: dict(zip(('dispatch', 'recovery', 'oms'), opened)))
    monkeypatch.setattr(cli, 'now_ms', lambda: NOW)
    calls = []
    def http(*_):
        calls.append(1)
        return 200, json.dumps({'symbol': 'BTCUSDC', 'clientOrderId': values['orders'][0]['newClientOrderId'], 'orderId': 123}).encode()
    monkeypatch.setattr(transport, '_http_once', http)
    reader, writer = os.pipe()
    class Channel(io.StringIO):
        sent = False
        def flush(self):
            if self.sent: return
            proposal = json.loads(self.getvalue())
            grant = {'schema': 'DEMO_EXECUTION_SESSION_GRANT_V1', 'proposal_sha256': proposal['proposal_sha256'],
                'envelope': json.loads(signed(private, envelope, proposal['proposal']))}
            raw = json.dumps(grant).encode()+b'\n'
            assert os.write(writer, raw) == len(raw)
            self.sent = True
    raw = request(values)+b'\n'
    def send_request():
        view = memoryview(raw)
        while view:
            count = os.write(writer, view); view = view[count:]
    sender = threading.Thread(target=send_request, daemon=True)
    sender.start(); channel = Channel()
    try:
        assert cli.run(source_commit='b'*40, input_fd=reader, output=channel) == 0
        assert len(calls) == 1
        lines = channel.getvalue().splitlines()
        assert len(lines) == 2
        result = json.loads(lines[1])
        assert result['status'] == 'QUERY_RECOVERY_REQUIRED' and result['cash_reconciled'] is False
        assert len(list((tmp_path/'session-results').glob('*.json'))) == 1
    finally:
        os.close(reader); os.close(writer); sender.join(timeout=1)
        for store in opened: store.close()


def test_eof_keeps_failed_session_result_and_does_not_initialize_missing_order_stores(tmp_path, monkeypatch):
    from tests.live.test_demo_execution_session import fixture, NOW
    _, opened, _, _, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, 'require_custody', lambda: tmp_path)
    monkeypatch.setattr(cli, '_open', lambda *a: dict(zip(('dispatch', 'recovery', 'oms'), opened)))
    monkeypatch.setattr(cli, 'now_ms', lambda: NOW)
    reader, writer = os.pipe(); os.close(writer)
    output = io.StringIO()
    try:
        assert cli.run(source_commit='b'*40, input_fd=reader, output=output) == 3
        result = json.loads(output.getvalue())
        assert result['phase'] == 'WAITING_INPUT' and result['stop_record_persisted'] is True
        assert result['retry_submission'] is False
        assert len(list((tmp_path/'session-results').glob('*.json'))) == 1
        assert opened[0].snapshot() == []
    finally:
        os.close(reader)
        for store in opened: store.close()
