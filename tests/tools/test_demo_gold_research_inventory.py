"""Actual registered producers through the original multipart transport.

Only release/role external inputs are isolated. Original TrialLedger, registry,
DB/WAL, report readers, indexes, bundles and archive operations execute.
"""

from copy import deepcopy
import hashlib
import importlib
import os
from pathlib import Path
import shutil

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from tests.tools.test_demo_gold_namespace_checkpoint import source_attachment as source_attachment
from tests.tools.test_demo_gold_namespace_checkpoint import private_files as private_files
from tools import demo_gold_checkpoint_transport as transport
from live.execution.demo_batch_risk import _digest


@pytest.fixture
def native_research(registered_case, source_attachment, tmp_path_factory, monkeypatch):
    f = registered_case
    identity = source_attachment["identity"]
    f["arguments"]["release_identity"] = identity
    assert f["store"]._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    f["store"]._conn.execute("PRAGMA wal_autocheckpoint=0")
    plans = [f["prepare"]("one"), f["prepare"]("two")]
    execute(f, "one")
    execute(f, "two")
    assert Path(str(f["root"] / "registry.db") + "-wal").stat().st_size > 0
    uid = os.getuid()
    registry = dict(
        schema="DEMO_AUTHORITY_REGISTRY_V1",
        deployment_sha256="d" * 64,
        executor_uid=uid + 1,
        issuer_uid=uid + 2,
        research_uids=[uid, uid + 3],
        keys=[],
    )
    public = dict(
        schema="GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1",
        source_commit=identity["commit"],
        registry=registry,
        account_ref_sha256="a" * 64,
        base_runtime=None,
        current_runtime=None,
        records={},
    )
    cut = dict(
        schema="GOLD_CHECKPOINT_CUT_REQUEST_V1",
        operation_sha256="e" * 64,
        source_commit=identity["commit"],
        registry_sha256=_digest(registry),
        public_bundle_sha256=_digest(public),
        roles=["executor", "issuer", "controller", "research"],
        writer_barrier_sha256="f" * 64,
        opening_at_ms=1,
        submission_enabled=False,
        economic_authority=False,
    )
    layout = dict(
        schema="GOLD_RESEARCH_CHECKPOINT_LAYOUT_V1",
        source_commit=identity["commit"],
        uid=uid,
        gid=os.getgid(),
        components=[dict(name="registered", kind="PROJECT", path="project", registry_path="registry.db")],
    )
    output = tmp_path_factory.mktemp("research-transport-output")
    output.chmod(0o700)
    monkeypatch.setattr(os, "getgroups", lambda: [uid])
    return dict(
        f=f, root=f["root"], plans=plans, public=public, cut=cut, layout=layout, source=source_attachment, output=output
    )


def capture(f):
    api = importlib.import_module("tools.demo_gold_research_inventory")
    return api.capture_inventory(
        root=f["root"],
        layout=f["layout"],
        public_bundle=f["public"],
        cut_request=f["cut"],
        source_attachment=f["source"],
        guard=lambda: None,
    )


def file_hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize("encrypted", [False, True])
def test_real_registered_population_wal_and_bundle_restore_without_originals(native_research, encrypted):
    f = native_research
    secret = "isolated-research-checkpoint-test-passphrase" if encrypted else None
    archive = f["output"] / "archive"
    with capture(f) as inventory:
        before = {row["relative"]: row["stored_sha256"] for row in inventory.iter_entries() if row["kind"] == "FILE"}
        expected = inventory.semantic_view()
        native = expected["research"]["components"]["registered"]["native"]
        assert native["attempt_count"] == 6
        assert set(native["runs"]) == {p["invocation_id"] for p in f["plans"]}
        assert inventory.manifest()["source_root"]["uid"] == f["layout"]["uid"]
        saved = transport.create_checkpoint(inventory, archive, passphrase=secret, capture_only=not encrypted)
    f["f"]["store"].close()
    shutil.rmtree(f["root"])
    restored = f["output"] / "restored"
    result = transport.restore_checkpoint(archive, restored, passphrase=secret, expected_sha256=saved["sha256"])
    assert result["namespace"] == "research" and result["state"] == expected
    assert file_hashes(restored / "state") == before
    assert result["source_uid"] == f["layout"]["uid"]
    assert result["production_resumable"] is result["complete_gold_set_verified"] is False
    assert expected["native_research_verified"] is True
    assert expected["configured_project_roster_verified"] is expected["writer_barrier_verified"] is False
    assert result["submission_enabled"] is result["economic_authority"] is False


