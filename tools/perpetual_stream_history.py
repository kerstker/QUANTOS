"""Offline history storage for the original account; no cash or execution formulas.

SQLite holds typed records and lookups, never an independently restored balance.
Restoration still replays retained inputs with fresh callbacks on the V2 ledger.
"""

from collections.abc import MutableMapping, Sequence
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
import hashlib
import os
from pathlib import Path
import sqlite3
import stat

from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from quantos.contracts import perpetual_chunked_result as result_contract
from quantos.contracts.registered_research_run import canonical, MAX_INPUT_BYTES
from quantos.streaming_identity import exact_stream_identity_hash, json_parts, key_order, MAX_BYTES
from services.execution.authoritative_perpetual_backtest import (
    CapacitySnapshot, HistoricalContractRule, FundingPolicySnapshot,
)
from services.execution.venue_order_filters import VenueOrderFilters, PriceFilter, QuantityFilter
from services.execution.perpetual_ledger_v2 import (
    CapacityAction,
    CapacityPolicy,
    ContractSpec,
    MarginBracket,
    FundingEvent,
    LiquidationBackstop,
    LiquidationRecord,
    MarginMode,
    MarkEvent,
)
from services.execution.perpetual_simulation import (
    EquityPoint,
    PositionValuation,
    ExecutableQuote,
    OrderIntent,
    OrderState,
    OrderRuleProfile,
    VenueOrderConstraints,
    ForecastFundingPolicySnapshot,
    RollingForecastFundingPolicySnapshot,
    SimulationFrame,
)
from services.research.research_evidence_reader import _open, _identity, _ReadSet
from services.research.gold_bar_candidates import Bar
from tools import registered_research_run as files

TYPES = {
    c.__name__: c
    for c in (
        Bar,
        HistoricalContractRule,
        FundingPolicySnapshot,
        ForecastFundingPolicySnapshot,
        RollingForecastFundingPolicySnapshot,
        ContractSpec,
        MarginBracket,
        OrderRuleProfile,
        VenueOrderConstraints,
        VenueOrderFilters,
        PriceFilter,
        QuantityFilter,
        CapacitySnapshot,
        CapacityPolicy,
        FundingEvent,
        LiquidationBackstop,
        LiquidationRecord,
        MarkEvent,
        EquityPoint,
        PositionValuation,
        ExecutableQuote,
        OrderIntent,
        OrderState,
        SimulationFrame,
    )
}
ENUMS = {c.__name__: c for c in (CapacityAction, MarginMode)}
MAX_PARTS = 4096


def _encode(value):
    if isinstance(value, Enum):
        if ENUMS.get(type(value).__name__) is not type(value):
            raise TypeError("HISTORY_ENUM")
        return ["enum", type(value).__name__, value.value]
    if type(value) is Decimal:
        return ["decimal", str(value)]
    if is_dataclass(value):
        if TYPES.get(type(value).__name__) is not type(value):
            raise TypeError("HISTORY_TYPE")
        return ["record", type(value).__name__, {f.name: _encode(getattr(value, f.name)) for f in fields(value)}]
    if type(value) in (tuple, list):
        return ["tuple" if type(value) is tuple else "list", [_encode(v) for v in value]]
    if type(value) is dict:
        return ["dict", [[_encode(k), _encode(v)] for k, v in value.items()]]
    if value is None or type(value) in (str, int, bool):
        return ["scalar", value]
    raise TypeError("HISTORY_TYPE")


