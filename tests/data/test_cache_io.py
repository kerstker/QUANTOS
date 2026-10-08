import os

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


def test_atomic_write_json_retries_transient_replace_permission(tmp_path, monkeypatch):
    import tools.cache_io as cache_io

    path = tmp_path / "state.json"
    attempts = []
    real_replace = os.replace

    def flaky_replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) < 3:
            raise PermissionError("transient destination lock")
        return real_replace(source, destination)

    monkeypatch.setattr(cache_io.os, "replace", flaky_replace)
    monkeypatch.setattr(cache_io.time, "sleep", lambda _delay: None)

    cache_io.atomic_write_json(str(path), {"ok": True})

    assert len(attempts) == 3
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