@pytest.mark.parametrize("damage", ["bytes", "inode", "new_member"])
def test_actual_original_change_after_last_archive_sync_cannot_publish(native_research, monkeypatch, damage):
    f = native_research
    original = transport.sync
    changed = []

    def sync(path):
        result = original(path)
        if Path(path).name == "archive" and not changed:
            target = f["f"]["directories"]["one"] / "input.json"
            if damage == "bytes":
                target.write_bytes(target.read_bytes() + b" ")
            elif damage == "inode":
                replacement = target.with_name("replacement.tmp")
                replacement.write_bytes(target.read_bytes())
                os.replace(replacement, target)
            else:
                (target.parent / "later.json").write_bytes(b"{}\n")
            changed.append(True)
        return result

    monkeypatch.setattr(transport, "sync", sync)
    with pytest.raises(ValueError):
        with capture(f) as inventory:
            transport.create_checkpoint(inventory, f["output"] / "archive", passphrase=None, capture_only=True)
    assert changed and not (f["output"] / "archive").exists()


@pytest.mark.parametrize("damage", ["uid", "cut", "source", "layout"])
def test_actual_role_and_exact_cut_must_match_before_capture(native_research, damage):
    f = native_research
    if damage == "uid":
        f["layout"]["uid"] = f["layout"]["gid"] = f["layout"]["uid"] + 3
    elif damage == "cut":
        f["cut"]["public_bundle_sha256"] = "0" * 64
    elif damage == "source":
        f["layout"]["source_commit"] = "0" * 40
    else:
        f["layout"]["components"][0]["path"] = "../project"
    with pytest.raises(ValueError):
        with capture(f):
            pytest.fail("wrong actual role or cut was accepted")


def test_inventory_snapshot_and_plain_dictionary_cannot_be_live_capture(native_research):
    f = native_research
    with capture(f) as inventory:
        snapshot = inventory.manifest()
        snapshot["research_layout"]["uid"] += 3
        assert inventory.manifest()["research_layout"] == f["layout"]
        with pytest.raises(ValueError):
            transport.create_checkpoint(
                deepcopy(inventory.manifest()), f["output"] / "archive", passphrase=None, capture_only=True
            )


def test_shared_research_restore_uses_indexed_original_uid_not_first_registry_uid(native_research):
    f = native_research
    f["public"]["registry"]["research_uids"].reverse()
    # A registry's canonical order must remain valid; the explicit UID is the
    # larger second member here, so picking the first one would be incorrect.
    f["public"]["registry"]["research_uids"] = [f["layout"]["uid"] - 1, f["layout"]["uid"]]
    f["cut"]["registry_sha256"] = _digest(f["public"]["registry"])
    f["cut"]["public_bundle_sha256"] = _digest(f["public"])
    with capture(f) as inventory:
        saved = transport.create_checkpoint(inventory, f["output"] / "archive", passphrase=None, capture_only=True)
    result = transport.restore_checkpoint(
        f["output"] / "archive", f["output"] / "restored", passphrase=None, expected_sha256=saved["sha256"]
    )
    assert result["source_uid"] == f["layout"]["uid"]


def test_failed_and_not_started_original_runs_survive_archive_without_reconciliation(native_research, monkeypatch):
    from tools import gold_bar_experiment as gold

    f = native_research
    failed = f["f"]["prepare"]("failed")
    original = gold._execute

    def failure(arguments):
        changed = deepcopy(arguments)
        changed["bar_receipt"]["response_sha256"] = "0" * 64
        return original(changed)

    with monkeypatch.context() as patch:
        patch.setattr(gold, "_execute", failure)
        with pytest.raises(ValueError):
            execute(f["f"], "failed")
    unknown = f["f"]["prepare"]("not_started")
    with capture(f) as inventory:
        expected = inventory.semantic_view()
        value = expected["research"]["components"]["registered"]["native"]
        assert unknown["invocation_id"] in value["unfinished_runs"]
        assert value["all_runs_finalized"] is False
        assert any(row["status"] == "FAILED" for row in value["attempts"])
        assert failed["invocation_id"] in value["runs"]
        saved = transport.create_checkpoint(inventory, f["output"] / "archive", passphrase=None, capture_only=True)
    monkeypatch.setattr(gold, "_execute", lambda _: pytest.fail("restore must not run a producer"))
    result = transport.restore_checkpoint(
        f["output"] / "archive", f["output"] / "restored", passphrase=None, expected_sha256=saved["sha256"]
    )
    assert result["state"] == expected


def test_destination_cannot_reach_protected_root_through_parent_alias(tmp_path, monkeypatch):
    from tools import demo_namespace_checkpoint as checkpoint

    protected = tmp_path / "protected"
    ordinary = tmp_path / "ordinary"
    protected.mkdir(mode=0o700)
    ordinary.mkdir(mode=0o700)
    monkeypatch.setattr(checkpoint, "PROTECTED_ROOTS", (protected,))
    alias = ordinary / ".." / "protected" / "new"
    with pytest.raises(ValueError):
        transport._destination(alias)
    assert not (protected / "new").exists()


