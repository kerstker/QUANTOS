"""Exact-byte tests for the v3 authority canonical JSON codec."""
from __future__ import annotations

import math
import sys

import pytest
import quantos.contracts.canonical as canonical_module

from quantos.contracts.canonical import (
    CanonicalJsonError,
    canonical_json_bytes,
    canonical_sha256,
    canonicalize_authority_value,
    float64_hex,
    load_canonical_json_bytes,
    parse_float64_hex,
    sha256_bytes,
)


def test_canonical_bytes_are_sorted_compact_utf8_and_newline_terminated() -> None:
    assert float64_hex(-0.0) == "0x0.0p+0"
    assert canonical_json_bytes({"z": "茅", "x": "0x1.0000000000000p+0"}) == (
        '{"x":"0x1.0000000000000p+0","z":"茅"}\n'.encode("utf-8")
    )
    assert canonical_sha256({"x": "0x1.0000000000000p+0"}) == sha256_bytes(
        b'{"x":"0x1.0000000000000p+0"}\n'
    )


def test_typed_floats_are_normalized_to_canonical_hex_recursively() -> None:
    value = canonicalize_authority_value({"nested": [-0.0, 1.5]})
    assert value == {"nested": ["0x0.0p+0", "0x1.8000000000000p+0"]}
    assert parse_float64_hex("0x0.0p+0") == 0.0
    assert float64_hex(parse_float64_hex("0x1.8000000000000p+0")) == "0x1.8000000000000p+0"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"x":1.0}\n',
        b'{"x":1}\r\n',
        b'{"x":1}',
        b'{"x":1}\n\n',
        b'\xef\xbb\xbf{"x":1}\n',
        b'{"x":1,"x":2}\n',
        b'{"x":"0X1.0000000000000p+0"}\n',
    ],
)
def test_load_fails_closed_for_noncanonical_json(payload: bytes) -> None:
    with pytest.raises(CanonicalJsonError):
        load_canonical_json_bytes(payload)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, "e\u0301"])
def test_canonicalization_rejects_nonfinite_or_non_nfc_values(value: object) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json_bytes({"value": value})


def test_canonicalization_rejects_non_nfc_keys_and_noncanonical_float_spellings() -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json_bytes({"e\u0301": "value"})
    with pytest.raises(CanonicalJsonError):
        parse_float64_hex("0x1.0p+0")


@pytest.mark.parametrize("raw_json", ['{"x":1}', '["x",1]'])
def test_canonicalization_rejects_top_level_preserialized_json(raw_json: str) -> None:
    with pytest.raises(CanonicalJsonError):
        canonicalize_authority_value(raw_json)


def test_canonicalization_keeps_an_ordinary_nfc_string() -> None:
    assert canonicalize_authority_value("茅") == "茅"


def test_canonicalization_fails_closed_when_json_parser_recurses(monkeypatch: pytest.MonkeyPatch) -> None:
    def _recurse(_: str) -> object:
        raise RecursionError("test parser exhaustion")

    monkeypatch.setattr(canonical_module.json, "loads", _recurse)
    with pytest.raises(CanonicalJsonError):
        canonicalize_authority_value('{"deep":true}')


def test_canonicalization_rejects_a_deep_valid_preserialized_json_array() -> None:
    depth = sys.getrecursionlimit() + 50
    raw_json = "[" * depth + "0" + "]" * depth
    with pytest.raises(CanonicalJsonError):
        canonicalize_authority_value(raw_json)


@pytest.mark.parametrize("payload", [b'{"x":NaN}\n', b'{"x":Infinity}\n', b'{"x":-Infinity}\n'])
def test_loader_rejects_raw_json_nonfinite_constants(payload: bytes) -> None:
    with pytest.raises(CanonicalJsonError):
        load_canonical_json_bytes(payload)
