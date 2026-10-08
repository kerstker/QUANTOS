"""Trusted offline storage for the original Gold producer and its five accounts.

The original callbacks, execution session and cash ledger remain the producer.
These histories are scratch records, never balances to restore or authority.
Returned views are usable only while the context holds their unchanged cut.
"""
from collections.abc import MutableSet, Sequence
from itertools import islice
from pathlib import Path

from quantos.streaming_identity import MAX_BYTES, StreamArray, StreamObject
from services.execution.perpetual_simulation import LinearPerpetualSimulationSession
from services.research.research_evidence_reader import _identity
from tools import registered_research_run as files
from tools.perpetual_stream_history import SimulationHistory, _Array, _Map


class _HistoryArray(Sequence):
    def __init__(self, owner, store, name):
        self.owner, self.store = owner, store
        self.array = _Array(store, name)

    def __len__(self):
        self.owner._check(self.store)
        return len(self.array)

    def __getitem__(self, index):
        self.owner._check(self.store, full=True)
        return self.array[index]

    def __iter__(self):
        self.owner._check(self.store, full=True)
        for item in self.array:
            self.owner._check(self.store)
            yield item
        self.owner._check(self.store, full=True)

    def append(self, value):
        self.owner._write_check(self.store)
        self.array.append(value)


class _HistorySet(MutableSet):
    def __init__(self, owner, store, name):
        self.owner, self.store = owner, store
        self.mapping = _Map(store, name)

    @classmethod
    def _from_iterable(cls, values):
        # The original portfolio subtracts owned IDs from its pending orders.
        return set(values)

    def __contains__(self, key):
        self.owner._check(self.store)
        return key in self.mapping

    def __len__(self):
        self.owner._check(self.store)
        return len(self.mapping)

    def __iter__(self):
        self.owner._check(self.store, full=True)
        for key in self.mapping:
            self.owner._check(self.store)
            yield key
        self.owner._check(self.store, full=True)

    def add(self, key):
        self.owner._write_check(self.store)
        self.mapping[key] = True

    def discard(self, key):
        self.owner._write_check(self.store)
        if key in self.mapping:
            del self.mapping[key]


class _BarAppend(Sequence):
    """Validate a received suffix without first rewriting the retained history."""
    def __init__(self, prefix, incoming):
        self.prefix, self.incoming = prefix, incoming
        self.count = len(prefix)

    def __len__(self):
        return self.count + len(self.incoming)

    def __iter__(self):
        if len(self.prefix) != self.count:
            raise ValueError('GOLD_BAR_HISTORY_CHANGED')
        yield from self.prefix
        yield from self.incoming

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return self.prefix[index] if index < self.count else self.incoming[index-self.count]


class _BarHistory(_HistoryArray):
    def __add__(self, incoming):
        self.owner._write_check(self.store)
        return _BarAppend(self, incoming)

    def append_validated(self, incoming):
        self.owner._write_check(self.store)
        self.array.extend(incoming)


class _ReportArray(Sequence):
    def __init__(self, owner, store, items):
        self.owner, self.store, self.items = owner, store, items

    def __iter__(self):
        self.owner._check(self.store, full=True)
        for value in self.items():
            self.owner._check(self.store)
            yield self.owner._view(self.store, value)
        self.owner._check(self.store, full=True)

    def __len__(self):
        return sum(1 for _ in self)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError(index)
        sentinel = object()
        value = next(islice(iter(self), index, index + 1), sentinel)
        if value is sentinel:
            raise IndexError(index)
        return value


