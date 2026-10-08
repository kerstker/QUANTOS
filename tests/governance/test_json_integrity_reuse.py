"""Repeated strict parsing keeps errors and independent caller/thread results."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

import pytest

from quantos.contracts.json_integrity import (
    DuplicateJsonKeyError,
    load_json_without_duplicate_keys as decode,
)


@pytest.mark.parametrize("invalid,error", [
    ('{"nested":[{"key":1,"key":2}]}', DuplicateJsonKeyError),
    ('{"value":NaN}', ValueError),
    ('{"value":1e9999}', ValueError),
    ('{"value":"\\ud800"}', ValueError),
    ('{"value":[1,]}', json.JSONDecodeError),
    ('\ufeff{}', json.JSONDecodeError),
    (b'{"value":"\xff"}', UnicodeDecodeError),
])
def test_failure_does_not_poison_next_document(invalid, error):
    for index in range(20):
        with pytest.raises(error):
            decode(invalid)
        expected = {"key": index, "nested": [{"中文": "🙂"}], "value": 1.25}
        assert decode(json.dumps(expected, ensure_ascii=False)) == expected


def test_each_parse_returns_fresh_nested_objects_and_observes_new_bytes():
    raw = b'{"nested":[{"key":1}]}'
    first = decode(raw)
    first["nested"][0]["key"] = 99
    first["nested"].append("changed")
    assert decode(raw) == {"nested": [{"key": 1}]}
    assert decode(raw.replace(b":1", b":2")) == {"nested": [{"key": 2}]}


def test_concurrent_readers_keep_subjects_and_rejections_isolated():
    workers = 8
    barrier = Barrier(workers)

    def read(worker):
        barrier.wait(timeout=10)
        for index in range(100):
            expected = {"subject": worker, "iteration": index, "nested": [{"é": "🙂"}]}
            assert decode(json.dumps(expected).encode()) == expected
            with pytest.raises(DuplicateJsonKeyError):
                decode('{"subject":0,"subject":1}')
            with pytest.raises(ValueError, match="non-finite JSON number"):
                decode('[1e9999]')
            assert decode(json.dumps(expected)) == expected
        return worker

    with ThreadPoolExecutor(max_workers=workers) as pool:
        assert sorted(pool.map(read, range(workers))) == list(range(workers))


@pytest.mark.parametrize("raw", ['\ufeff{}', b'\xef\xbb\xbf{}'])
def test_bom_keeps_original_json_error_location(raw):
    with pytest.raises(json.JSONDecodeError) as caught:
        decode(raw)
    error = caught.value
    assert error.msg == "Unexpected UTF-8 BOM (decode using utf-8-sig)"
    assert error.doc == '\ufeff{}'
    assert (error.pos, error.lineno, error.colno) == (0, 1, 1)
