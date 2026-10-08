import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from tools import gold_quote_campaign as campaign
from tools import gold_quote_session as session


@pytest.fixture
def clock(monkeypatch):
    state = [0.0]
    def sleep(seconds):
        state[0] += seconds
    monkeypatch.setattr(campaign, 'time', SimpleNamespace(
        monotonic=lambda: state[0], time_ns=lambda: int(state[0] * 1e9), sleep=sleep))
    monkeypatch.setattr(campaign, 'source_identity', lambda: {'fixture': True})
    monkeypatch.setattr(session, 'source_identity', lambda: {'fixture': True})
    return state


def test_network_failure_preserved_and_successor_is_separate(tmp_path, monkeypatch, clock):
    calls = []
    def capture(**kwargs):
        calls.append(kwargs['seconds'])
        kwargs['on_record']({'kind': 'mark', 'received_at': len(calls)})
        if len(calls) == 1:
            clock[0] += 10
            raise URLError(TimeoutError())
        clock[0] += kwargs['seconds']
        return dict(sample_count=1, quote_coverage={'gap_count': 0})
    monkeypatch.setattr(session, 'capture_gold_session', capture)
    result = campaign.run(tmp_path/'out', seconds=120, segment_seconds=60, retry_seconds=10)
    assert calls == [60, 60, 40]
    assert result['reason'] == 'DURATION_COMPLETE'
    assert result['status'] == 'PARTIAL_OBSERVATIONS'
    assert result['continuous'] is False and result['replay_ready'] is False
    assert result['submission_enabled'] is False
    assert (tmp_path/'out/segment-0001/failure.json').exists()
    assert (tmp_path/'out/segment-0001/receipt-000000.json').exists()
    assert (tmp_path/'out/segment-0002/summary.json').exists()
    assert len(result['segments']) == 3
    assert result['segments'][0]['retryable'] is True
    assert len(result['segments'][0]['terminal_sha256']) == 64
    with pytest.raises(FileExistsError):
        campaign.run(tmp_path/'out', seconds=120)


@pytest.mark.parametrize('error', [ValueError('bad record'), HTTPError('https://public.invalid', 403, 'denied', {}, None)])
def test_non_network_or_http_rejection_is_not_retried(tmp_path, monkeypatch, clock, error):
    def capture(**kwargs):
        raise error
    monkeypatch.setattr(session, 'capture_gold_session', capture)
    with pytest.raises(type(error)):
        campaign.run(tmp_path/'out', seconds=120)
    result = json.loads((tmp_path/'out/summary.json').read_text())
    assert result['reason'] == 'NONRECOVERABLE_FAILURE'
    assert len(result['segments']) == 1
    assert not (tmp_path/'out/segment-0002').exists()


def test_retries_cannot_exceed_fixed_segment_budget(tmp_path, monkeypatch, clock):
    def capture(**kwargs):
        raise URLError(ConnectionResetError())
    monkeypatch.setattr(session, 'capture_gold_session', capture)
    result = campaign.run(tmp_path/'out', seconds=120, max_segments=2, retry_seconds=10)
    assert result['reason'] == 'SEGMENT_LIMIT'
    assert len(result['segments']) == 2
    assert clock[0] == 10  # no pointless delay after final allowed attempt


def test_interrupt_is_recorded_and_never_restarted(tmp_path, monkeypatch, clock):
    def capture(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(session, 'capture_gold_session', capture)
    with pytest.raises(KeyboardInterrupt):
        campaign.run(tmp_path/'out', seconds=120)
    result = json.loads((tmp_path/'out/summary.json').read_text())
    assert result['reason'] == 'INTERRUPTED'
    assert len(result['segments']) == 1


def test_source_change_stops_before_starting_next_segment(tmp_path, monkeypatch, clock):
    def capture(**kwargs):
        clock[0] += kwargs['seconds']
        monkeypatch.setattr(campaign, 'source_identity', lambda: {'changed': True})
        return dict(sample_count=1, quote_coverage={'gap_count': 0})
    monkeypatch.setattr(session, 'capture_gold_session', capture)
    with pytest.raises(ValueError, match='CAMPAIGN_SOURCE_CHANGED'):
        campaign.run(tmp_path/'out', seconds=120, segment_seconds=60)
    result = json.loads((tmp_path/'out/summary.json').read_text())
    assert result['reason'] == 'SOURCE_CHANGED'
    assert not (tmp_path/'out/segment-0002').exists()


def test_minimum_duration_still_collects_after_plan_write_latency(tmp_path, monkeypatch, clock):
    save = campaign._save
    def delayed_save(*args):
        result = save(*args)
        clock[0] += .001
        return result
    monkeypatch.setattr(campaign, '_save', delayed_save)
    calls = []
    def capture(**kwargs):
        calls.append(kwargs['seconds'])
        clock[0] += kwargs['seconds']
        return dict(sample_count=1, quote_coverage={'gap_count': 0})
    monkeypatch.setattr(session, 'capture_gold_session', capture)
    result = campaign.run(tmp_path/'out', seconds=30)
    assert calls == [30]
    assert result['status'] == 'SEGMENTS_OBSERVED'
