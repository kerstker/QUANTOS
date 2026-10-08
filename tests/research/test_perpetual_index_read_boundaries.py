"""Joined lazy reads retain missing-child and closed-view rejection boundaries."""
import pytest

from quantos.contracts.backtest_identity import exact_identity_hash
from quantos.streaming_identity import StreamArray, exact_stream_identity_hash
from services.research.perpetual_chunked_result_store import _Index


@pytest.mark.parametrize('kind', ['array', 'object'])
def test_missing_index_child_cannot_disappear_from_joined_reads(tmp_path, kind):
    index = _Index(tmp_path / 'index', 2_000_000, 20)
    try:
        root = index.add(kind)
        index.edge(root, 0, 'field' if kind == 'object' else None, 999)
        index.finish(root, 1)
        index.readonly()
        view = index.value(root)
        with pytest.raises(ValueError, match='INDEX_ROW'):
            view['field' if kind == 'object' else 0]
        with pytest.raises(ValueError, match='INDEX_ROW'):
            exact_stream_identity_hash(view)
    finally:
        index.close()


def test_cached_scalar_does_not_make_a_closed_original_view_readable(tmp_path):
    index = _Index(tmp_path / 'index', 2_000_000, 20)
    root = index.add('object')
    child = index.add('scalar', True)
    index.edge(root, 0, 'field', child)
    index.finish(root, 1)
    index.readonly()
    view = index.value(root)
    try:
        assert view['field'] is True
        assert view['field'] is True
        with pytest.raises(ValueError, match='STREAM_BYTES'):
            exact_stream_identity_hash(view, maximum_bytes=1)
        with pytest.raises(KeyError):
            view['absent']
    finally:
        index.close()
    with pytest.raises(ValueError, match='INDEX_CLOSED'):
        view['field']
    with pytest.raises(ValueError, match='INDEX_CLOSED'):
        exact_stream_identity_hash(view)


def test_joined_array_retains_original_scalar_type_tags(tmp_path):
    expected = [None, False, True, 0, 1, 0.0, -0.0, 1.0, '1', '金']
    index = _Index(tmp_path / 'index', 2_000_000, 20)
    try:
        root = index.add('array')
        for ordinal, value in enumerate(expected):
            child = index.add('scalar', value)
            index.edge(root, ordinal, None, child)
        index.finish(root, len(expected))
        index.readonly()
        view = index.value(root)
        assert exact_stream_identity_hash(view) == exact_identity_hash(expected)
        assert exact_stream_identity_hash(view) == exact_identity_hash(expected)
    finally:
        index.close()


def test_joined_identity_keeps_original_recursion_limit(tmp_path):
    index = _Index(tmp_path / 'index', 2_000_000, 20)
    try:
        root = index.add('object')
        child = index.add('scalar', True)
        index.edge(root, 0, 'field', child)
        index.finish(root, 1)
        index.readonly()
        value = index.value(root)
        for _ in range(255):
            inner = value
            value = StreamArray(lambda inner=inner: iter((inner,)))
        exact_stream_identity_hash(value)
        too_deep = StreamArray(lambda: iter((value,)))
        with pytest.raises(ValueError, match='STREAM_DEPTH'):
            exact_stream_identity_hash(too_deep)
    finally:
        index.close()
