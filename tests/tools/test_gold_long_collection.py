"""Long public observation keeps the original journals and legacy boundaries."""
import asyncio
import hashlib
import json
from copy import deepcopy

import pytest

from tests.tools.test_gold_bar_session import fixture_history
from tests.tools.test_gold_public_segment_producer import transport


def budget(**changes):
    value = dict(schema='GOLD_PUBLIC_COLLECTION_BUDGET_V1',
                maximum_duration_seconds=32 * 86400,
                maximum_quote_bytes=16 * 1024**3,
                maximum_quote_receipts=8_000_000,
                maximum_quote_segments=4096,
                maximum_bar_bytes=16 * 1024**3,
                maximum_bar_messages=16_000_000,
                maximum_bar_connections=128)
    return dict(value, **changes)


def test_long_request_reaches_original_collector_only_with_frozen_budget(tmp_path, monkeypatch):
    from tools import gold_quote_session as journal
    from tools import perpetual_public_capture as collector
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    calls = []

    def fetch_forbidden(*args, **kwargs):
        calls.append(True)
        raise ConnectionError('FIRST_ORIGINAL_REQUEST')

    monkeypatch.setattr(collector.urllib.request, 'build_opener', fetch_forbidden)
    with pytest.raises(ValueError):
        journal.run(tmp_path / 'legacy', seconds=30 * 86400, segment_samples=120)
    assert not calls and not (tmp_path / 'legacy').exists()
    frozen = budget()
    with pytest.raises(ConnectionError, match='FIRST_ORIGINAL_REQUEST'):
        journal.run(tmp_path / 'long', seconds=30 * 86400, segment_samples=120,
                    collection_budget=frozen)
    plan = json.loads((tmp_path / 'long/plan.json').read_bytes())
    failure = json.loads((tmp_path / 'long/failure.json').read_bytes())
    assert plan['schema'] == 'GOLD_PUBLIC_SEGMENTED_QUOTE_SESSION_PLAN_V2'
    assert plan['collection_budget'] == frozen == failure['collection_budget']
    assert failure['status'] == 'INCOMPLETE' and failure['receipts'] == 0
    assert not (tmp_path / 'long/summary.json').exists()


def test_budgeted_original_segments_cold_read_and_downgrade_rejection(tmp_path, monkeypatch):
    from tools import gold_quote_session as journal
    transport(monkeypatch)
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    frozen = budget()
    result = journal.run(tmp_path / 'quotes', seconds=30, interval_seconds=1,
                         segment_samples=8, collection_budget=frozen)
    saved = journal.read_segment(tmp_path / 'quotes', 0)
    reviewed = journal.review(tmp_path / 'quotes')
    assert reviewed['sample_count'] == result['sample_count']
    assert reviewed['quote_coverage'] == result['quote_coverage']
    assert reviewed['source_authenticity_proven'] is reviewed['economic_eligible'] is False
    assert saved['collection_budget'] == result['collection_budget'] == frozen
    assert saved['schema'] == 'GOLD_PUBLIC_RETAINED_QUOTE_SEGMENT_V2'
    plan_path = tmp_path / 'quotes/plan.json'
    plan = json.loads(plan_path.read_bytes())
    plan['schema'] = journal.SEGMENT_PLAN
    plan_path.write_bytes(journal._raw(plan))
    with pytest.raises(ValueError):
        journal.read_segment(tmp_path / 'quotes', 0)


