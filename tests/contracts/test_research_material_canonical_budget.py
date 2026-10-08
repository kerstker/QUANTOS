"""Wire, preallocation bounds and CPU cost of complete research JSON material."""
import json
from statistics import median
import time

import pytest

from quantos.contracts import research_evidence_json as api
from quantos.contracts import registered_research_run as registered
from quantos.contracts import backtest_identity as identity


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        allow_nan=False).encode()


@pytest.mark.parametrize('value', [chr(code) for code in range(32)] + [
    '"', '\\', '/', '\x7f', '中文', '😀', '\u2028', 'x"\\\x00\n中文😀'])
def test_all_json_escape_and_utf8_costs_keep_the_original_byte_boundary(monkeypatch, value):
    expected = wire(value)
    monkeypatch.setattr(api, 'MAX_BYTES', len(expected))
    assert api.canonical(value) == expected
    monkeypatch.setattr(api, 'MAX_BYTES', len(expected)-1)
    with pytest.raises(ValueError, match='BOUND'):
        api.canonical(value)


@pytest.mark.parametrize('value', [chr(code) for code in range(32)] + [
    '"', '\\', '/', '\x7f', '中文', '😀', '\u2028', 'x"\\\x00\n中文😀'])
def test_registered_and_identity_string_costs_keep_exact_original_wire(value):
    expected = wire(value)
    assert registered.canonical(value, maximum=len(expected)) == expected
    with pytest.raises(ValueError, match='BYTES'):
        registered.canonical(value, maximum=len(expected)-1)
    assert identity._json_string_size(value) == len(expected)


def test_escape_expansion_is_rejected_before_serialization(monkeypatch):
    monkeypatch.setattr(api, 'MAX_BYTES', 64)
    monkeypatch.setattr(api.json, 'dumps', lambda *a, **kw: pytest.fail('oversized material reached serialization'))
    with pytest.raises(ValueError, match='BOUND'):
        api.canonical('\x00'*20)


@pytest.mark.parametrize('kind', ['research', 'registered', 'identity'])
def test_complete_material_validation_stays_within_encoding_cpu_budget(kind):
    value = {'schema':'MATERIAL_WIRE_BENCHMARK', 'bindings':[
        {'name':'registry-field-'+str(i), 'sha256':str(i).zfill(64), 'kind':'original signed evidence'}
        for i in range(5000)]}
    if kind == 'identity':
        value = ['sequence', [['str', str(i).zfill(64)] for i in range(5000)]]
    expected = wire(value)
    assert len(expected) == (375014 if kind == 'identity' else 703939)
    checked = {'research':api.canonical, 'registered':registered.canonical,
        'identity':identity._transport_bytes}[kind]
    ratios = []
    for _ in range(5):
        started = time.process_time_ns()
        assert wire(value) == expected
        encoding = time.process_time_ns()-started
        started = time.process_time_ns()
        assert checked(value) == expected
        validation = time.process_time_ns()-started
        ratios.append(validation/encoding)
    assert median(ratios) <= 15, f'complete material validation CPU ratios: {ratios}'
