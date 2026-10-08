"""The neutral parser preserves the original public trust-document contract."""
import importlib

import pytest


def _parsers():
    neutral = importlib.import_module("quantos.contracts.json_integrity")
    legacy = importlib.import_module("services.governance.json_integrity")
    return neutral, legacy


def test_neutral_and_legacy_entrypoints_share_original_objects():
    neutral, legacy = _parsers()
    from services.governance import artifact_admission
    from services.validation import research_evidence_artifact

    assert legacy.DuplicateJsonKeyError is neutral.DuplicateJsonKeyError
    assert artifact_admission.DuplicateJsonKeyError is neutral.DuplicateJsonKeyError
    assert legacy.load_json_without_duplicate_keys is neutral.load_json_without_duplicate_keys
    assert artifact_admission.load_json_without_duplicate_keys is neutral.load_json_without_duplicate_keys
    assert research_evidence_artifact.load_json_without_duplicate_keys is neutral.load_json_without_duplicate_keys


@pytest.mark.parametrize("raw", [
    '{"name":"é","nested":[true,false,null,1,1.25,-2e-3]}',
    '{"name":"é","nested":[true,false,null,1,1.25,-2e-3]}'.encode(),
])
def test_neutral_parser_keeps_finite_unicode_json(raw):
    neutral, legacy = _parsers()
    expected = {"name": "é", "nested": [True, False, None, 1, 1.25, -0.002]}
    assert neutral.load_json_without_duplicate_keys(raw) == expected
    assert legacy.load_json_without_duplicate_keys(raw) == expected


@pytest.mark.parametrize("raw,exception", [
    ('{"x":1,"x":2}', "DuplicateJsonKeyError"),
    ('{"nested":[{"x":1,"x":2}]}', "DuplicateJsonKeyError"),
    ('{"x":NaN}', "ValueError"),
    ('{"x":Infinity}', "ValueError"),
    ('{"x":-Infinity}', "ValueError"),
    ('{"x":1e9999}', "ValueError"),
    ('{"x":"\\ud800"}', "ValueError"),
    ('{"\\udfff":1}', "ValueError"),
    (b'{"x":"\xff"}', "UnicodeDecodeError"),
    (b'\xef\xbb\xbf{}', "ValueError"),
    ('{"x":', "ValueError"),
    (None, "TypeError"),
    ({}, "TypeError"),
    (bytearray(b'{}'), "TypeError"),
])
def test_neutral_and_legacy_reject_ambiguous_or_invalid_documents(raw, exception):
    neutral, legacy = _parsers()
    expected = {"DuplicateJsonKeyError": neutral.DuplicateJsonKeyError,
                "ValueError": ValueError, "UnicodeDecodeError": UnicodeDecodeError,
                "TypeError": TypeError}[exception]
    for parser in (neutral.load_json_without_duplicate_keys, legacy.load_json_without_duplicate_keys):
        with pytest.raises(expected):
            parser(raw)


def test_supply_chain_pins_neutral_implementation_and_legacy_entrypoint():
    from scripts.generate_supply_chain_evidence import _MANIFESTS

    assert {"quantos/contracts/json_integrity.py", "services/governance/json_integrity.py"} <= set(_MANIFESTS)
