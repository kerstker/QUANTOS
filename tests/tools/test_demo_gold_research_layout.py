"""Native fixed public records and real roster descriptor reads, virtual OS owners.

All files are isolated. Only root/role ownership and accepted release are
virtualized, as in the existing fixed-public fixture. No writer barrier or
whole-role recovery is inferred from this component check.
"""

from copy import deepcopy
import importlib
import os
from pathlib import Path
import shutil

import pytest

from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public, public_cut
from tests.tools.test_demo_market_partition_install import book as book, env as env
from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from tools.gold_pilot_retention_store import canonical

_ORIGINAL_OWNER = os.geteuid(), os.getegid()


def module():
    return importlib.import_module("tools.demo_gold_research_layout")


@pytest.fixture
def configured(fixed_public, tmp_path, monkeypatch, request):
    f = fixed_public
    api = module()
    if getattr(request, "param", None) == "multiple":
        f.registry["research_uids"].append(9999)
        f.policy["registry_sha256"] = f.module._digest(f.registry)
        for name, value in (("executor_registry", f.registry), ("issuer_registry", f.registry), ("policy", f.policy)):
            f.paths[name].write_bytes(canonical(value))
    config = tmp_path / "fixed-layout" / "layout.json"
    config.parent.mkdir(mode=0o700)
    roots = []
    owners = {}
    for uid in sorted(f.registry["research_uids"]):
        root = tmp_path / ("research-" + str(uid))
        root.mkdir(mode=0o700)
        (root / "projects").mkdir(mode=0o700)
        (root / "projects" / "one").mkdir(mode=0o700)
        (root / "projects" / "one" / "original.json").write_bytes(b'{"original":true}\n')
        items = [dict(name="one", kind="PROJECT", path="projects/one", registry_path="registry.db")]
        for name, kind in (
            ("jobs", "RUN_REGISTRY"),
            ("audit", "AUDIT"),
            ("factory", "FACTORY"),
            ("gold", "GOLD_RESEARCH"),
            ("feedback", "FEEDBACK"),
        ):
            path = name + ".db" if kind in ("RUN_REGISTRY", "AUDIT") else name
            if path.endswith(".db"):
                (root / path).write_bytes(b"original database boundary")
                (root / path).chmod(0o600)
            else:
                (root / path).mkdir(mode=0o700)
            items.append(dict(name=name, kind=kind, path=path, registry_path=None))
        (root / "registry.db").write_bytes(b"original shared registry boundary")
        (root / "registry.db").chmod(0o600)
        for path in (root, *root.rglob("*")):
            path.chmod(0o700 if path.is_dir() else 0o600)
            owners[(path.stat().st_dev, path.stat().st_ino)] = (uid, uid)
        roots.append(
            dict(
                name="research_" + str(uid),
                root=str(root),
                layout=dict(
                    schema="GOLD_RESEARCH_CHECKPOINT_LAYOUT_V1",
                    source_commit=f.args["source_commit"],
                    uid=uid,
                    gid=uid,
                    components=items,
                ),
                project_collections=["projects"],
            )
        )
    roots.sort(key=lambda row: row["name"])
    value = dict(
        schema="GOLD_RESEARCH_INSTALLED_LAYOUT_V1",
        source_commit=f.args["source_commit"],
        account_ref_sha256=f.args["account_ref_sha256"],
        registry_sha256=f.module._digest(f.registry),
        research_roots=roots,
        submission_enabled=False,
        economic_authority=False,
    )
    config.write_bytes(canonical(value))
    config.chmod(0o644)
    monkeypatch.setattr(api, "CONFIG", config)
    original = api._identity
    ancestors = {(path.stat().st_dev, path.stat().st_ino) for path in tmp_path.parents}

    def identity(info):
        value = original(info)
        key = info.st_dev, info.st_ino
        owner = owners.get(key, (0, 0))
        value.update(st_uid=owner[0], st_gid=owner[1])
        if key in ancestors:
            value["st_mode"] &= ~0o022
        return value

    monkeypatch.setattr(api, "_identity", identity)
    snapshot = f.module.capture(**f.args)
    public, cut = public_cut(f, snapshot)
    handle = f.module.bind_captured(snapshot, public_bundle=public, cut_request=cut, guard=f.args["guard"])
    return dict(
        api=api, config=config, value=value, roots=roots, public=public, cut=cut, handle=handle, f=f, owners=owners
    )


