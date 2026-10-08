import hashlib
import json
import os
import stat
import pytest
from tools.research_runtime_permissions import repair


def fixture(tmp_path):
    root = tmp_path / "venv"
    root.mkdir(mode=0o700)
    binding = {"artifact_commit": "a" * 40}
    (root / "quantos-maintenance-install.json").write_text(
        json.dumps({"binding_sha256": hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()})
    )
    (root / "quantos-maintenance-install.json").chmod(0o644)
    lib = root / "lib"
    lib.mkdir(mode=0o700)
    (lib / "package.py").write_text("value = 1")
    (lib / "package.py").chmod(0o600)
    (root / "lib64").symlink_to("lib")
    private = root / "quantos-maintenance"
    private.mkdir(mode=0o700)
    return root, binding


def test_private_runtime_becomes_readable_without_opening_saved_staging(tmp_path, monkeypatch):
    root, binding = fixture(tmp_path)
    # Test filesystem ownership while keeping real traversal and chmod behavior.
    monkeypatch.setattr("tools.research_runtime_permissions.protected_parents", lambda root, owner: None)
    result = repair(root, binding, owner=os.getuid())
    assert result["changed_paths"] == 3
    assert stat.S_IMODE(root.stat().st_mode) == 0o755
    assert stat.S_IMODE((root / "lib/package.py").stat().st_mode) == 0o644
    assert stat.S_IMODE((root / "quantos-maintenance").stat().st_mode) == 0o700
    assert (root / "lib64").is_symlink()
    assert repair(root, binding, owner=os.getuid())["changed_paths"] == 0


def test_external_symlink_rejected_before_any_permission_change(tmp_path, monkeypatch):
    root, binding = fixture(tmp_path)
    monkeypatch.setattr("tools.research_runtime_permissions.protected_parents", lambda root, owner: None)
    outside = tmp_path / "outside"
    outside.write_text("unchanged")
    (root / "lib/link").symlink_to(outside)
    with pytest.raises(ValueError, match="RUNTIME_LINK"):
        repair(root, binding, owner=os.getuid())
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_receipt_mismatch_does_not_mutate(tmp_path, monkeypatch):
    root, binding = fixture(tmp_path)
    monkeypatch.setattr("tools.research_runtime_permissions.protected_parents", lambda root, owner: None)
    with pytest.raises(ValueError, match="RUNTIME_RECEIPT_BINDING"):
        repair(root, {}, owner=os.getuid())
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_release_metadata_repair_preserves_source_and_index_bytes(tmp_path, monkeypatch):
    from tools.research_runtime_permissions import repair_release_metadata

    monkeypatch.setattr("tools.research_runtime_permissions.protected_parents", lambda root, owner: None)
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    (root / ".git").mkdir(mode=0o755)
    index = root / ".git/index"
    index.write_bytes(b"original-index")
    index.chmod(0o600)
    cache = root / "__pycache__"
    cache.mkdir(mode=0o700)
    bytecode = cache / "module.cpython-312.pyc"
    bytecode.write_bytes(b"original-cache")
    bytecode.chmod(0o600)
    source = root / "module.py"
    source.write_text("unchanged")
    source.chmod(0o644)
    result = repair_release_metadata(root, owner=os.getuid())
    assert result["changed_paths"] == 3
    assert index.read_bytes() == b"original-index"
    assert bytecode.read_bytes() == b"original-cache"
    assert source.read_text() == "unchanged"
    assert stat.S_IMODE(index.stat().st_mode) == 0o644
    assert repair_release_metadata(root, owner=os.getuid())["changed_paths"] == 0