@pytest.mark.parametrize(
    "damage", ["large_leaf", "boolean_size", "negative_size", "file_count", "total_bytes", "hash", "path", "shape"]
)
def test_real_inventory_rejects_damaged_payload_budget_before_extraction(native_research, damage):
    from quantos.contracts.research_evidence_json import digest
    from tools import demo_gold_research_checkpoint as research
    from tools import demo_gold_research_inventory as api

    f = native_research
    with capture(f) as captured:
        inventory = captured.manifest()
    core = inventory["semantic_view"]["research"]
    name = next(iter(core["files"]))
    if damage == "large_leaf":
        core["files"][name]["size"] = research.MAX_FILE_BYTES + 1
        core["bytes"] = inventory["payload_bytes"] = sum(v["size"] for v in core["files"].values())
    elif damage == "boolean_size":
        core["files"][name]["size"] = True
    elif damage == "negative_size":
        core["files"][name]["size"] = -1
    elif damage == "file_count":
        inventory["files"] += 1
    elif damage == "total_bytes":
        core["bytes"] = inventory["payload_bytes"] = core["bytes"] + 1
    elif damage == "hash":
        core["files"][name]["sha256"] = "g" * 64
    elif damage == "path":
        core["files"]["../outside"] = core["files"].pop(name)
    else:
        core["files"][name]["undeclared"] = "extra"
    core["semantic_sha256"] = digest({k: v for k, v in core.items() if k != "semantic_sha256"})
    with pytest.raises(ValueError):
        api.inventory_limits(inventory, public_bundle=f["public"], source_attachment=f["source"])


def test_read_only_original_files_keep_original_mode_and_private_restore(native_research):
    import json

    f = native_research
    original = f["f"]["directories"]["one"] / "input.json"
    original.chmod(0o644)
    relative = original.relative_to(f["root"]).as_posix()
    with capture(f) as captured:
        row = next(r for r in captured.iter_entries() if r["relative"] == relative)
        assert row["source_identity"]["mode"] & 0o777 == 0o644
        saved = transport.create_checkpoint(captured, f["output"] / "archive", passphrase=None, capture_only=True)
    result = transport.restore_checkpoint(
        f["output"] / "archive", f["output"] / "restored", passphrase=None, expected_sha256=saved["sha256"]
    )
    assert result["production_resumable"] is False
    assert original.stat().st_mode & 0o777 == 0o644
    assert (f["output"] / "restored/state" / relative).stat().st_mode & 0o777 == 0o600
    manifest = json.loads((f["output"] / "archive/checkpoint-manifest.json").read_bytes())
    assert manifest["inventory"]["research_layout"] == f["layout"]


@pytest.mark.parametrize("damage", ["digest", "path", "uid"])
def test_original_index_must_match_verified_file_population_before_first_payload(native_research, damage):
    from tools import demo_gold_namespace_checkpoint as native

    f = native_research
    with capture(f) as captured:
        inventory = captured.manifest()
        entries = list(captured.iter_entries())
    row = next(row for row in entries if row["kind"] == "FILE")
    if damage == "digest":
        row["original_sha256"] = row["stored_sha256"] = "e" * 64
    elif damage == "path":
        row["relative"] = str(Path(row["relative"]).with_name("unrecorded-member.json"))
    else:
        row["source_identity"]["uid"] = row["source_identity"]["gid"] = f["layout"]["uid"] + 3
    entries.sort(key=lambda row: row["relative"])
    root = f["output"] / "index-probe"
    root.mkdir(mode=0o700)
    (root / "state").mkdir(mode=0o700)
    inventory["index"] = native._write_index(root / "index", entries)
    with pytest.raises(ValueError):
        list(transport._expected_files(root, dict(inventory=inventory)))


@pytest.mark.parametrize("damage", ["relative", "secret_parent"])
def test_role_root_is_canonical_and_never_reads_secret_parent(native_research, monkeypatch, damage):
    from tools import demo_gold_research_inventory as api

    f = native_research
    if damage == "relative":
        f["root"] = Path("relative-research-root")
    else:
        f["root"] = f["output"] / "keys" / "research-root"
        f["root"].mkdir(mode=0o700, parents=True)
    monkeypatch.setattr(
        api, "_node", lambda *a, **kw: pytest.fail("invalid root must be rejected before descriptor reads")
    )
    with pytest.raises(ValueError):
        with capture(f):
            pytest.fail("invalid root accepted")
