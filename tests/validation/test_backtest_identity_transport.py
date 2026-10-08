"""Lossless JSON transport for the original hash inputs, without type guessing."""
from decimal import Decimal
from enum import Enum
import json
import math
from pathlib import Path
import subprocess
import sys
from statistics import median
import time

import pytest


@pytest.mark.parametrize('value', [None, False, True, 0, -0.0, math.nextafter(.1, 1),
    math.nextafter(0.0, 1), 10**1000, '"\\\x00\n中文😀',
    Decimal('1.2300'), b'\0\xff', 'text', (1, Decimal('2.00')),
    {('symbol', 1): {'risk': Decimal('.125')}, 'set': frozenset(('a', 'b'))}])
def test_round_trip_preserves_original_hash_and_scalar_types(value):
    from quantos.contracts.backtest_identity import (
        decode_identity_payload, encode_identity_payload, exact_identity_hash,
    )
    tree = encode_identity_payload(value)
    restored = decode_identity_payload(tree)
    assert encode_identity_payload(restored) == tree
    assert exact_identity_hash(restored) == exact_identity_hash(value)
    if type(value) in (type(None), bool, int, float, Decimal, bytes, str):
        assert type(restored) is type(value)


@pytest.mark.parametrize('tree', [
    ['bool', 0], ['int', '01'], ['int', '+1'], ['float64', '0.1'],
    ['float64', 'nan'], ['decimal', '+1'], ['decimal', 'NaN'], ['bytes', 'FF'],
    ['bytes', 'f'], ['none', None], ['sequence', {}], ['str', 1],
    ['mapping', [[['str', 'a'], ['int', '1']], [['str', 'a'], ['int', '2']]]],
    ['mapping', [[['bool', False], ['int', '1']], [['int', '0'], ['int', '2']]]],
    ['set', [['int', '1'], ['int', '1']]],
    ['enum', 'untrusted.module.Class', ['str', 'value']],
])
def test_noncanonical_or_ambiguous_transport_rejected(tree):
    from quantos.contracts.backtest_identity import decode_identity_payload
    with pytest.raises(ValueError):
        decode_identity_payload(tree)


def test_bounds_apply_before_recursive_decode_or_huge_numeric_parse():
    from quantos.contracts.backtest_identity import decode_identity_payload
    tree = ['none']
    for _ in range(65):
        tree = ['sequence', [tree]]
    for invalid in (tree, ['int', '1' * 5000], ['sequence', [['none']] * 100001]):
        with pytest.raises(ValueError):
            decode_identity_payload(invalid)


def test_enum_objects_are_not_dynamically_reconstructed():
    from quantos.contracts.backtest_identity import encode_identity_payload
    class Authority(Enum):
        PASS = object()
    with pytest.raises((ValueError, TypeError)):
        encode_identity_payload(Authority.PASS)


