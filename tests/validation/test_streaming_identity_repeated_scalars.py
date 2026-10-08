"""Repeated fields retain exact tagged identity and every streaming boundary."""
from decimal import Decimal
import json

import pytest

from quantos.contracts.backtest_identity import exact_identity_hash
from quantos.streaming_identity import StreamArray, StreamObject, exact_stream_identity_hash, json_parts, key_order


def test_repeated_scalar_tags_and_wire_match_original_exact_identity():
    values = [None, False, True, 0, 1, -1, 0.0, -0.0, 1.0, Decimal('1'),
              Decimal('1.0'), Decimal('-0'), '1', 'a"\\\n', '金', 'x' * 129, 2 ** 129]
    for value in values * 3:
        assert exact_stream_identity_hash(value) == exact_identity_hash(value)
    original = [{'name': '金', 'quantity': 1, 'eligible': False}] * 200
    streamed = StreamArray(lambda: iter(original))
    assert exact_stream_identity_hash(streamed) == exact_identity_hash(original)
    assert json.loads(b''.join(json_parts(streamed))) == original


def test_repeated_fields_still_apply_exact_byte_budget():
    original = [{'key': 'constant', 'value': 1}] * 20
    streamed = StreamArray(lambda: iter(original))
    wire = b''.join(json_parts(streamed))
    assert b''.join(json_parts(streamed, maximum_bytes=len(wire))) == wire
    with pytest.raises(ValueError, match='STREAM_BYTES'):
        b''.join(json_parts(streamed, maximum_bytes=len(wire) - 1))
    exact_stream_identity_hash(streamed)
    with pytest.raises(ValueError, match='STREAM_BYTES'):
        exact_stream_identity_hash(streamed, maximum_bytes=1)


def test_repeatable_original_view_is_reread_and_reordered_keys_rejected():
    original = {'a': 1, 'b': 2}
    items = sorted(original.items(), key=lambda entry: key_order(entry[0]))
    streamed = StreamObject(lambda: iter(items))
    before = exact_stream_identity_hash(streamed)
    items[1] = (items[1][0], 3)
    assert exact_stream_identity_hash(streamed) != before
    items.reverse()
    with pytest.raises(ValueError, match='STREAM_OBJECT_ORDER'):
        exact_stream_identity_hash(streamed)
