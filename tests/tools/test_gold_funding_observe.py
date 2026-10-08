import json
from types import SimpleNamespace

import pytest

from tools import gold_bar_capture as capture


START = 1_800_000_000_000


def setup(monkeypatch, rows):
    clock = [START]
    monkeypatch.setattr(capture, 'time', SimpleNamespace(
        time_ns=lambda: clock[0] * 1_000_000,
        monotonic=lambda: (clock[0] - START) / 1000,
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + int(seconds * 1000))))
    calls = []

    def fetch(out, endpoint, requested):
        calls.append(endpoint)
        raw = json.dumps(rows[len(calls) - 1]).encode()
        (out / 'response.json').write_bytes(raw)
        capture._write(out / 'receipt.json', dict(requested_at_ms=requested,
            received_at_ms=requested + 5))
        return raw, requested + 5
    monkeypatch.setattr(capture, '_fetch', fetch)
    return calls


def row(timestamp=START - 1000, rate='0.001'):
    return dict(symbol='XAUUSDT', fundingTime=timestamp,
                fundingRate=rate, markPrice='4000')


def test_overlapping_polls_preserve_first_receipt_and_new_settlement(tmp_path, monkeypatch):
    calls = setup(monkeypatch, [[row()], [row(), row(START + 10_000)]])
    result = capture.observe_funding(tmp_path / 'out', seconds=120, interval_seconds=60)
    assert result['status'] == 'OBSERVATION_COMPLETE'
    assert result['polls'] == 2 and result['settlements'] == 2
    assert result['duplicates'] == 1
    records = [json.loads(line) for line in (tmp_path / 'out/settlements.jsonl').read_text().splitlines()]
    assert records[0]['available_at_ms'] == START + 5
    assert records[1]['available_at_ms'] == START + 60_005
    assert result['schedule_completeness_proven'] is False
    assert len(calls) == 2


def test_conflicting_observation_stops_and_preserves_raw_response(tmp_path, monkeypatch):
    setup(monkeypatch, [[row()], [row(rate='0.002')]])
    result = capture.observe_funding(tmp_path / 'out', seconds=120, interval_seconds=60)
    assert result['status'] == 'INCOMPLETE'
    assert result['reason'] == 'CONFLICTING_FUNDING_SETTLEMENT'
    assert (tmp_path / 'out/poll-0002/response.json').is_file()
    assert len((tmp_path / 'out/settlements.jsonl').read_text().splitlines()) == 1


def test_empty_polls_do_not_prove_settlement_coverage(tmp_path, monkeypatch):
    setup(monkeypatch, [[], []])
    result = capture.observe_funding(tmp_path / 'out', seconds=120, interval_seconds=60)
    assert result['settlements'] == 0
    assert result['schedule_completeness_proven'] is False
    assert result['account_cash_verified'] is False


@pytest.mark.parametrize('seconds,interval', [(0, 60), (86401, 60), (120, 1), (True, 60)])
def test_invalid_budget_has_no_output(tmp_path, seconds, interval):
    with pytest.raises(ValueError):
        capture.observe_funding(tmp_path / 'out', seconds=seconds, interval_seconds=interval)
    assert not (tmp_path / 'out').exists()


def test_network_failure_retains_completed_poll_and_stops(tmp_path, monkeypatch):
    setup(monkeypatch, [[row()]])
    previous = capture._fetch

    def fetch(out, endpoint, requested):
        if out.name == 'poll-0002':
            raise ConnectionResetError('network interrupted')
        return previous(out, endpoint, requested)
    monkeypatch.setattr(capture, '_fetch', fetch)
    result = capture.observe_funding(tmp_path / 'out', seconds=120)
    assert result['status'] == 'INCOMPLETE'
    assert result['settlements'] == 1
    assert (tmp_path / 'out/poll-0001/response.json').is_file()
    assert result['reason'] == 'CAPTURE_FAILED_ConnectionResetError'


def test_command_reports_incomplete_with_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, 'observe_funding', lambda *a, **kw: {'status': 'INCOMPLETE'})
    assert capture.main(['--observe-funding', '--output-dir', str(tmp_path / 'out')]) == 2
