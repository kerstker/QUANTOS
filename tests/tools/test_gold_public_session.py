"""Paired long collection drains the original workers and binds both journals."""
import asyncio
import json
from threading import Event

import pytest

from tests.tools.test_gold_long_collection import budget


def test_invalid_shared_duration_rejects_before_output_or_workers(tmp_path, monkeypatch):
    from tools import gold_public_session as session

    def forbidden(*args, **kwargs):
        raise AssertionError('COLLECTOR_STARTED')

    monkeypatch.setattr(session, 'observe_bars', forbidden)
    monkeypatch.setattr(session, 'observe_quotes', forbidden)
    with pytest.raises(ValueError, match='BUDGET'):
        asyncio.run(session.run(tmp_path / 'absent-history', tmp_path / 'invalid',
                                seconds=30 * 86400, collection_budget=budget(maximum_duration_seconds=30 * 86400)))
    assert not (tmp_path / 'invalid').exists()


def test_failed_bar_worker_drains_quote_thread_and_retains_terminal(tmp_path, monkeypatch):
    from tools import gold_public_session as session
    from tests.tools.test_gold_bar_session import fixture_history
    history, _ = fixture_history(tmp_path)
    started, drained = Event(), Event()
    monkeypatch.setattr(session, 'source_identity', lambda: {'fixture': True})

    async def bars(*args, **kwargs):
        while not started.is_set():
            await asyncio.sleep(.001)
        return dict(status='INCOMPLETE', reason='DISCONNECTED')

    def quotes(*args, **kwargs):
        started.set()
        assert kwargs['cancel_event'].wait(2)
        drained.set()
        raise InterruptedError('DRAINED_ORIGINAL_WORKER')

    monkeypatch.setattr(session, 'observe_bars', bars)
    monkeypatch.setattr(session, 'observe_quotes', quotes)
    output = tmp_path / 'pair'
    result = asyncio.run(session.run(history, output, seconds=30, collection_budget=budget()))
    assert drained.is_set() and result['status'] == 'INCOMPLETE'
    assert result['workers_terminal'] is True
    assert result['errors'] == [None, 'InterruptedError']
    assert json.loads((output / 'summary.json').read_bytes()) == result
    assert result['submission_enabled'] is result['replay_ready'] is False


def test_pair_never_calls_shorter_span_a_completed_observation(tmp_path, monkeypatch):
    from tools import gold_public_session as session
    from tests.tools.test_gold_bar_session import fixture_history
    history, _ = fixture_history(tmp_path)
    monkeypatch.setattr(session, 'source_identity', lambda: {'fixture': True})

    async def bars(*args, **kwargs):
        return dict(status='OBSERVED', last_received_at_ms=15000)

    def quotes(*args, **kwargs):
        return dict(status='RAW_QUOTES_COLLECTED', quote_coverage=dict(
            first_received_at=1000, last_received_at=15000, max_gap_ms=0, gap_count=0))

    monkeypatch.setattr(session, 'observe_bars', bars)
    monkeypatch.setattr(session, 'observe_quotes', quotes)
    result = asyncio.run(session.run(history, tmp_path / 'pair', seconds=30,
                                    collection_budget=budget()))
    assert result['status'] == 'INCOMPLETE'
    assert result['reason'] == 'QUOTE_COVERAGE_BEFORE_REQUESTED_DURATION'


@pytest.mark.parametrize('retain_late_open', [False, True])
def test_pair_cold_review_reconstructs_both_original_journals_and_rejects_nonselected_damage(
        tmp_path, monkeypatch, retain_late_open):
    import shutil
    import tests.tools.test_gold_public_segment_producer as fixtures
    from tests.tools.test_gold_bar_session import fixture_history
    from tools import gold_bar_session as bars, gold_quote_session as quotes, gold_public_session as session
    history, message = fixture_history(tmp_path)
    source = {'fixture': True}
    for tool in (bars, quotes, session):
        monkeypatch.setattr(tool, 'source_identity', lambda: source)
    warmup_received = json.loads((history / 'receipt.json').read_bytes())['received_at_ms']
    monkeypatch.setattr(fixtures, 'BASE', warmup_received + 20000)
    with monkeypatch.context() as clock_patch:
        fixtures.transport(clock_patch)
        quotes.run(tmp_path / 'original-quotes', seconds=60, interval_seconds=1,
                   segment_samples=8, collection_budget=budget())

    async def stream(seconds, state, **kwargs):
        state['connected'] = True
        if retain_late_open:
            raw, received = message(0)
            late = json.loads(raw)
            received -= 10000
            late['data']['E'] = received - 30756
            late['data']['k']['x'] = False
            yield json.dumps(late), received
        yield message(0)
        await asyncio.sleep(10)

    options = dict(late_open_policy=bars.LATE_OPEN_POLICY) if retain_late_open else {}
    original_timeout = asyncio.timeout
    with monkeypatch.context() as timer_patch:
        timer_patch.setattr(bars, '_messages', stream)
        timer_patch.setattr(bars.asyncio, 'timeout', lambda seconds: original_timeout(.01))
        asyncio.run(bars.observe(history, tmp_path / 'original-bars', seconds=150,
                                 collection_budget=budget(), **options))

    async def bar_worker(history, output, **kwargs):
        assert kwargs.get('late_open_policy') == options.get('late_open_policy')
        shutil.copytree(tmp_path / 'original-bars', output)
        return json.loads((output / 'summary.json').read_bytes())

    def quote_worker(output, **kwargs):
        shutil.copytree(tmp_path / 'original-quotes', output)
        return json.loads((output / 'summary.json').read_bytes())

    monkeypatch.setattr(session, 'observe_bars', bar_worker)
    monkeypatch.setattr(session, 'observe_quotes', quote_worker)
    output = tmp_path / 'pair'
    result = asyncio.run(session.run(history, output, seconds=30, interval_seconds=1,
                                    segment_samples=8, collection_budget=budget(), **options))
    assert result['status'] == 'RAW_PAIRED_SESSION_COLLECTED'
    reviewed = session.review(output)
    assert reviewed['bars']['closed_bars'] == 1 and reviewed['quotes']['segment_count'] >= 3
    assert reviewed['source_authenticity_proven'] is reviewed['replay_ready'] is False
    if retain_late_open:
        assert reviewed['schema'] == 'GOLD_PAIRED_PUBLIC_SESSION_REVIEW_V2'
        assert reviewed['bars']['rejected_late_open_messages'] == 1
        # Rehashing the paired envelope must not downgrade its child journal policy.
        plan_path = output / 'plan.json'
        summary_path = output / 'summary.json'
        original_plan, original_summary = plan_path.read_bytes(), summary_path.read_bytes()
        plan, summary = json.loads(original_plan), json.loads(original_summary)
        plan['schema'], summary['schema'] = session.PLAN_SCHEMA, session.SUMMARY_SCHEMA
        plan.pop('late_open_policy')
        summary.pop('late_open_policy')
        plan_path.write_text(json.dumps(plan))
        import hashlib
        summary['files']['plan.json'] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        summary_path.write_text(json.dumps(summary))
        with pytest.raises(ValueError, match='POLICY'):
            session.review(output)
        plan_path.write_bytes(original_plan)
        summary_path.write_bytes(original_summary)
    path = sorted((output / 'quotes').glob('segment-*.json'))[-1]
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='CHANGED'):
        session.review(output)