def _decode(value):
    if type(value) is not list or not value:
        raise ValueError("HISTORY_RECORD")
    kind = value[0]
    if kind == "scalar" and len(value) == 2 and (value[1] is None or type(value[1]) in (str, int, bool)):
        return value[1]
    if kind == "decimal" and len(value) == 2 and type(value[1]) is str:
        number = Decimal(value[1])
        if not number.is_finite():
            raise ValueError("HISTORY_DECIMAL")
        return number
    if kind == "enum" and len(value) == 3 and value[1] in ENUMS:
        return ENUMS[value[1]](value[2])
    if kind == "record" and len(value) == 3 and value[1] in TYPES and type(value[2]) is dict:
        cls = TYPES[value[1]]
        if set(value[2]) != {f.name for f in fields(cls)}:
            raise ValueError("HISTORY_FIELDS")
        return cls(**{k: _decode(v) for k, v in value[2].items()})
    if kind in ("tuple", "list") and len(value) == 2 and type(value[1]) is list:
        items = [_decode(v) for v in value[1]]
        return tuple(items) if kind == "tuple" else items
    if kind == "dict" and len(value) == 2 and type(value[1]) is list:
        pairs = [(_decode(k), _decode(v)) for k, v in value[1]]
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("HISTORY_DUPLICATE_KEY")
        return result
    raise ValueError("HISTORY_RECORD")


def _raw(value):
    return canonical(_encode(value), maximum=MAX_INPUT_BYTES)


def _display(key):
    return "|".join(str(part) for part in key) if type(key) is tuple else str(key)


class _Array(Sequence):
    def __init__(self, store, name):
        self.store, self.name, self.length = store, name, 0

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(self.length))]
        if index < 0:
            index += self.length
        if not 0 <= index < self.length:
            raise IndexError(index)
        row = self.store.db.execute(
            "SELECT value FROM arrays WHERE namespace=? AND ordinal=?", (self.name, index)
        ).fetchone()
        if row is None:
            raise ValueError("HISTORY_MISSING_ROW")
        return _decode(decode(row[0]))

    def __iter__(self):
        expected = 0
        for ordinal, raw in self.store.db.execute(
            "SELECT ordinal,value FROM arrays WHERE namespace=? ORDER BY ordinal", (self.name,)
        ):
            if ordinal != expected:
                raise ValueError("HISTORY_ROW_ORDER")
            expected += 1
            yield _decode(decode(raw))
        if expected != self.length:
            raise ValueError("HISTORY_ROW_COUNT")

    def append(self, value):
        self.store.db.execute("INSERT INTO arrays VALUES(?,?,?)", (self.name, self.length, _raw(value)))
        self.length += 1

    def extend(self, values):
        for value in values:
            self.append(value)

    def __iadd__(self, values):
        self.extend(values)
        return self

    def __delitem__(self, index):
        if not isinstance(index, slice) or index.step is not None or index.stop is not None:
            raise ValueError("HISTORY_ONLY_SUFFIX_TRUNCATION")
        start = index.indices(self.length)[0]
        self.store.db.execute("DELETE FROM arrays WHERE namespace=? AND ordinal>=?", (self.name, start))
        self.length = start


