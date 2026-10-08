"""Private, bounded preparation of the original Gold tape and input identities.

Known typed records use the existing history codec. This is disposable input
indexing, never an account balance, executable object, or economic authority.
"""
from bisect import bisect_left
from collections.abc import Sequence

from tools.perpetual_stream_history import SimulationHistory, _Array, _Map, _raw
from tools import registered_research_run as files
from services.research.research_evidence_reader import _identity


class _Range(Sequence):
    def __init__(self, source, indices):
        self.source, self.indices = source, indices

    def __len__(self):
        self.source.owner._check()
        return len(self.indices)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _Range(self.source, self.indices[index])
        return self.source[self.indices[index]]

    def __deepcopy__(self, memo):
        self.source.__deepcopy__(memo)
        return self


class _Records(Sequence):
    def __init__(self, owner, name):
        self.owner, self.array = owner, _Array(owner.store, name)

    def __len__(self):
        self.owner._check()
        return len(self.array)

    def __getitem__(self, index):
        self.owner._check()
        if isinstance(index, slice):
            return _Range(self, range(*index.indices(len(self))))
        return self.array[index]

    def __iter__(self):
        self.owner._check()
        for item in self.array:
            self.owner._check()
            yield item
        self.owner._check()

    def __deepcopy__(self, memo):
        # The complete physical cut is sealed before any session receives it.
        self.owner._check()
        if self.owner.pin is None:
            raise ValueError('GOLD_INPUT_NOT_SEALED')
        return self

    def append(self, value):
        self.owner._write_check()
        self.array.append(value)

    def extend(self, values):
        for value in values:
            self.append(value)

    def replace(self, index, value):
        self.owner._write_check()
        if not 0 <= index < len(self):
            raise IndexError(index)
        self.owner.store.db.execute('UPDATE arrays SET value=? WHERE namespace=? AND ordinal=?',
                                   (_raw(value), self.array.name, index))

    def __delitem__(self, index):
        self.owner._write_check()
        del self.array[index]

    def at_time(self, timestamp):
        index = bisect_left(self, timestamp, key=lambda frame: frame.timestamp)
        return self[index] if index < len(self) and self[index].timestamp == timestamp else None


class _Projection(Sequence):
    def __init__(self, records, field):
        self.records, self.field = records, field

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _Projection(self.records[index], self.field)
        return self.records[index][self.field]

    def __deepcopy__(self, memo):
        self.records.__deepcopy__(memo)
        return self


class GoldInputHistory:
    def __init__(self, directory, *, maximum_bytes):
        self.store = SimulationHistory(directory, maximum_bytes=maximum_bytes)
        self.pin = None
        self.closed = False
        self.root_identity = None
        self.journal_bytes = 0
        for name in ('frames', 'segments', 'updates', 'decisions', 'reviews', 'bars'):
            setattr(self, name, _Records(self, name))
        self.samples = _Map(self.store, 'samples')

    def _check(self):
        if self.closed:
            raise ValueError('GOLD_INPUT_CLOSED')
        if self.pin is not None:
            files._directory(self.store.root)
            if (_identity(self.store.path.stat(follow_symlinks=False)) != self.pin[0]
                    or self.store.db.total_changes != self.pin[2]
                    or _identity(self.store.root.stat(follow_symlinks=False)) != self.root_identity):
                raise ValueError('GOLD_INPUT_CHANGED')

    def _write_check(self):
        self._check()
        if self.pin is not None:
            raise ValueError('GOLD_INPUT_SEALED')

    def flush(self):
        self._write_check()
        self.store.flush()
        if self.store.path.stat().st_size + self.journal_bytes > self.store.maximum_bytes:
            raise ValueError('GOLD_INPUT_BYTES')

    def reserve_journal(self, size):
        self._write_check()
        if self.store.path.stat().st_size + self.journal_bytes + size > self.store.maximum_bytes:
            raise ValueError('GOLD_INPUT_BYTES')
        self.journal_bytes += size

    def seal(self):
        self.flush()
        if {path.name for path in self.store.root.iterdir()} != {'history.sqlite3'}:
            raise ValueError('GOLD_INPUT_MEMBERS')
        self.pin = self.store._pin()
        self.root_identity = _identity(self.store.root.stat(follow_symlinks=False))

    def projection(self, records, field):
        return _Projection(records, field)

    def verify(self):
        self._check()
        if self.pin is not None and self.store._pin() != self.pin:
            raise ValueError('GOLD_INPUT_CHANGED')

    def __enter__(self):
        return self

    def __exit__(self, error_type, *unused):
        try:
            if error_type is None:
                self.verify()
        finally:
            try:
                self.store.__exit__(error_type, *unused)
            finally:
                self.closed = True