def capture(f):
    return f["api"].capture(
        verified_public=f["handle"], public_bundle=f["public"], cut_request=f["cut"], guard=f["f"].args["guard"]
    )


def test_installed_scope_does_not_pin_the_pre_shutdown_registry(configured, monkeypatch):
    monkeypatch.setattr(os, 'getresuid', lambda: (0, 0, 0))
    monkeypatch.setattr(os, 'getresgid', lambda: (0, 0, 0))
    f = configured
    old = capture(f)
    registry = Path(f['roots'][0]['root']) / 'registry.db'
    registry.write_bytes(registry.read_bytes())
    with pytest.raises(ValueError, match='ORIGINAL_CHANGED'):
        old.recheck()
    scope = f['api'].installed_scope(old, verified_public=f['handle'])
    scope.recheck()
    direct = f['api'].capture_installed_scope(verified_public=f['handle'],
        public_bundle=f['public'], cut_request=f['cut'], guard=f['f'].args['guard'])
    assert direct.snapshot() == scope.snapshot()
    assert 'observed' not in direct.snapshot()
    body = f['config'].read_bytes()
    f['config'].write_bytes(body)
    with pytest.raises(ValueError, match='CONFIGURATION_CHANGED'):
        scope.recheck()


def test_installed_scope_still_rejects_caller_documents_and_replaced_original_root(configured, tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'getresuid', lambda: (0, 0, 0))
    monkeypatch.setattr(os, 'getresgid', lambda: (0, 0, 0))
    f = configured
    scope = f['api'].capture_installed_scope(verified_public=f['handle'],
        public_bundle=f['public'], cut_request=f['cut'], guard=f['f'].args['guard'])
    with pytest.raises(ValueError, match='INSTALLED_SCOPE_REQUIRED'):
        f['api'].require_scope(scope.snapshot(), verified_public=f['handle'])
    root = Path(f['roots'][0]['root'])
    root.rename(tmp_path / 'old-original-root')
    root.mkdir(mode=0o700)
    f['owners'][(root.stat().st_dev, root.stat().st_ino)] = (f['roots'][0]['layout']['uid'],) * 2
    with pytest.raises(ValueError, match='ROOT_CHANGED'):
        scope.recheck()


def test_installed_scope_cannot_be_declared_with_nonroot_identity(configured, monkeypatch):
    f = configured
    uid = f['roots'][0]['layout']['uid']
    monkeypatch.setattr(os, 'getresuid', lambda: (uid, uid, uid))
    monkeypatch.setattr(os, 'getresgid', lambda: (uid, uid, uid))
    with pytest.raises(ValueError, match='ROOT_GUARD_REQUIRED'):
        f['api'].capture_installed_scope(verified_public=f['handle'],
            public_bundle=f['public'], cut_request=f['cut'], guard=f['f'].args['guard'])


def test_fixed_original_roster_is_complete_and_read_only(configured):
    f = configured
    before = f["config"].read_bytes()
    handle = capture(f)
    snapshot = handle.snapshot()
    assert snapshot["schema"] == "GOLD_RESEARCH_FIXED_LAYOUT_V1"
    assert snapshot["operation_sha256"] == f["cut"]["operation_sha256"]
    assert snapshot["configured_project_roster_verified"] is True
    assert snapshot["writer_barrier_verified"] is snapshot["whole_set_verified"] is False
    assert snapshot["submission_enabled"] is snapshot["economic_authority"] is False
    assert f["config"].read_bytes() == before
    for row in f["roots"]:
        uid = row["layout"]["uid"]
        assert handle.layout_for_uid(uid) == row["layout"]
        assert handle.root_for_uid(uid) == Path(row["root"])
    snapshot["configuration"]["body"] = "caller replacement"
    assert handle.snapshot()["configuration"]["body"] != "caller replacement"


