"""Shared storage cannot turn any offline entrypoint into a production path."""

from pathlib import Path

import pytest

from tools import demo_retention_transaction as retention


@pytest.mark.parametrize(
    "operation",
    ["prepare", "prepare_reviewed", "apply", "rollback", "lookup", "lookup_many", "transaction", "placements"],
)
@pytest.mark.parametrize("protected_root", ["/var/lib/quantos-demo-executor", "/var/lib/quantos-demo-archives"])
def test_offline_entrypoints_reject_runtime_root_before_io(operation, protected_root, tmp_path, monkeypatch):
    root = Path(protected_root)
    archive = tmp_path / "archive"

    def unexpected(*args, **kwargs):
        raise AssertionError("private filesystem IO before boundary check")

    monkeypatch.setattr(retention, "read", unexpected)
    monkeypatch.setattr(retention, "directory", unexpected)
    with pytest.raises(ValueError, match="ISOLATED_ONLY"):
        if operation == "prepare":
            retention.prepare(root, archive, [])
        elif operation == "prepare_reviewed":
            retention.prepare_reviewed(
                root, archive, plan=tmp_path / "plan", review=tmp_path / "review", expected_review_sha256="a" * 64
            )
        elif operation == "lookup":
            retention.lookup(root, archive, "observation-views/" + "a" * 64 + ".json")
        elif operation == "lookup_many":
            retention.lookup_many(root, [], "observation-views/" + "a" * 64 + ".json")
        elif operation == "transaction":
            with retention.transaction(root, archive):
                pass
        elif operation == "placements":
            retention.placements(root, archive, [])
        else:
            getattr(retention, operation)(root, archive)