class _Map(MutableMapping):
    def __init__(self, store, name):
        self.store, self.name, self.length = store, name, 0

    def __len__(self):
        return self.length

    def __getitem__(self, key):
        row = self.store.db.execute(
            "SELECT value FROM mappings WHERE namespace=? AND key=?", (self.name, _raw(key))
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return _decode(decode(row[0]))

    def __setitem__(self, key, value):
        raw = _raw(key)
        row = self.store.db.execute(
            "SELECT ordinal FROM mappings WHERE namespace=? AND key=?", (self.name, raw)
        ).fetchone()
        ordinal = self.length if row is None else row[0]
        self.store.db.execute(
            "INSERT INTO mappings VALUES(?,?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value",
            (self.name, raw, ordinal, key_order(_display(key)), _raw(value)),
        )
        if row is None:
            self.length += 1

    def __delitem__(self, key):
        cursor = self.store.db.execute("DELETE FROM mappings WHERE namespace=? AND key=?", (self.name, _raw(key)))
        if not cursor.rowcount:
            raise KeyError(key)
        self.length -= 1

    def __iter__(self):
        for (raw,) in self.store.db.execute(
            "SELECT key FROM mappings WHERE namespace=? ORDER BY ordinal", (self.name,)
        ):
            yield _decode(decode(raw))

    def values(self):
        for (raw,) in self.store.db.execute(
            "SELECT value FROM mappings WHERE namespace=? ORDER BY ordinal", (self.name,)
        ):
            yield _decode(decode(raw))

    def sorted_items(self):
        for key, value in self.store.db.execute(
            "SELECT key,value FROM mappings WHERE namespace=? ORDER BY sort_key", (self.name,)
        ):
            yield _decode(decode(key)), _decode(decode(value))


class SimulationHistory:
    """Private scratch history for one original session, with explicit disk budgets."""

    maximum_batch_frames = 4096

    def __init__(self, directory, *, maximum_bytes):
        if type(maximum_bytes) is not int or not 0 < maximum_bytes <= MAX_BYTES:
            raise ValueError("HISTORY_BYTE_BUDGET")
        self.root = Path(directory).absolute()
        if self.root.resolve() != self.root:
            raise ValueError("HISTORY_PATH")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        files._directory(self.root)
        self.path = self.root / "history.sqlite3"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        initial = self.path.stat()
        self._inode = initial.st_dev, initial.st_ino
        self._disk_identity = None
        self.maximum_bytes = maximum_bytes
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.execute(
            "CREATE TABLE arrays(namespace TEXT,ordinal INTEGER,value BLOB,PRIMARY KEY(namespace,ordinal)) WITHOUT ROWID"
        )
        self.db.execute(
            "CREATE TABLE mappings(namespace TEXT,key BLOB,ordinal INTEGER,sort_key TEXT,value BLOB,PRIMARY KEY(namespace,key)) WITHOUT ROWID"
        )
        self.db.execute("CREATE INDEX mapping_order ON mappings(namespace,ordinal)")
        self.db.execute("CREATE INDEX mapping_sort ON mappings(namespace,sort_key)")
        self.frames = _Array(self, "frames")
        self.batches = _Array(self, "batches")
        self.attached = False
        try:
            self.flush()
        except BaseException:
            self.db.close()
            raise

    def attach(self, state):
        if self.attached:
            raise ValueError("HISTORY_ALREADY_ATTACHED")
        self.attached = True
        self.frames.extend(state["frame_items"])
        state["frame_items"] = self.frames
        account = state["account"]
        for name in ("history", "order_events", "decisions", "risk_events"):
            if getattr(account, name):
                raise ValueError("HISTORY_REQUIRES_PRISTINE_ACCOUNT")
            setattr(account, name, _Array(self, name))
        account.orders = _Map(self, "orders")
        account.ledger._events = _Array(self, "ledger_events")
        account.ledger._liquidations = _Array(self, "liquidations")
        account.ledger._capacity_consumed = _Map(self, "capacity_consumed")
        account.ledger._capacity_contracts = _Map(self, "capacity_contracts")

    def flush(self):
        before = self.path.stat(follow_symlinks=False)
        if (
            (before.st_dev, before.st_ino) != self._inode
            or self._disk_identity is not None
            and not self.db.in_transaction
            and _identity(before) != self._disk_identity
        ):
            raise ValueError("HISTORY_CHANGED")
        self.db.commit()
        size = self.db.execute("PRAGMA page_count").fetchone()[0] * self.db.execute("PRAGMA page_size").fetchone()[0]
        if size > self.maximum_bytes:
            raise ValueError("HISTORY_BYTES")
        self._disk_identity = _identity(self.path.stat(follow_symlinks=False))

    def begin_batch(self):
        self.flush()

    def record_batch(self, count):
        if type(count) is not int or not 1 <= count <= self.maximum_batch_frames:
            raise ValueError("FRAME_BATCH_BUDGET")
        self.batches.append(count)

    def _pin(self):
        fd = _open(self.path, os.O_RDONLY)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != self._inode:
                raise ValueError("HISTORY_CHANGED")
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o777 != 0o600
                or info.st_size > self.maximum_bytes
            ):
                raise ValueError("HISTORY_CUSTODY")
            digest = hashlib.sha256()
            with os.fdopen(fd, "rb", closefd=False) as handle:
                for part in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(part)
            if _identity(os.fstat(fd)) != _identity(info):
                raise ValueError("HISTORY_CHANGED")
            return _identity(info), digest.hexdigest(), self.db.total_changes
        finally:
            os.close(fd)

    @contextmanager
    def read_cut(self):
        self.flush()
        before = self._pin()
        yield
        if self._pin() != before:
            raise ValueError("HISTORY_CHANGED")

    def close(self):
        try:
            self.flush()
        finally:
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, error_type, *unused):
        if error_type is not None:
            self.db.close()
        else:
            self.close()


