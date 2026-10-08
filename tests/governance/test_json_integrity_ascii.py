"""ASCII fast-path coverage preserves strict JSON key/value and Unicode rules."""
import builtins
import json

import pytest

from quantos.contracts import json_integrity as parser
from services.governance import json_integrity as legacy


def test_ascii_keys_values_and_nested_records_do_not_scan_each_character(monkeypatch):
    calls = []

    def ordinal(character):
        calls.append(character)
        return builtins.ord(character)

    monkeypatch.setattr(parser, 'ord', ordinal, raising=False)
    value = {'source_commit': 'a' * 40, 'nested': [{'hash': 'b' * 64, 'items': ['ASCII', '', '0.001']}],
             'flags': [False, True, None], 'number': 1.25}
    assert parser.load_json_without_duplicate_keys(json.dumps(value).encode()) == value
    assert calls == [], 'Validated ASCII cannot contain a surrogate code point.'
    assert legacy.load_json_without_duplicate_keys is parser.load_json_without_duplicate_keys
    assert legacy.DuplicateJsonKeyError is parser.DuplicateJsonKeyError


@pytest.mark.parametrize('value', [
    {'中文': 'é'}, {'nested': ['😀', {'α': '\u0000\n'}]},
    {'safe': '\ud7ff\ue000\uffff\U0010ffff'}, ['ASCII', '', 0, -1, 1.25, True, False, None],
])
@pytest.mark.parametrize('as_bytes', [False, True])
def test_ascii_shortcut_keeps_valid_non_ascii_and_controls(value, as_bytes):
    raw = json.dumps(value, ensure_ascii=False)
    assert parser.load_json_without_duplicate_keys(raw.encode() if as_bytes else raw) == value


@pytest.mark.parametrize('raw,exception', [
    ('{"x":"\\ud800"}', ValueError), ('{"\\udfff":1}', ValueError),
    ('{"nested":[{"value":"prefix\\ud800suffix"}]}', ValueError),
    ('{"nested":{"key\\udfff":[]}}', ValueError),
    ('{"x":"\ud800"}', ValueError), ('{"\udfff":1}', ValueError),
    ('{"x":1,"x":2}', parser.DuplicateJsonKeyError),
    ('{"nested":[{"é":1,"é":2}]}', parser.DuplicateJsonKeyError),
    ('{"x":1e9999}', ValueError), ('{"x":-1e9999}', ValueError),
    ('{"x":NaN}', ValueError), ('{"x":Infinity}', ValueError),
    ('{"x":-Infinity}', ValueError), (b'{"x":"\xff"}', UnicodeDecodeError),
    (b'\xef\xbb\xbf{}', ValueError), ('{"x":', ValueError),
    (None, TypeError), (bytearray(b'{}'), TypeError),
])
def test_fast_path_never_accepts_ambiguous_non_finite_or_surrogate_json(raw, exception):
    with pytest.raises(exception):
        parser.load_json_without_duplicate_keys(raw)
