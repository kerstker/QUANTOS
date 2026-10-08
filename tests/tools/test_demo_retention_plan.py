import hashlib
import json

import pytest


def module():
    from tools import demo_retention_plan

    return demo_retention_plan


def record(directory, at):
    data = json.dumps({"observed_at_ms": at, "private_fixture": "not exported"}).encode()
    digest = hashlib.sha256(data).hexdigest()
    path = directory / (digest + ".json")
    path.write_bytes(data)
    path.chmod(0o600)
    return digest


def fixture(tmp_path):
    directory = tmp_path / "views"
    directory.mkdir(mode=0o700)
    hashes = [record(directory, at) for at in (1, 2, 3, 9_900_000)]
    pointer = directory / "latest.json"
    pointer.write_text(json.dumps({"sha256": hashes[0]}))
    pointer.chmod(0o600)
    return directory, hashes


def test_frozen_prefix_keeps_latest_recent_and_newest_without_writes(tmp_path):
    m = module()
    directory, hashes = fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    plan = m.inspect_directory(directory, keep_count=1, now_ms=10_000_000)
    assert {r["sha256"] for r in plan["candidates"]} == {hashes[1], hashes[2]}
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert "private_fixture" not in json.dumps(plan)
    assert plan["reference_integrity_verified"] is False
    assert plan["apply_available"] is False


@pytest.mark.parametrize("damage", ["bytes", "symlink", "hardlink", "pointer", "future", "unknown"])
def test_unsafe_or_ambiguous_inventory_is_rejected(tmp_path, damage):
    m = module()
    directory, hashes = fixture(tmp_path)
    path = directory / (hashes[1] + ".json")
    if damage == "bytes":
        path.write_text("{}")
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(directory / (hashes[2] + ".json"))
    elif damage == "hardlink":
        (tmp_path / "outside").hardlink_to(path)
    elif damage == "pointer":
        (directory / "latest.json").write_text(json.dumps({"sha256": "0" * 64}))
    elif damage == "future":
        record(directory, 10_000_001)
    else:
        (directory / "unknown.json").write_text("{}")
    with pytest.raises(m.PlanError):
        m.inspect_directory(directory, keep_count=1, now_ms=10_000_000)


def test_failure_never_creates_archive_directory(tmp_path):
    m = module()
    with pytest.raises(m.PlanError):
        m.inspect_directory(tmp_path / "missing", keep_count=1, now_ms=10_000_000)
    assert list(tmp_path.iterdir()) == []