class GoldResearchHistory:
    """Aggregate disk budget and a retained cut of the original Gold histories."""
    def __init__(self, directory, *, maximum_history_bytes):
        if type(maximum_history_bytes) is not int or not 0 < maximum_history_bytes <= MAX_BYTES:
            raise ValueError('GOLD_HISTORY_BYTE_BUDGET')
        self.root = Path(directory).absolute()
        if self.root.resolve() != self.root:
            raise ValueError('GOLD_HISTORY_PATH')
        self.root.mkdir(mode=0o700, exist_ok=False)
        files._directory(self.root)
        self.maximum_bytes = maximum_history_bytes
        self.accounts = []
        self._sealed = {}
        self._closed = False
        self._root_identity = _identity(self.root.stat(follow_symlinks=False))

    def _check(self, store=None, *, full=False):
        if self._closed:
            raise ValueError('GOLD_HISTORY_CLOSED')
        files._directory(self.root)
        info = self.root.stat(follow_symlinks=False)
        if (info.st_dev, info.st_ino) != self._root_identity[:2]:
            raise ValueError('GOLD_HISTORY_CHANGED')
        if store in self._sealed:
            expected = self._sealed[store]
            if _identity(store.path.stat(follow_symlinks=False)) != expected[0]:
                raise ValueError('GOLD_HISTORY_CHANGED')
            if full and store._pin() != expected:
                raise ValueError('GOLD_HISTORY_CHANGED')

    def _write_check(self, store):
        self._check(store)
        if store in self._sealed:
            raise ValueError('GOLD_HISTORY_SEALED')

    def new_session(self, configuration, *, strategy=None):
        self.verify()
        if len(self.accounts) >= 5 or len(self._sealed) != len(self.accounts):
            raise ValueError('GOLD_HISTORY_ACCOUNT_SCOPE')
        remaining = self.maximum_bytes - sum(a._history_store.path.stat().st_size for a in self.accounts)
        if remaining <= 0:
            raise ValueError('GOLD_HISTORY_BYTES')
        store = SimulationHistory(self.root / str(len(self.accounts)), maximum_bytes=remaining)
        try:
            session = LinearPerpetualSimulationSession(history_store=store, **configuration)
            self.accounts.append(session)
            if strategy is not None:
                if hasattr(strategy, 'bars'):
                    if type(strategy.bars) is not tuple or strategy.last_time is not None:
                        raise ValueError('GOLD_HISTORY_REQUIRES_PRISTINE_STRATEGY')
                    retained = _BarHistory(self, store, 'strategy_bars')
                    retained.append_validated(strategy.bars)
                    strategy.bars = retained
                for name in ('observations', 'events', 'trace', 'allocation_intents'):
                    if hasattr(strategy, name):
                        if type(getattr(strategy, name)) is not list or getattr(strategy, name):
                            raise ValueError('GOLD_HISTORY_REQUIRES_PRISTINE_STRATEGY')
                        setattr(strategy, name, _HistoryArray(self, store, 'strategy_' + name))
                for name in ('consumed', 'orders'):
                    value = getattr(strategy, name, None)
                    if type(value) is set:
                        if value:
                            raise ValueError('GOLD_HISTORY_REQUIRES_PRISTINE_STRATEGY')
                        setattr(strategy, name, _HistorySet(self, store, 'strategy_' + name))
            return session
        except BaseException:
            store.db.close()
            raise

    def advance(self, session, frames, *, environment=None):
        self._write_check(session._history_store)
        iterator = iter(frames)
        first = True
        while batch := tuple(islice(iterator, session._history_store.maximum_batch_frames)):
            if first and environment is not None:
                session.advance_environment(batch, **environment)
            else:
                session.advance(batch)
            first = False
        if first:
            raise ValueError('GOLD_HISTORY_EMPTY_BATCH')

    def run(self, configuration, *, strategy=None):
        # The same causal epoch dispatcher as the original one-batch runner.
        from services.execution.perpetual_environment import advance_segments
        configuration = dict(configuration)
        frames = configuration.pop('frames')
        updates = configuration.pop('environment_updates', None)
        session = self.new_session(configuration, strategy=strategy)
        if updates is None:
            self.advance(session, frames)
        else:
            owner = self
            class BatchedSession:
                _replay_budget = session._replay_budget

                def advance(self, frames):
                    owner.advance(session, frames)

                def advance_environment(self, frames, **environment):
                    owner.advance(session, frames, environment=environment)
            advance_segments(BatchedSession(), tuple(frames), updates)
        return self.result(session)

    def _view(self, store, value):
        if isinstance(value, StreamArray):
            return _ReportArray(self, store, value.items)
        if isinstance(value, StreamObject):
            def items():
                self._check(store, full=True)
                for key, item in value.items():
                    self._check(store)
                    yield key, self._view(store, item)
                self._check(store, full=True)
            return StreamObject(items)
        if type(value) is dict:
            return {k: self._view(store, v) for k, v in value.items()}
        if type(value) in (tuple, list):
            return [self._view(store, item) for item in value]
        return value

    def result(self, session):
        store = session._history_store
        self._write_check(store)
        report = session.report_stream()
        self._sealed[store] = store._pin()
        self.verify()
        return self._view(store, report)

    def verify(self):
        self._check()
        if {p.name for p in self.root.iterdir()} != {str(i) for i in range(len(self.accounts))}:
            raise ValueError('GOLD_HISTORY_MEMBERS')
        total = 0
        for account in self.accounts:
            store = account._history_store
            files._directory(store.root)
            if {p.name for p in store.root.iterdir()} != {'history.sqlite3'}:
                raise ValueError('GOLD_HISTORY_MEMBERS')
            self._check(store, full=True)
            total += store.path.stat().st_size
        if total > self.maximum_bytes:
            raise ValueError('GOLD_HISTORY_BYTES')

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, error_type, *unused):
        try:
            if error_type is None:
                self.verify()
        finally:
            self._closed = True
            for account in self.accounts:
                account._history_store.db.close()
