"""Repeatable lazy views and the unchanged exact identity wire, emitted in pieces."""

from collections.abc import Mapping, Sequence
import hashlib
import json

from quantos.contracts.backtest_identity import _canonical_identity

MAX_BYTES = 32 * 1024**3


class StreamArray:
    def __init__(self, items):
        if not callable(items):
            raise TypeError("STREAM_ITEMS_FACTORY")
        self.items = items


class StreamObject:
    """String keys supplied in exact tagged-key identity order, without sorting RAM."""

    def __init__(self, items):
        if not callable(items):
            raise TypeError("STREAM_ITEMS_FACTORY")
        self.items = items


def key_order(key):
    return json.dumps(_canonical_identity(key, name="key"), ensure_ascii=False, separators=(",", ":"))


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _items(value):
    if isinstance(value, StreamObject):
        previous = None
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("STREAM_OBJECT_KEY")
            order = key_order(key)
            if previous is not None and order <= previous:
                raise ValueError("STREAM_OBJECT_ORDER")
            previous = order
            yield key, item
    else:
        yield from sorted(value.items(), key=lambda entry: key_order(entry[0]))


def _identity(value, depth=0):
    if depth > 256:
        raise ValueError("STREAM_DEPTH")
    if isinstance(value, (Mapping, StreamObject)):
        yield b'["mapping",['
        for index, (key, item) in enumerate(_items(value)):
            if index:
                yield b","
            yield b"["
            yield from _identity(key, depth + 1)
            yield b","
            yield from _identity(item, depth + 1)
            yield b"]"
        yield b"]]"
    elif isinstance(value, StreamArray) or isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        yield b'["sequence",['
        for index, item in enumerate(value.items() if isinstance(value, StreamArray) else value):
            if index:
                yield b","
            yield from _identity(item, depth + 1)
        yield b"]]"
    else:
        yield _json(_canonical_identity(value, name="streamed_identity"))


def _transport(value, depth=0):
    if depth > 256:
        raise ValueError("STREAM_DEPTH")
    if isinstance(value, (Mapping, StreamObject)):
        yield b"{"
        for index, (key, item) in enumerate(_items(value)):
            if type(key) is not str:
                raise ValueError("STREAM_JSON_KEY")
            if index:
                yield b","
            yield _json(key)
            yield b":"
            yield from _transport(item, depth + 1)
        yield b"}"
    elif isinstance(value, StreamArray) or isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        yield b"["
        for index, item in enumerate(value.items() if isinstance(value, StreamArray) else value):
            if index:
                yield b","
            yield from _transport(item, depth + 1)
        yield b"]"
    elif value is None or type(value) in (str, int, bool, float):
        yield _json(value)
    else:
        raise TypeError("STREAM_JSON_SCALAR")


def _bounded(parts, maximum):
    if type(maximum) is not int or not 0 < maximum <= MAX_BYTES:
        raise ValueError("STREAM_BYTE_BUDGET")
    size = 0
    for part in parts:
        size += len(part)
        if size > maximum:
            raise ValueError("STREAM_BYTES")
        yield part


def exact_stream_identity_hash(value, *, name="authority_payload", maximum_bytes=MAX_BYTES):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("identity hash name must be non-empty")
    digest = hashlib.sha256()
    for part in _bounded(_identity(value), maximum_bytes):
        digest.update(part)
    return digest.hexdigest()


def json_parts(value, *, maximum_bytes=MAX_BYTES):
    yield from _bounded(_transport(value), maximum_bytes)
