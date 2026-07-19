import json

import pytest

from tools.cache_io import atomic_write_json


def test_atomic_write_json_replaces_complete_document(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"old": true}', encoding="utf-8")

    atomic_write_json(str(path), {"new": [1, 2]}, indent=2)

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": [1, 2]}


def test_atomic_write_json_preserves_original_on_serialization_failure(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"old": true}', encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_write_json(str(path), {"bad": object()})

    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".tmp_*")) == []