def write_result(session, directory, *, maximum_bytes, chunk_bytes=MAX_INPUT_BYTES):
    """Seal the full original result in bounded files; no native admission granted."""
    if type(maximum_bytes) is not int or not 0 < maximum_bytes <= MAX_BYTES:
        raise ValueError("RESULT_BYTE_BUDGET")
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= MAX_INPUT_BYTES:
        raise ValueError("RESULT_CHUNK_BYTES")
    if session._history_store is None:
        raise ValueError("RESULT_REQUIRES_HISTORY_STORE")
    root = Path(directory).absolute()
    if root.resolve() != root:
        raise ValueError("RESULT_PATH")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    files._directory(root)
    files._mkdir(root / "blobs")
    parts = []
    size = 0
    digest = hashlib.sha256()
    buffer = bytearray()
    reads = _ReadSet(maximum_directory_entries=MAX_PARTS)

    def retain(raw):
        if len(parts) >= MAX_PARTS:
            raise ValueError("RESULT_PARTS")
        name = hashlib.sha256(raw).hexdigest()
        files._put(root / "blobs" / name, None, raw=raw)
        if reads.read(root / "blobs" / name, len(raw)) != raw:
            raise ValueError("RESULT_CHANGED")
        parts.append(dict(sha256=name, bytes=len(raw)))

    try:
        with session._history_store.read_cut():
            result = session.report_stream()
            for raw in json_parts(result):
                size += len(raw)
                if size > maximum_bytes:
                    raise ValueError("RESULT_BYTES")
                digest.update(raw)
                offset = 0
                while offset < len(raw):
                    amount = min(chunk_bytes - len(buffer), len(raw) - offset)
                    buffer.extend(raw[offset : offset + amount])
                    offset += amount
                    if len(buffer) == chunk_bytes:
                        retain(bytes(buffer))
                        buffer.clear()
            if buffer:
                retain(bytes(buffer))
            if (
                exact_stream_identity_hash({k: v for k, v in result.items() if k != "result_hash"})
                != result["result_hash"]
            ):
                raise ValueError("RESULT_CHANGED")
            if set(reads.directory(root / "blobs")) != {part["sha256"] for part in parts}:
                raise ValueError("RESULT_MEMBERS")
            reads.recheck()
            input_archive = dict(
                file="history.sqlite3",
                sha256=session._history_store._pin()[1],
                frame_count=len(session._state["frame_items"]),
                batch_count=len(session._history_store.batches),
            )
        manifest = dict(
            schema="PERPETUAL_SIMULATION_CHUNKED_RESULT_V1",
            status="TECHNICAL_VALIDATION_ONLY",
            parts=parts,
            bytes=size,
            sha256=digest.hexdigest(),
            input_hash=result["input_hash"],
            result_hash=result["result_hash"],
            authoritative=False,
            economic_eligible=False,
            submission_enabled=False,
            native_registered=False,
            input_archive=input_archive,
        )
        canonical(manifest, maximum=1_048_576)
        result_contract.validate(manifest)
        files._put(root / "manifest.json", manifest)
        return manifest
    except BaseException as exc:
        files._put(
            root / "failure.json",
            dict(
                status="INCOMPLETE",
                error_type=type(exc).__name__,
                parts=parts,
                bytes=size,
                authoritative=False,
                submission_enabled=False,
            ),
        )
        raise