@pytest.mark.parametrize(
    "damage",
    [
        "new_project",
        "deleted_project",
        "project_link",
        "wrong_root_owner",
        "same_bytes_inode",
        "changed_config",
        "config_mode",
        "removed_registry",
    ],
)
def test_original_roster_mutation_or_unsafe_custody_rejects(configured, damage):
    f = configured
    handle = capture(f)
    root = Path(f["roots"][0]["root"])
    if damage == "new_project":
        (root / "projects" / "unlisted").mkdir(mode=0o700)
    elif damage == "deleted_project":
        (root / "projects" / "one" / "original.json").unlink()
        (root / "projects" / "one").rmdir()
    elif damage == "project_link":
        (root / "projects" / "one" / "original.json").unlink()
        (root / "projects" / "one").rmdir()
        (root / "projects" / "one").symlink_to(root / "factory", target_is_directory=True)
    elif damage == "wrong_root_owner":
        f["owners"][(root.stat().st_dev, root.stat().st_ino)] = (9999, 9999)
    elif damage == "same_bytes_inode":
        path = f["config"]
        body = path.read_bytes()
        replacement = path.with_name("replacement.json")
        replacement.write_bytes(body)
        replacement.chmod(0o644)
        os.replace(replacement, path)
    elif damage == "changed_config":
        value = deepcopy(f["value"])
        value["research_roots"][0]["name"] = "replacement"
        f["config"].write_bytes(canonical(value))
    elif damage == "config_mode":
        f["config"].chmod(0o664)
    else:
        (root / "registry.db").unlink()
    with pytest.raises((ValueError, OSError)):
        handle.recheck()


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_source",
        "wrong_account",
        "wrong_registry",
        "missing_uid",
        "extra_uid",
        "root_overlap",
        "project_scope",
        "secret_path",
        "unknown_field",
    ],
)
def test_installed_document_cannot_relabel_source_identity_or_managed_population(configured, damage):
    f = configured
    value = deepcopy(f["value"])
    if damage.startswith("wrong_"):
        field = {
            "wrong_source": "source_commit",
            "wrong_account": "account_ref_sha256",
            "wrong_registry": "registry_sha256",
        }[damage]
        value[field] = "a" * len(value[field])
    elif damage == "missing_uid":
        value["research_roots"] = []
    elif damage == "extra_uid":
        value["research_roots"][0]["layout"]["uid"] = 9999
        value["research_roots"][0]["layout"]["gid"] = 9999
    elif damage == "root_overlap":
        row = deepcopy(value["research_roots"][0])
        row["name"] = "overlap"
        value["research_roots"].append(row)
    elif damage == "project_scope":
        value["research_roots"][0]["project_collections"] = []
    elif damage == "secret_path":
        value["research_roots"][0]["layout"]["components"][0]["path"] = "key/project"
    else:
        value["caller_verified"] = True
    f["config"].write_bytes(canonical(value))
    with pytest.raises((ValueError, OSError)):
        capture(f)


def test_raw_public_or_unknown_research_uid_never_becomes_capability(configured):
    f = configured
    with pytest.raises(ValueError):
        f["api"].capture(
            verified_public=f["public"], public_bundle=f["public"], cut_request=f["cut"], guard=f["f"].args["guard"]
        )
    handle = capture(f)
    with pytest.raises(ValueError):
        handle.layout_for_uid(9999)
    with pytest.raises(ValueError):
        handle.layout_for_uid(True)
    with pytest.raises(ValueError):
        f["api"].require_captured(handle.snapshot(), verified_public=f["handle"])