def test_bar_budget_preserves_original_open_and_closed_messages_and_cold_restore(tmp_path, monkeypatch):
    from tools import gold_bar_session as journal
    history, message = fixture_history(tmp_path)
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    closed, received = message(0)
    opened = json.loads(closed)
    opened['data']['k']['x'] = False
    opened['data']['E'] = received - 1
    original_open = json.dumps(opened)

    async def stream(seconds, state, **kwargs):
        state['connected'] = True
        yield original_open, received - 1
        yield closed, received
        await asyncio.sleep(10)

    monkeypatch.setattr(journal, '_messages', stream)
    result = asyncio.run(journal.observe(history, tmp_path / 'bars', seconds=1,
                                        collection_budget=budget()))
    assert result['schema'] == 'GOLD_CONTINUOUS_BAR_SESSION_V2'
    assert result['messages'] == 2 and result['closed_bars'] == 1
    rows = [json.loads(row) for row in (tmp_path / 'bars/messages-0000.jsonl').read_bytes().splitlines()]
    assert [row['raw'] for row in rows] == [original_open, closed]
    assert journal.restore(tmp_path / 'bars')['messages'] == 2
    manifest_path = tmp_path / 'bars/manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['collection_budget']['maximum_bar_messages'] += 1
    manifest_path.write_bytes(journal._encode(manifest))
    summary_path = tmp_path / 'bars/summary.json'
    changed = deepcopy(result)
    changed['manifest_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    summary_path.write_bytes(journal._encode(changed))
    with pytest.raises(ValueError, match='BUDGET'):
        journal.restore(tmp_path / 'bars')


@pytest.mark.parametrize('field', ['maximum_duration_seconds', 'maximum_quote_bytes',
                                  'maximum_quote_receipts', 'maximum_quote_segments',
                                  'maximum_bar_bytes', 'maximum_bar_messages',
                                  'maximum_bar_connections'])
@pytest.mark.parametrize('value', [True, 0, -1, 0.5, '1'])
def test_invalid_budget_never_creates_output_or_opens_network(tmp_path, monkeypatch, field, value):
    from tools import gold_quote_session as journal
    from tools import perpetual_public_capture as collector

    def forbidden(*args, **kwargs):
        raise AssertionError('NETWORK_AFTER_INVALID_BUDGET')

    monkeypatch.setattr(collector.urllib.request, 'build_opener', forbidden)
    with pytest.raises(ValueError, match='BUDGET'):
        journal.run(tmp_path / 'invalid', seconds=30, segment_samples=120,
                    collection_budget=budget(**{field: value}))
    assert not (tmp_path / 'invalid').exists()


@pytest.mark.parametrize('resource', ['receipts', 'bytes', 'segments'])
def test_resource_exhaustion_retains_failure_and_all_committed_originals(tmp_path, monkeypatch, resource):
    from tools import gold_quote_session as journal
    transport(monkeypatch)
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    field, limit = {'receipts': ('maximum_quote_receipts', 1),
                    'bytes': ('maximum_quote_bytes', 6000),
                    'segments': ('maximum_quote_segments', 1)}[resource]
    output = tmp_path / resource
    with pytest.raises(ValueError, match='BUDGET'):
        journal.run(output, seconds=30, interval_seconds=1, segment_samples=8,
                    collection_budget=budget(**{field: limit}))
    failure = json.loads((output / 'failure.json').read_bytes())
    assert failure['status'] == 'INCOMPLETE'
    assert len(list(output.glob('receipt-*.json'))) == failure['receipts']
    originals = (output / 'originals.jsonl').read_bytes()
    assert hashlib.sha256(originals).hexdigest() == failure['originals_sha256']
    for line in originals.splitlines():
        binding = json.loads(line)
        raw = (output / binding['name']).read_bytes()
        assert len(raw) == binding['bytes']
        assert hashlib.sha256(raw).hexdigest() == binding['sha256']
    assert not (output / 'summary.json').exists()


@pytest.mark.parametrize('damage', ['extra', 'missing', 'error', 'nonselected', 'budget'])
def test_quote_cold_review_rejects_changed_or_incomplete_originals(tmp_path, monkeypatch, damage):
    from tools import gold_quote_session as journal
    transport(monkeypatch)
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    original_capture = journal.capture_gold_session

    def capture(**kwargs):
        kwargs['on_error']({'error_type': 'ISOLATED_TEST_DIAGNOSTIC'})
        return original_capture(**kwargs)

    monkeypatch.setattr(journal, 'capture_gold_session', capture)
    output = tmp_path / 'quotes'
    journal.run(output, seconds=30, interval_seconds=1, segment_samples=8,
                collection_budget=budget())
    assert journal.review(output)['status'] == 'RAW_ORIGINALS_RECONSTRUCTED'
    if damage == 'extra':
        (output / 'receipt-999999.json').write_bytes(b'{}')
    elif damage == 'missing':
        (output / 'receipt-000000.json').unlink()
    elif damage == 'error':
        path = output / 'error-000000.json'
        path.write_bytes(path.read_bytes() + b' ')
    elif damage == 'nonselected':
        path = sorted(output.glob('segment-*.json'))[-1]
        path.write_bytes(path.read_bytes() + b' ')
    else:
        path = output / 'summary.json'
        summary = json.loads(path.read_bytes())
        summary['collection_budget']['maximum_quote_bytes'] -= 1
        path.write_bytes(journal._raw(summary))
    with pytest.raises((ValueError, FileNotFoundError)):
        journal.review(output)


def test_bar_duration_over_one_day_uses_budget_before_first_network_message(tmp_path, monkeypatch):
    from tools import gold_bar_session as journal
    history, _ = fixture_history(tmp_path)
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    calls = []

    async def stream(seconds, state, **kwargs):
        calls.append(seconds)
        raise ConnectionError('FIRST_ORIGINAL_MESSAGE')
        yield  # noqa: B018 - retain an asynchronous generator

    monkeypatch.setattr(journal, '_messages', stream)
    with pytest.raises(ValueError):
        asyncio.run(journal.observe(history, tmp_path / 'legacy', seconds=30 * 86400))
    assert not calls and not (tmp_path / 'legacy').exists()
    result = asyncio.run(journal.observe(history, tmp_path / 'long', seconds=30 * 86400,
                                        collection_budget=budget()))
    assert calls == [30 * 86400]
    assert result['status'] == 'INCOMPLETE' and result['reason'] == 'FAILED_ConnectionError'
    assert result['collection_budget'] == budget()


def test_explicit_connection_budget_can_cross_four_disconnects_without_resetting_cursor(monkeypatch):
    from contextlib import aclosing
    from tools import gold_bar_observe as observer
    calls = []

    class Socket:
        async def __aenter__(self):
            calls.append(True)
            return self

        async def __aexit__(self, *args):
            pass

        async def recv(self):
            if len(calls) <= 4:
                raise ConnectionError('ISOLATED_DISCONNECT')
            return 'original-message'

    async def pause(seconds):
        pass

    monkeypatch.setattr(observer, 'connect', lambda *args, **kwargs: Socket())
    monkeypatch.setattr(observer.asyncio, 'sleep', pause)

    async def scenario():
        state = dict(connected=False, errors=[])
        async with aclosing(observer._messages(30, state)) as stream:
            assert [row async for row in stream] == []
        assert len(calls) == 4 and len(state['errors']) == 4
        calls.clear()
        state = dict(connected=False, errors=[])
        async with aclosing(observer._messages(30, state, maximum_connections=8)) as stream:
            message, received = await anext(stream)
        assert message == 'original-message' and type(received) is int
        assert len(calls) == 5 and len(state['errors']) == 4

    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['malformed', 'budget'])