def frames_from_history(directory, *, database_sha256, frame_count, batch_frames=4096):
    """Read only retained frame rows; saved money/order state is never a restore input."""
    if (
        type(database_sha256) is not str
        or len(database_sha256) != 64
        or any(c not in "0123456789abcdef" for c in database_sha256)
        or type(frame_count) is not int
        or not 0 < frame_count <= 100_000_000
        or type(batch_frames) is not int
        or not 1 <= batch_frames <= 4096
    ):
        raise ValueError("HISTORY_SOURCE_BINDING")
    root = files._directory(Path(directory).absolute())
    reads = _ReadSet(maximum_directory_entries=1)
    if reads.directory(root) != ("history.sqlite3",):
        raise ValueError("HISTORY_SOURCE_MEMBERS")
    path = root / "history.sqlite3"
    fd = _open(path, os.O_RDONLY)
    connection = None

    def pin():
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_mode & 0o777 != 0o600
            or before.st_size > MAX_BYTES
        ):
            raise ValueError("HISTORY_SOURCE_CUSTODY")
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            raw = os.read(fd, 1024 * 1024)
            if not raw:
                break
            digest.update(raw)
        if _identity(os.fstat(fd)) != _identity(before):
            raise ValueError("HISTORY_SOURCE_CHANGED")
        return _identity(before), digest.hexdigest()

    try:
        before = pin()
        if before[1] != database_sha256:
            raise ValueError("HISTORY_SOURCE_CHANGED")
        # SQLite opens this already-checked descriptor, not a caller URI or alias.
        connection = sqlite3.connect(f"file:/proc/self/fd/{fd}?mode=ro&immutable=1", uri=True)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA cache_size=-2048")
        connection.execute("PRAGMA temp_store=FILE")
        if connection.execute("SELECT type FROM sqlite_master WHERE name='arrays'").fetchone() != ("table",):
            raise ValueError("HISTORY_SOURCE_TABLE")
        if connection.execute("SELECT count(*) FROM arrays WHERE namespace='frames'").fetchone()[0] != frame_count:
            raise ValueError("HISTORY_SOURCE_COUNT")
        count = 0
        frames = iter(
            connection.execute(
                "SELECT ordinal,length(value),substr(value,1,?) FROM arrays WHERE namespace='frames' ORDER BY ordinal",
                (MAX_INPUT_BYTES + 1,),
            )
        )
        batch_count = 0
        for ordinal, raw in connection.execute(
            "SELECT ordinal,substr(value,1,1024) FROM arrays WHERE namespace='batches' ORDER BY ordinal"
        ):
            size = _decode(decode(raw))
            if ordinal != batch_count or type(size) is not int or not 1 <= size <= batch_frames:
                raise ValueError("HISTORY_SOURCE_BATCH")
            batch_count += 1
            batch = []
            for _ in range(size):
                row = next(frames, None)
                if row is None:
                    raise ValueError("HISTORY_SOURCE_ROW")
                index, length, body = row
                if index != count or count >= frame_count or not 0 < length <= MAX_INPUT_BYTES:
                    raise ValueError("HISTORY_SOURCE_ROW")
                value = _decode(decode(body))
                if type(value) is not SimulationFrame:
                    raise ValueError("HISTORY_SOURCE_FRAME")
                batch.append(value)
                count += 1
            yield tuple(batch)
        if not batch_count or next(frames, None) is not None:
            raise ValueError("HISTORY_SOURCE_BATCH")
        if count != frame_count or pin() != before or _identity(path.stat(follow_symlinks=False)) != before[0]:
            raise ValueError("HISTORY_SOURCE_CHANGED")
        reads.recheck()
    finally:
        if connection is not None:
            connection.close()
        os.close(fd)
