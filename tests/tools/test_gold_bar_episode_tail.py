import json

import pytest

from services.research.gold_bar_stream import GoldBarStream
from services.research.gold_bar_dataset import load_bars
from tests.research.test_gold_bar_dataset import raw_rows, STEP
from tools.gold_bar_demo_entry import BarDemoEntry


def message(at, price='109'):
    received = at+STEP+20
    return json.dumps(dict(stream='xauusdt@kline_15m', data=dict(e='kline', E=received,
        s='XAUUSDT', k=dict(t=at, T=at+STEP-1, s='XAUUSDT', i='15m', x=True,
            o='100', h='110', l='89' if price == '90' else '99', c=price)))), received


def engine(monkeypatch):
    instance = object.__new__(BarDemoEntry)
    instance._registered = 1000*STEP+100
    instance._definition = {'definition_hash': 'd'*64, 'candidate': 'channel'}
    instance._last = None
    instance._cursor = GoldBarStream(load_bars(json.dumps(raw_rows()).encode(),
        requested_at_ms=1000*STEP, received_at_ms=1000*STEP+10))
    monkeypatch.setattr(BarDemoEntry, 'verified_definition', lambda self: dict(self._definition))
    raw, at = message(1000*STEP)
    instance.ingest(raw, received_at_ms=at)
    return instance, at


def test_tail_restores_original_entry_and_snapshot_allows_later_append(tmp_path, monkeypatch):
    from tools.gold_bar_episode_tail import EpisodeTail
    original, entry = engine(monkeypatch)
    args = dict(bundle_sha256='b'*64, engine=original, entry_decision_ms=entry)
    tail = EpisodeTail(tmp_path, **args)
    raw, at = message(1001*STEP, '90')
    tail.append(raw, received_at_ms=at)
    frozen, digest, recheck = tail.snapshot()
    assert frozen.entry_signal(entry) == original.entry_signal(entry)
    from services.research.gold_bar_candidates import evaluate
    assert evaluate(frozen._cursor.bars, candidate='channel', as_of_ms=at)['exit_long'] is True
    assert frozen._cursor.bars[-1].close_ms > original._cursor.bars[-1].close_ms
    raw, at = message(1002*STEP)
    tail.append(raw, received_at_ms=at)
    assert recheck() == digest
    restored = EpisodeTail(tmp_path, **args).snapshot()[0]
    assert restored._cursor.bars == tail.engine._cursor.bars
    assert restored.entry_signal(entry) == original.entry_signal(entry)
    path = tail.records[0][0]
    path.write_text('{}')
    with pytest.raises(ValueError):
        recheck()


def test_transport_gap_survives_restart_and_blocks_signal_cursor(tmp_path, monkeypatch):
    from tools.gold_bar_episode_tail import EpisodeTail
    original, entry = engine(monkeypatch)
    args = dict(bundle_sha256='b'*64, engine=original, entry_decision_ms=entry)
    tail = EpisodeTail(tmp_path, **args)
    tail.gap(received_at_ms=entry+10, reason='TRANSPORT_LOST')
    restored = EpisodeTail(tmp_path, **args)
    assert restored.snapshot()[0]._cursor.blocked
    raw, at = message(1001*STEP)
    with pytest.raises(ValueError, match='BLOCKED'):
        restored.append(raw, received_at_ms=at)


def test_protected_observer_feeds_tail_and_retains_exhausted_transport(tmp_path, monkeypatch):
    import asyncio
    from tools import gold_bar_episode_tail as module, gold_bar_observe
    from live.exchange import demo_network
    original, entry = engine(monkeypatch)
    tail = module.EpisodeTail(tmp_path, bundle_sha256='b'*64, engine=original, entry_decision_ms=entry)
    monkeypatch.setattr(demo_network, 'proxy_url', lambda: 'http://fixture.invalid:1234')
    context = object()
    monkeypatch.setattr(demo_network, 'tls_context', lambda: context)
    async def messages(seconds, transport, options):
        assert options == {'proxy': 'http://fixture.invalid:1234', 'ssl': context}
        transport['connected'] = True
        yield message(1001*STEP, '90')
        transport['connected'] = False
    monkeypatch.setattr(gold_bar_observe, '_messages', messages)
    result = asyncio.run(module.observe(tail, seconds=30))
    assert result['status'] == 'INCOMPLETE' and result['reason'] == 'TRANSPORT_EXHAUSTED'
    restored = module.EpisodeTail(tmp_path, bundle_sha256='b'*64, engine=original, entry_decision_ms=entry)
    assert len(restored.records) == 2
    assert restored.snapshot()[0]._cursor.blocked


def test_fixed_prefix_replays_without_writing_or_using_later_bars(tmp_path, monkeypatch):
    from tools.gold_bar_episode_tail import EpisodeTail, restore_snapshot
    original, entry = engine(monkeypatch)
    args = dict(bundle_sha256='b'*64, engine=original, entry_decision_ms=entry)
    tail = EpisodeTail(tmp_path, **args)
    raw, at = message(1001*STEP, '90')
    tail.append(raw, received_at_ms=at)
    frozen, digest, _ = tail.snapshot()
    raw, at = message(1002*STEP)
    tail.append(raw, received_at_ms=at)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    rebuilt = restore_snapshot(tmp_path, snapshot_sha256=digest, **args)
    assert rebuilt._cursor.bars == frozen._cursor.bars
    assert rebuilt._cursor.bars != tail.engine._cursor.bars
    assert rebuilt.entry_signal(entry) == original.entry_signal(entry)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    tail.records[0][0].write_text('{}')
    with pytest.raises(ValueError):
        restore_snapshot(tmp_path, snapshot_sha256=digest, **args)


def test_snapshot_missing_never_creates_directories(tmp_path, monkeypatch):
    from tools.gold_bar_episode_tail import restore_snapshot
    original, entry = engine(monkeypatch)
    with pytest.raises((ValueError, FileNotFoundError)):
        restore_snapshot(tmp_path, bundle_sha256='b'*64, engine=original,
                         entry_decision_ms=entry, snapshot_sha256='c'*64)
    assert list(tmp_path.iterdir()) == []


def test_unretained_memory_change_cannot_become_a_decision_snapshot(tmp_path, monkeypatch):
    from tools.gold_bar_episode_tail import EpisodeTail
    original, entry = engine(monkeypatch)
    tail = EpisodeTail(tmp_path, bundle_sha256='b'*64, engine=original, entry_decision_ms=entry)
    raw, at = message(1001*STEP, '90')
    tail.engine.ingest(raw, received_at_ms=at)
    with pytest.raises(ValueError, match='MEMORY_PREFIX_MISMATCH'):
        tail.snapshot()