@pytest.mark.parametrize('operation', ['encode', 'decode', 'encode_decimal'])
def test_expanded_byte_budget_precedes_large_allocation(operation):
    root = Path(__file__).resolve().parents[2]
    program = '''
import resource, sys
resource.setrlimit(resource.RLIMIT_AS, (128*1024**2,128*1024**2))
sys.path.insert(0, sys.argv[1])
from quantos.contracts.backtest_identity import encode_identity_payload, decode_identity_payload
text = 'x' * (4*1024**2)
try:
    if sys.argv[2] == 'encode':
        encode_identity_payload([text]*100)
    elif sys.argv[2] == 'encode_decimal':
        from decimal import Decimal
        encode_identity_payload([Decimal('2' * (4*1024**2))]*100)
    else:
        decode_identity_payload(['sequence', [['str', text]]*100])
except ValueError:
    pass
else:
    raise AssertionError('oversized transport accepted')
'''
    result = subprocess.run([sys.executable, '-I', '-c', program, str(root), operation],
                            capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_primitive_identity_decoder_stays_within_json_cpu_budget():
    from quantos.contracts.backtest_identity import decode_identity_payload
    values=[str(i).zfill(64) for i in range(5000)]
    trees=[['str',value] for value in values]
    raw=json.dumps(trees,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    assert len(raw)==375001
    ratios=[]
    for _ in range(5):
        started=time.process_time_ns()
        assert json.loads(raw)==trees
        encoding=time.process_time_ns()-started
        started=time.process_time_ns()
        assert [decode_identity_payload(tree) for tree in trees]==values
        decoding=time.process_time_ns()-started
        ratios.append(decoding/encoding)
    assert median(ratios)<=15,f'primitive identity CPU ratios: {ratios}'


def test_primitive_identity_encoder_uses_the_original_json_cpu_budget():
    from quantos.contracts.backtest_identity import encode_identity_payload
    values = [str(i).zfill(64) for i in range(5000)]
    trees = [['str', value] for value in values]
    raw = json.dumps(trees, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    assert len(raw) == 375001
    ratios = []
    for _ in range(5):
        started = time.process_time_ns()
        assert json.loads(raw) == trees
        json_cpu = time.process_time_ns() - started
        started = time.process_time_ns()
        assert [encode_identity_payload(value) for value in values] == trees
        native_cpu = time.process_time_ns() - started
        ratios.append(native_cpu / json_cpu)
    assert median(ratios) <= 15, f'primitive encoding CPU ratios: {ratios}'


@pytest.mark.parametrize('value', ['x' * (16_000_000 - 10), '\x00' * 2_666_665],
                         ids=['ascii-wire-limit', 'escaped-wire-limit'])
def test_scalar_transport_wire_limit_includes_the_original_tag(value):
    from quantos.contracts.backtest_identity import decode_identity_payload, encode_identity_payload
    raw = json.dumps(['str', value], separators=(',', ':'), ensure_ascii=False).encode()
    assert len(raw) == 16_000_000
    assert encode_identity_payload(value) == ['str', value]
    assert decode_identity_payload(['str', value]) == value
    with pytest.raises(ValueError, match='byte bound'):
        encode_identity_payload(value + 'x')
    with pytest.raises(ValueError, match='byte bound'):
        decode_identity_payload(['str', value + 'x'])


@pytest.mark.parametrize('value', [
    float('nan'), float('inf'), float('-inf'), Decimal('NaN'),
    Decimal('sNaN'), Decimal('Infinity'), '\ud800', 10**4096,
    Decimal('9' * 4097), b'x' * 8_000_001,
], ids=['float-nan', 'float-inf', 'float-negative-inf', 'decimal-nan',
        'decimal-snan', 'decimal-inf', 'surrogate', 'large-int',
        'large-decimal', 'large-bytes'])
def test_scalar_encoder_retains_nonfinite_unicode_and_numeric_bounds(value):
    from quantos.contracts.backtest_identity import encode_identity_payload
    with pytest.raises(ValueError):
        encode_identity_payload(value)


@pytest.mark.parametrize('value', [10**4095, Decimal('9' * 4096)],
                         ids=['int-numeric-limit', 'decimal-numeric-limit'])
def test_scalar_numeric_limit_is_inclusive(value):
    from quantos.contracts.backtest_identity import decode_identity_payload, encode_identity_payload
    tree = encode_identity_payload(value)
    assert len(tree[1]) == 4096
    assert type(decode_identity_payload(tree)) is type(value)
    assert decode_identity_payload(tree) == value


@pytest.mark.parametrize('operation', ['encode', 'decode'])
def test_valid_wide_unicode_container_fits_original_memory_budget(operation):
    """A valid <16MB wire must not allocate a second wide Unicode JSON copy."""
    root = Path(__file__).resolve().parents[2]
    program = '''
import resource, sys
resource.setrlimit(resource.RLIMIT_AS, (128*1024**2,128*1024**2))
sys.path.insert(0, sys.argv[1])
from quantos.contracts.backtest_identity import encode_identity_payload, decode_identity_payload
text = '\\U0001f600' + 'x' * 15_000_000
tree = ['sequence', [['str', text]]]
# 25 container/tag bytes plus 4-byte emoji and 15M ASCII bytes.
assert 25 + 4 + 15_000_000 < 16_000_000
if sys.argv[2] == 'encode':
    assert encode_identity_payload([text]) == tree
else:
    assert decode_identity_payload(tree) == (text,)
'''
    result = subprocess.run([sys.executable, '-I', '-c', program, str(root), operation],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_container_wire_limit_still_counts_all_tags():
    from quantos.contracts.backtest_identity import decode_identity_payload, encode_identity_payload
    text = 'x' * (16_000_000 - 25)
    tree = ['sequence', [['str', text]]]
    assert len(json.dumps(tree, ensure_ascii=False, separators=(',', ':')).encode()) == 16_000_000
    assert encode_identity_payload([text]) == tree
    assert decode_identity_payload(tree) == (text,)
    with pytest.raises(ValueError, match='byte bound'):
        encode_identity_payload([text + 'x'])
    with pytest.raises(ValueError, match='byte bound'):
        decode_identity_payload(['sequence', [['str', text + 'x']]])


@pytest.mark.parametrize('tree', [
    ['set', [['str', 'b'], ['str', 'a']]],
    ['set', [['int', '1'], ['bool', True]]],
    ['mapping', [[['str', 'b'], ['int', '1']], [['str', 'a'], ['int', '2']]]],
    ['sequence', [['bytes', '00 01']]],
    ['sequence', [['float64', '-0x0.0p+00']]],
    ['sequence', [['str', '\ud800']]],
])
def test_nested_noncanonical_order_aliases_and_spelling_remain_rejected(tree):
    from quantos.contracts.backtest_identity import decode_identity_payload
    with pytest.raises(ValueError):
        decode_identity_payload(tree)