def test_changed_cut_cannot_reuse_verified_public(configured):
    f = configured
    cut = deepcopy(f["cut"])
    cut["operation_sha256"] = "e" * 64
    with pytest.raises(ValueError):
        f["api"].capture(
            verified_public=f["handle"], public_bundle=f["public"], cut_request=cut, guard=f["f"].args["guard"]
        )


def test_new_project_during_capture_rejects_before_returning_handle(configured):
    f = configured
    calls = []

    def guard():
        calls.append(None)
        if len(calls) == 2:
            (Path(f["roots"][0]["root"]) / "projects" / "new-project").mkdir(mode=0o700)

    with pytest.raises(ValueError):
        f["api"].capture(verified_public=f["handle"], public_bundle=f["public"], cut_request=f["cut"], guard=guard)


def test_project_enumeration_bound_is_checked_before_materialization(configured, monkeypatch):
    f = configured
    root = Path(f["roots"][0]["root"])
    (root / "projects" / "extra").mkdir(mode=0o700)
    monkeypatch.setattr(f["api"], "MAX_PROJECTS", 1)
    with pytest.raises(ValueError, match="BOUND"):
        capture(f)


@pytest.mark.parametrize("fixed_public", ["absent"], indirect=True)
@pytest.mark.parametrize("configured", ["multiple"], indirect=True)
def test_every_native_registry_research_uid_has_its_own_root(configured):
    f = configured
    handle = capture(f)
    assert len(f["roots"]) == 2
    assert {row["layout"]["uid"] for row in f["roots"]} == set(f["f"].registry["research_uids"])
    for row in f["roots"]:
        assert handle.root_for_uid(row["layout"]["uid"]) == Path(row["root"])
    value = deepcopy(f["value"])
    value["research_roots"] = value["research_roots"][:1]
    f["config"].write_bytes(canonical(value))
    with pytest.raises(ValueError, match="RESEARCH_UID_POPULATION"):
        capture(f)


def test_captured_layout_selects_actual_original_project_and_full_native_population(
    configured, registered_case, monkeypatch
):
    f = configured
    native = registered_case
    native["arguments"]["release_identity"]["commit"] = f["f"].args["source_commit"]
    with monkeypatch.context() as actual:
        actual.setattr(os, "geteuid", lambda: _ORIGINAL_OWNER[0])
        native["prepare"]("one")
        execute(native)
    row = f["roots"][0]
    root = Path(row["root"])
    shutil.rmtree(root / "projects/one")
    shutil.copytree(native["root"] / "project", root / "projects/one")
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(native["root"] / "registry.db") + suffix)
        if source.exists():
            shutil.copyfile(source, Path(str(root / "registry.db") + suffix))
    row["layout"]["components"] = row["layout"]["components"][:1]
    for path in (root, *root.rglob("*")):
        path.chmod(0o700 if path.is_dir() else 0o600)
        f["owners"][(path.stat().st_dev, path.stat().st_ino)] = (row["layout"]["uid"], row["layout"]["gid"])
    f["config"].write_bytes(canonical(f["value"]))
    handle = capture(f)
    original_layout = handle.layout_for_uid(row["layout"]["uid"])
    selected_root = handle.root_for_uid(row["layout"]["uid"])
    from tools import demo_gold_research_checkpoint as research

    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with monkeypatch.context() as actual:
        actual.setattr(os, "geteuid", lambda: _ORIGINAL_OWNER[0])
        view = research.verify(selected_root, layout=original_layout, isolated_restore_owner=_ORIGINAL_OWNER)
    assert view["components"]["one"]["native"]["attempt_count"] == 3
    assert view["components"]["one"]["native"]["all_runs_finalized"] is True
    assert view["transport_verified"] is view["whole_set_verified"] is False
    assert {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
