import asyncio
from threading import Event
from types import SimpleNamespace

from tests.tools.test_gold_bar_episode_tail import engine, message, STEP


def test_worker_session_reuses_observer_and_closes_before_rotation(tmp_path, monkeypatch):
    from tools import gold_bar_sessions as sessions
    from tools import gold_bar_fresh_inputs
    events = []
    ready = Event()
    original, entry = engine(monkeypatch)

    def episode(root, bundle, **kwargs):
        events.append(('create', bundle['episode_id']))
        return SimpleNamespace(engine=original, checked={'definition_hash': 'd'*64},
            entry_decision_ms=entry, recheck=lambda: {'definition_hash': 'd'*64})

    async def observe(tail, *, seconds):
        events.append(('start', tail.binding['bundle_sha256']))
        raw, at = message(1001*STEP, '90')
        tail.append(raw, received_at_ms=at)
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append(('stopped', tail.binding['bundle_sha256']))

    monkeypatch.setattr(gold_bar_fresh_inputs, 'FreshEpisode', episode)
    monkeypatch.setattr(sessions, 'observe', observe)
    bundle = {'episode_id': 'a'*64}
    kwargs = dict(source_commit='c'*40, uid=tmp_path.stat().st_uid, account='d'*64)
    with sessions.BarSessions() as manager:
        first = manager.frame(tmp_path, bundle, **kwargs)
        assert ready.wait(2)
        second = manager.frame(tmp_path, bundle, **kwargs)
        assert second[0]._cursor.bars[-1].close_ms == 1002*STEP
        assert second[0].entry_signal(entry) == original.entry_signal(entry)
        assert first[3]() == first[1]
        assert len([x for x in events if x[0] == 'create']) == 1
        ready.clear()
        manager.frame(tmp_path, {'episode_id': 'b'*64}, **kwargs)
        assert ready.wait(2)
        assert events[2][0] == 'stopped' and events[3] == ('create', 'b'*64)
    assert len([x for x in events if x[0] == 'stopped']) == 2


def test_observer_failure_latches_gap_and_still_closes(tmp_path, monkeypatch):
    from tools import gold_bar_sessions as sessions
    from tools.gold_bar_episode_tail import EpisodeTail
    original, entry = engine(monkeypatch)
    tail = EpisodeTail(tmp_path, bundle_sha256='b'*64, engine=original, entry_decision_ms=entry)
    async def failed(*args, **kwargs):
        raise ConnectionError('fixture')
    monkeypatch.setattr(sessions, 'observe', failed)
    observer = sessions.Observer(tail)
    assert observer.done.wait(2)
    assert tail.snapshot()[0]._cursor.blocked
    observer.close()
    assert not observer.thread.is_alive()


def test_worker_serve_closes_sessions_on_failed_frame(monkeypatch):
    from tools import gold_strategy_worker as worker, gold_bar_sessions as sessions
    events = []
    class Manager:
        def __enter__(self):
            events.append('start')
            return self
        def __exit__(self, *args):
            events.append('close')
    def run(**kwargs):
        assert isinstance(kwargs['bar_sessions'], Manager)
        events.append('failed-frame')
        return 3
    monkeypatch.setattr(sessions, 'BarSessions', Manager)
    monkeypatch.setattr(worker, 'run', run)
    assert worker.serve(source_commit='c'*40, frames=2) == 3
    assert events == ['start', 'failed-frame', 'close']
