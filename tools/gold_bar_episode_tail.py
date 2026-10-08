"""Immutable closed-bar continuation for an original protected Demo episode.

Append and snapshot are serialized; snapshots retain immutable prefixes.
"""
from copy import deepcopy
from dataclasses import asdict
from itertools import islice
from functools import wraps
from threading import RLock
from pathlib import Path
import re

from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _retain
from tools.demo_reported_recovery import _read
from tools.demo_resident_recovery import _check_path
from tools.gold_bar_demo_entry import BarDemoEntry

MAX_RECORDS = 128


def require(ok, reason):
    if not ok:
        raise ValueError('BAR_EPISODE_TAIL_'+reason)


def serialized(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return invoke


def _binding(engine, bundle_sha256, entry_decision_ms):
    require(type(engine) is BarDemoEntry and type(bundle_sha256) is str
        and re.fullmatch('[a-f0-9]{64}', bundle_sha256) is not None, 'INPUT')
    return dict(bundle_sha256=bundle_sha256,
        definition_hash=engine.verified_definition()['definition_hash'],
        entry_decision_ms=entry_decision_ms, signal_sha256=_digest(engine.signal_at(entry_decision_ms)),
        bars_sha256=_digest([asdict(b) for b in engine._cursor.bars]), submission_enabled=False)


def _replay_row(engine, row, *, binding, ordinal, previous):
    require(type(row) is dict and set(row) == {'binding_sha256','ordinal','previous_sha256','kind','received_at_ms','raw','reason'}
        and row['binding_sha256'] == _digest(binding)
        and type(row['ordinal']) is int and row['ordinal'] == ordinal
        and row['previous_sha256'] == previous
        and not engine._cursor.blocked, 'CHAIN')
    require(type(row['received_at_ms']) is int
        and row['received_at_ms'] >= engine._cursor.last_received, 'TIME')
    if row['kind'] == 'CLOSED_BAR':
        require(row['reason'] is None, 'REASON')
        result = engine.ingest(row['raw'], received_at_ms=row['received_at_ms'])
        require(result['status'] == 'CLOSED_BAR', 'FINALITY')
    else:
        require(row['kind'] == 'GAP' and row['raw'] is None
            and type(row['reason']) is str and 0 < len(row['reason']) <= 100, 'GAP')
        engine._cursor.last_received = row['received_at_ms']
        engine._cursor.blocked = True


def restore_snapshot(root, *, bundle_sha256, engine, entry_decision_ms, snapshot_sha256):
    """Read only the named decision prefix, never later appended market data."""
    require(type(snapshot_sha256) is str and re.fullmatch('[a-f0-9]{64}', snapshot_sha256) is not None,
        'SNAPSHOT_ID')
    root = Path(root).absolute()
    require(root.resolve() == root, 'ROOT')
    binding = _binding(engine, bundle_sha256, entry_decision_ms)
    area = root/'strategy-bar-tails'/bundle_sha256
    manifest = _read(area/'snapshots'/(snapshot_sha256+'.json'), root, 200000)
    require(type(manifest) is dict and set(manifest) == {'binding', 'prefix'}
        and manifest['binding'] == binding, 'SNAPSHOT_BINDING')
    require(_read(area/'binding'/(_digest(binding)+'.json'), root) == binding, 'BINDING')
    prefix = manifest['prefix']
    require(type(prefix) is list and len(prefix) <= MAX_RECORDS
        and all(type(v) is str and re.fullmatch('[a-f0-9]{64}', v) is not None for v in prefix), 'PREFIX')
    rebuilt = deepcopy(engine)
    previous = None
    for ordinal, digest in enumerate(prefix, 1):
        row = _read(area/'events'/(digest+'.json'), root, 200000)
        _replay_row(rebuilt, row, binding=binding, ordinal=ordinal, previous=previous)
        previous = digest
    return rebuilt


class EpisodeTail:
    def __init__(self, root, *, bundle_sha256, engine, entry_decision_ms):
        require(type(engine) is BarDemoEntry and type(bundle_sha256) is str
            and re.fullmatch('[a-f0-9]{64}', bundle_sha256) is not None, 'INPUT')
        self.root = Path(root).absolute()
        require(self.root.resolve() == self.root, 'ROOT')
        uid = self.root.stat().st_uid
        _check_path(self.root, uid, directory=True)
        self.area = self.root/'strategy-bar-tails'/bundle_sha256
        for directory in (self.area.parent, self.area, self.area/'events', self.area/'snapshots'):
            directory.mkdir(mode=0o700, exist_ok=True)
            _check_path(directory, uid, directory=True)
        self._lock = RLock()
        self.original = engine
        self.entry_decision_ms = entry_decision_ms
        self.binding = _binding(engine, bundle_sha256, entry_decision_ms)
        binding_dir = self.area/'binding'
        if binding_dir.exists():
            require(_read(binding_dir/(_digest(self.binding)+'.json'), self.root) == self.binding, 'BINDING')
        else:
            require(not list((self.area/'events').iterdir()), 'BINDING_MISSING')
            _retain(binding_dir, self.binding)
        self.engine = deepcopy(engine)
        self.records = []
        paths = list(islice((self.area/'events').iterdir(), MAX_RECORDS+1))
        require(len(paths) <= MAX_RECORDS, 'BUDGET')
        rows = [(path, _read(path, self.root, 200000)) for path in paths]
        require(all(type(row.get('ordinal')) is int for _, row in rows), 'ORDINAL')
        for path, row in sorted(rows, key=lambda item: item[1]['ordinal']):
            _replay_row(self.engine, row, binding=self.binding, ordinal=len(self.records)+1,
                previous=self.records[-1][1] if self.records else None)
            self.records.append((path, _digest(row), row))

    def _save(self, kind, received, raw=None, reason=None):
        require(len(self.records) < MAX_RECORDS, 'BUDGET')
        row = dict(binding_sha256=_digest(self.binding), ordinal=len(self.records)+1,
            previous_sha256=self.records[-1][1] if self.records else None,
            kind=kind, received_at_ms=received, raw=raw, reason=reason)
        path = _retain(self.area/'events', row)
        self.records.append((path, _digest(row), row))

    @serialized
    def gap(self, *, received_at_ms, reason):
        require(type(received_at_ms) is int and received_at_ms >= self.engine._cursor.last_received
            and type(reason) is str and 0 < len(reason) <= 100, 'GAP')
        require(not self.engine._cursor.blocked, 'BLOCKED')
        self._save('GAP', received_at_ms, reason=reason)
        self.engine._cursor.last_received = received_at_ms
        self.engine._cursor.blocked = True

    @serialized
    def append(self, raw, *, received_at_ms):
        require(not self.engine._cursor.blocked, 'BLOCKED')
        require(len(self.records) < MAX_RECORDS, 'BUDGET')
        candidate = deepcopy(self.engine)
        try:
            result = candidate.ingest(raw, received_at_ms=received_at_ms)
        except (ValueError, TypeError, KeyError) as exc:
            self.gap(received_at_ms=max(self.engine._cursor.last_received, received_at_ms)
                if type(received_at_ms) is int else self.engine._cursor.last_received,
                reason=type(exc).__name__)
            raise
        if result['status'] == 'CLOSED_BAR':
            self._save('CLOSED_BAR', received_at_ms, raw=raw)
        self.engine = candidate
        return result

    @serialized
    def snapshot(self):
        require(self.original.verified_definition()['definition_hash'] == self.binding['definition_hash']
            and _digest(self.original.signal_at(self.entry_decision_ms)) == self.binding['signal_sha256'], 'ORIGINAL_CHANGED')
        records = tuple((path, digest, deepcopy(row)) for path, digest, row in self.records)
        def recheck():
            require(all(_read(path, self.root, 200000) == row and _digest(row) == digest
                for path, digest, row in records), 'PREFIX_CHANGED')
            return _digest(dict(binding=self.binding, prefix=[digest for _, digest, _ in records]))
        digest = recheck()
        _retain(self.area/'snapshots', dict(binding=self.binding, prefix=[value for _, value, _ in records]))
        rebuilt = restore_snapshot(self.root, bundle_sha256=self.binding['bundle_sha256'],
            engine=self.original, entry_decision_ms=self.entry_decision_ms, snapshot_sha256=digest)
        require(rebuilt._cursor.bars == self.engine._cursor.bars
            and rebuilt._cursor.blocked == self.engine._cursor.blocked
            and rebuilt._last == self.engine._last, 'MEMORY_PREFIX_MISMATCH')
        return deepcopy(self.engine), digest, recheck


async def observe(tail, *, seconds):
    """Bounded protected public stream; caller owns task lifetime and serialization."""
    import asyncio
    import time
    from contextlib import aclosing
    from live.exchange import demo_network
    from tools.gold_bar_observe import _messages
    require(type(tail) is EpisodeTail and type(seconds) is int and 1 <= seconds <= 86400,
        'OBSERVATION_INPUT')
    options = {'proxy': demo_network.proxy_url(), 'ssl': demo_network.tls_context()}
    transport = dict(connected=False, errors=[])
    timer = asyncio.timeout(seconds)
    reason = 'TRANSPORT_EXHAUSTED'
    try:
        async with timer:
            async with aclosing(_messages(seconds, transport, options)) as stream:
                async for raw, received in stream:
                    tail.append(raw, received_at_ms=received)
    except TimeoutError:
        reason = 'DURATION_COMPLETE' if timer.expired() else 'TRANSPORT_TIMEOUT'
    except asyncio.CancelledError:
        # The owner intentionally stopped this bounded observer, e.g. at shutdown.
        raise
    except Exception as exc:
        reason = type(exc).__name__
    if reason != 'DURATION_COMPLETE' and not tail.engine._cursor.blocked:
        tail.gap(received_at_ms=max(time.time_ns()//1000000, tail.engine._cursor.last_received), reason=reason)
    return dict(status='OBSERVED' if reason == 'DURATION_COMPLETE' else 'INCOMPLETE',
        reason=reason, transport=transport, journal_records=len(tail.records),
        submission_enabled=False, economic_authority=False)