def test_failed_bar_processing_keeps_the_rejected_wire_original(tmp_path, monkeypatch, failure):
    from tools import gold_bar_session as journal
    history, message = fixture_history(tmp_path)
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    rejected, received = message(1)
    if failure == 'malformed':
        rejected = '{"stream":"wrong", "original":"retained"}'

    async def stream(seconds, state, **kwargs):
        state['connected'] = True
        yield message(0)
        yield rejected, received

    monkeypatch.setattr(journal, '_messages', stream)
    output = tmp_path / 'bars'
    result = asyncio.run(journal.observe(history, output, seconds=1,
                                        collection_budget=budget(maximum_bar_messages=1)
                                        if failure == 'budget' else budget()))
    assert result['status'] == 'INCOMPLETE' and result['messages'] == 1
    raw = (output / 'rejected-message.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result['rejected_message_sha256']
    retained = json.loads(raw)
    assert retained['raw'] == rejected and retained['received_at_ms'] == received
    assert retained['submission_enabled'] is False
    with pytest.raises(ValueError, match='INCOMPLETE'):
        journal.restore(output)


def test_utc_day_and_funding_boundary_preserve_received_originals(tmp_path, monkeypatch):
    import tests.tools.test_gold_public_segment_producer as fixtures
    from tools import gold_quote_session as journal
    day = fixtures.BASE // 86400000 * 86400000
    monkeypatch.setattr(fixtures, 'BASE', day + 86400000 - 15000)
    transport(monkeypatch, funding_after_ms=10000)
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    output = tmp_path / 'quotes'
    result = journal.run(output, seconds=30, interval_seconds=1, segment_samples=8,
                         collection_budget=budget())
    segments = [journal.read_segment(output, i)['segment'] for i in range(result['segment_count'])]
    capacity = [row for segment in segments for row in segment['capacity_observations']]
    funding = [row for segment in segments for row in segment['observed_funding_records']]
    assert capacity and funding
    assert all(row['requested_at'] >= day + 86400000 for row in capacity)
    assert all(row['received_at'] >= row['payload'][0]['fundingTime'] for row in funding)
    assert journal.review(output)['sample_count'] == result['sample_count']


def test_measured_monthly_quote_capacity_accepts_explicit_64_gib_without_allocation(tmp_path, monkeypatch):
    from tools import gold_quote_session as journal
    from tools import perpetual_public_capture as collector
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise ConnectionError('FIRST_ORIGINAL_REQUEST')

    monkeypatch.setattr(collector.urllib.request, 'build_opener', forbidden)
    with pytest.raises(ConnectionError, match='FIRST_ORIGINAL_REQUEST'):
        journal.run(tmp_path / 'long', seconds=30 * 86400, segment_samples=120,
                    collection_budget=budget(maximum_quote_bytes=64 * 1024**3))
    assert calls
    plan = json.loads((tmp_path / 'long/plan.json').read_bytes())
    assert plan['collection_budget']['maximum_quote_bytes'] == 64 * 1024**3
    assert sum(path.stat().st_size for path in (tmp_path / 'long').iterdir()) < 65536
