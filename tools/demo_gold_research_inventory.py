"""Native research role adapter for the existing Gold multipart transport.

An explicit layout is an operation input, not an installed-roster capability.
This holds the original research read set until the final publication, retains
exact file/DB/WAL bytes, and replays native semantics on isolated restores.
Root-controlled roster admission, writer quiescence, cross-role consistency and
production activation remain separate required consumers.
"""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
import os
from pathlib import Path
import stat
import tempfile
import time

from services.research.research_evidence_reader import _ReadSet, _open
from services.validation.demo_submission import validate_registry
from quantos.contracts.research_evidence_json import digest as research_digest
from tools import demo_gold_namespace_checkpoint as native
from tools import demo_gold_research_checkpoint as research
from tools import demo_namespace_checkpoint as checkpoint
from tools.demo_gold_checkpoint_transport import _extents
from tools.demo_gold_research_layout import _absolute as _canonical_root

SCHEMA = "GOLD_RESEARCH_INVENTORY_V1"
VIEW_SCHEMA = "GOLD_RESEARCH_ROLE_SEMANTIC_V1"
FIELDS = (
    "schema operation_sha256 cut_request_sha256 namespace source_commit registry_sha256 "
    "account_ref_sha256 pilot_identity source_root index semantic_view component_bounds files directories "
    "payload_bytes source_attachment_sha256 source_dependencies research_layout submission_enabled economic_authority"
)
VIEW_FIELDS = (
    "schema research native_research_verified configured_project_roster_verified "
    "writer_barrier_verified transport_verified whole_set_verified submission_enabled economic_authority"
)
require = native.require


def _scope(layout, public, cut=None):
    layout = research.validate_layout(layout)
    native._fields(public, "schema source_commit registry account_ref_sha256 base_runtime current_runtime records")
    registry = validate_registry(public["registry"])
    require(
        public["schema"] == "GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1"
        and public["source_commit"] == layout["source_commit"]
        and type(public["records"]) is dict
        and layout["uid"] > 0
        and layout["gid"] == layout["uid"]
        and layout["uid"] in registry["research_uids"],
        "GOLD_RESEARCH_ROLE_SCOPE",
    )
    native._hash(public["account_ref_sha256"])
    if cut is not None:
        native._fields(
            cut,
            "schema operation_sha256 source_commit registry_sha256 public_bundle_sha256 roles "
            "writer_barrier_sha256 opening_at_ms submission_enabled economic_authority",
        )
        require(
            cut["schema"] == "GOLD_CHECKPOINT_CUT_REQUEST_V1"
            and cut["source_commit"] == layout["source_commit"]
            and cut["registry_sha256"] == native._digest(registry)
            and cut["public_bundle_sha256"] == native._digest(public)
            and type(cut["roles"]) is list
            and cut["roles"]
            and all(type(role) is str for role in cut["roles"])
            and len(cut["roles"]) == len(set(cut["roles"]))
            and "research" in cut["roles"]
            and set(cut["roles"]) <= {"research", "executor", "controller", "issuer"}
            and type(cut["opening_at_ms"]) is int
            and 0 < cut["opening_at_ms"] < 2**53
            and cut["submission_enabled"] is cut["economic_authority"] is False,
            "GOLD_RESEARCH_ROLE_CUT",
        )
        native._hash(cut["operation_sha256"])
        native._hash(cut["writer_barrier_sha256"])
    return layout


def _view(value):
    # Original inode/time custody is retained in the role index and receipt.
    # A restored tree has new inodes and an explicitly isolated current owner.
    core = {key: deepcopy(item) for key, item in value.items() if key not in ("read_identity_sha256", "custody_mode")}
    return dict(
        schema=VIEW_SCHEMA,
        research=core,
        native_research_verified=True,
        configured_project_roster_verified=False,
        writer_barrier_verified=False,
        transport_verified=False,
        whole_set_verified=False,
        submission_enabled=False,
        economic_authority=False,
    )


def _node(path, uid, *, directory):
    fd = _open(path, os.O_RDONLY | (os.O_DIRECTORY if directory else 0))
    try:
        info = os.fstat(fd)
        require(
            info.st_uid == info.st_gid == uid
            and not info.st_mode & 0o022
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
            "GOLD_RESEARCH_ROLE_CUSTODY",
        )
        require(native._identity(info) == native._identity(path.lstat()), "GOLD_RESEARCH_ROLE_CHANGED")
        return native._identity(info, directory=directory)
    finally:
        os.close(fd)


def _entries(root, held, uid, directories):
    # The bounded native reader determines the payload, including opaque tapes
    # and original SQLite sidecars. No suffix-based archive discovery occurs.
    files = held.files_manifest()
    names = {path.relative_to(root).as_posix() for path in held._capture.reads.directories if path != root}
    for relative in (*names, *files):
        names.update(parent.as_posix() for parent in Path(relative).parents if parent.as_posix() != ".")
    require(len(files) + len(names) < research.MAX_FILES, "GOLD_RESEARCH_ROLE_FILE_BOUND")
    rows = []
    for relative in sorted(names):
        path = root / relative
        directories.directory(path)
        rows.append(
            dict(
                kind="DIRECTORY",
                component="research",
                relative=relative,
                source_identity=_node(path, uid, directory=True),
            )
        )
    for relative, captured in sorted(files.items()):
        path = root / relative
        identity = _node(path, uid, directory=False)
        require(identity["size"] == captured["size"], "GOLD_RESEARCH_ROLE_CHANGED")
        extents = list(_extents(path))
        rows.append(
            dict(
                kind="FILE",
                component="research",
                relative=relative,
                source_identity=identity,
                original_sha256=captured["sha256"],
                stored_sha256=captured["sha256"],
                logical_bytes=captured["size"],
                snapshot_method="STABLE_FILE_READ",
                extents=extents,
            )
        )
    held.recheck()
    return sorted(rows, key=lambda row: row["relative"])


class ResearchCaptureInventory:
    def __init__(self, *, root, layout, public_bundle, cut_request, source_attachment, guard):
        require(callable(guard), "GOLD_RESEARCH_ROLE_GUARD")
        guard()
        self.layout = _scope(layout, public_bundle, cut_request)
        self.uid = self.layout["uid"]
        require(
            os.getresuid() == (self.uid,) * 3
            and os.getresgid() == (self.uid,) * 3
            and set(os.getgroups()) <= {self.uid},
            "GOLD_RESEARCH_ROLE_REQUIRED",
        )
        self.root = _canonical_root(str(root))
        require(self.root.resolve() == self.root and ".." not in self.root.parts, "GOLD_RESEARCH_ROLE_ROOT")
        self.guard = guard
        self.closed = False
        self.stack = ExitStack()
        self.source = self.layout["source_commit"]
        self.namespace = "research"
        self.operation = cut_request["operation_sha256"]
        self.public = deepcopy(public_bundle)
        self.cut = deepcopy(cut_request)
        self.source_attachment = deepcopy(source_attachment)
        native._source_scope(self.source_attachment, self.source, self.public)
        self.attachment_sha256 = native._source_attachment(self.source_attachment, verify_bundle=False)
        self.started = time.time_ns() // 1_000_000
        try:
            self.source_trees = self.stack.enter_context(native.verified_source_trees(self.source_attachment))
            self.root_identity = _node(self.root, self.uid, directory=True)
            self.held = research.capture_verified(self.root, layout=self.layout)
            self.view = _view(self.held.view())
            self.directories = _ReadSet(maximum_directory_entries=research.MAX_FILES)
            self.entries = _entries(self.root, self.held, self.uid, self.directories)
            self.before = native._source_digest(self.entries)
            self.scratch = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="gold-research-index-")))
            self.index_directory = self.scratch / "index"
            self.index = native._write_index(self.index_directory, self.entries)
            self.recheck()
        except BaseException:
            self.close()
            raise

    def iter_entries(self):
        for row in self.entries:
            yield deepcopy(row)

    def semantic_view(self):
        return deepcopy(self.view)

    def payload_path(self, relative):
        require(not self.closed and relative in self.view["research"]["files"], "GOLD_RESEARCH_ROLE_PAYLOAD")
        self.guard()
        return self.root / relative

    def manifest(self):
        return dict(
            schema=SCHEMA,
            operation_sha256=self.operation,
            cut_request_sha256=native._digest(self.cut),
            namespace="research",
            source_commit=self.source,
            registry_sha256=native._digest(self.public["registry"]),
            account_ref_sha256=self.public["account_ref_sha256"],
            pilot_identity=None,
            source_root=dict(path=str(self.root), **{k: v for k, v in self.root_identity.items() if k != "nlink"}),
            index=deepcopy(self.index),
            semantic_view=self.semantic_view(),
            research_layout=deepcopy(self.layout),
            component_bounds={"research": native._bounds(research.MAX_FILES, research.MAX_FILES, research.MAX_BYTES)},
            files=sum(row["kind"] == "FILE" for row in self.entries),
            directories=sum(row["kind"] == "DIRECTORY" for row in self.entries),
            payload_bytes=self.view["research"]["bytes"],
            source_attachment_sha256=self.attachment_sha256,
            source_dependencies=[],
            submission_enabled=False,
            economic_authority=False,
        )

    def recheck(self):
        require(not self.closed, "GOLD_RESEARCH_ROLE_CLOSED")
        self.guard()
        _scope(self.layout, self.public, self.cut)
        require(
            os.getresuid() == (self.uid,) * 3
            and os.getresgid() == (self.uid,) * 3
            and set(os.getgroups()) <= {self.uid},
            "GOLD_RESEARCH_ROLE_REQUIRED",
        )
        self.held.recheck()
        self.directories.recheck()
        self.source_trees.recheck(self.source_attachment)
        require(self.root_identity == _node(self.root, self.uid, directory=True), "GOLD_RESEARCH_ROLE_CHANGED")
        for row in self.entries:
            require(
                row["source_identity"]
                == _node(self.root / row["relative"], self.uid, directory=row["kind"] == "DIRECTORY"),
                "GOLD_RESEARCH_ROLE_CHANGED",
            )
        self.guard()
        completed = time.time_ns() // 1_000_000
        require(self.cut["opening_at_ms"] <= self.started <= completed, "GOLD_RESEARCH_ROLE_CLOCK")
        return dict(
            schema=native.RECEIPT_SCHEMA,
            operation_sha256=self.operation,
            cut_request_sha256=native._digest(self.cut),
            namespace="research",
            source_commit=self.source,
            registry_sha256=native._digest(self.public["registry"]),
            inventory_sha256=native._digest(self.manifest()),
            semantic_view_sha256=native._digest(self.view),
            source_attachment_sha256=self.attachment_sha256,
            before_sha256=self.before,
            after_sha256=self.before,
            started_at_ms=self.started,
            completed_at_ms=completed,
            stable=True,
            worker_identity=native._worker_identity(),
            submission_enabled=False,
            economic_authority=False,
        )

    def close(self):
        if not self.closed:
            self.stack.close()
            self.closed = True


@contextmanager
def capture_inventory(**kwargs):
    inventory = ResearchCaptureInventory(**kwargs)
    try:
        yield inventory
        inventory.recheck()
    finally:
        inventory.close()


def original_custody(inventory, *, layout):
    """One canonical original-root check, shared by archive/public consumers."""
    original = inventory["source_root"]
    native._fields(original, "path device inode uid gid mode")
    require(
        type(original["path"]) is str
        and Path(original["path"]).is_absolute()
        and str(Path(original["path"])) == original["path"]
        and ".." not in Path(original["path"]).parts
        and all(type(original[key]) is int and original[key] >= 0 for key in ("device", "inode", "uid", "gid", "mode"))
        and original["inode"] > 0
        and original["uid"] == original["gid"] == layout["uid"]
        and stat.S_ISDIR(original["mode"])
        and not original["mode"] & 0o022,
        "GOLD_RESEARCH_ROLE_ORIGINAL_CUSTODY",
    )
    return layout['uid']


def inventory_limits(inventory, *, public_bundle, source_attachment, verified_source_trees=None):
    native._fields(inventory, FIELDS)
    require(
        inventory["schema"] == SCHEMA
        and inventory["namespace"] == "research"
        and inventory["submission_enabled"] is inventory["economic_authority"] is False,
        "GOLD_RESEARCH_ROLE_INVENTORY",
    )
    layout = _scope(inventory["research_layout"], public_bundle)
    require(
        inventory["source_commit"] == layout["source_commit"]
        and inventory["registry_sha256"] == native._digest(public_bundle["registry"])
        and inventory["account_ref_sha256"] == public_bundle["account_ref_sha256"]
        and inventory["pilot_identity"] is None
        and inventory["source_dependencies"] == [],
        "GOLD_RESEARCH_ROLE_CONTEXT",
    )
    original_custody(inventory, layout=layout)
    original = inventory['source_root']
    native._source_scope(source_attachment, layout["source_commit"], public_bundle)
    require(
        native._digest(native.source_attachment_identity(source_attachment)) == inventory["source_attachment_sha256"],
        "GOLD_RESEARCH_ROLE_SOURCE",
    )
    if verified_source_trees is not None:
        require(type(verified_source_trees) is native._VerifiedSourceTrees, "GOLD_RESEARCH_ROLE_SOURCE")
        verified_source_trees.recheck(source_attachment)
    view = inventory["semantic_view"]
    native._fields(view, VIEW_FIELDS)
    require(
        view["schema"] == VIEW_SCHEMA
        and view["native_research_verified"] is True
        and all(
            view[key] is False
            for key in VIEW_FIELDS.split()
            if key.endswith("_verified") and key != "native_research_verified"
        )
        and view["submission_enabled"] is view["economic_authority"] is False,
        "GOLD_RESEARCH_ROLE_VIEW",
    )
    core = view["research"]
    native._fields(
        core,
        "schema layout_sha256 original_root components registries files databases trial_ledgers "
        "bytes writer_barrier_verified transport_verified whole_set_verified submission_enabled economic_authority semantic_sha256",
    )
    require(
        core["schema"] == research.SCHEMA
        and core["layout_sha256"] == research_digest(layout)
        and core["original_root"] == original["path"]
        and core["semantic_sha256"] == research_digest({k: v for k, v in core.items() if k != "semantic_sha256"})
        and all(
            core[key] is False
            for key in (
                "writer_barrier_verified",
                "transport_verified",
                "whole_set_verified",
                "submission_enabled",
                "economic_authority",
            )
        ),
        "GOLD_RESEARCH_ROLE_VIEW_BINDING",
    )
    expected = {"research": native._bounds(research.MAX_FILES, research.MAX_FILES, research.MAX_BYTES)}
    require(
        inventory["component_bounds"] == expected
        and all(
            type(inventory[key]) is int and 0 <= inventory[key] <= maximum
            for key, maximum in (
                ("files", research.MAX_FILES),
                ("directories", research.MAX_FILES),
                ("payload_bytes", research.MAX_BYTES),
            )
        )
        and inventory["files"] + inventory["directories"] < research.MAX_FILES
        and inventory["payload_bytes"] == core["bytes"],
        "GOLD_RESEARCH_ROLE_BUDGET",
    )
    _file_population(inventory)
    extents = research.MAX_FILES + (research.MAX_BYTES + native.MAX_EXTENT_BYTES - 1) // native.MAX_EXTENT_BYTES
    return dict(
        component_bounds=expected,
        max_files=research.MAX_FILES,
        max_directories=research.MAX_FILES,
        max_bytes=research.MAX_BYTES,
        max_extent_members=extents,
        max_index_bytes=research.MAX_FILES * (6 * 4096 + 2048) + extents * 256,
        pending_source_tree_components=[],
    )


def _file_population(inventory):
    """Validate the complete original leaf budget before extracting any FILE."""
    layout = research.validate_layout(inventory["research_layout"])
    files = inventory["semantic_view"]["research"]["files"]
    require(type(files) is dict and len(files) < research.MAX_FILES, "GOLD_RESEARCH_ROLE_FILE_POPULATION")
    databases = set()
    trees = []
    for item in layout["components"]:
        if item["kind"] in research.TREES:
            trees.append(item["path"])
        else:
            databases.add(item["path"])
        if item["registry_path"] is not None:
            databases.add(item["registry_path"])
    database_files = {path + suffix for path in databases for suffix in ("", "-wal", "-shm")}
    total = 0
    for relative, value in files.items():
        research._relative(relative)
        require(
            len(relative.encode("utf-8")) <= 4096
            and (
                relative in database_files or any(relative == path or relative.startswith(path + "/") for path in trees)
            ),
            "GOLD_RESEARCH_ROLE_FILE_SCOPE",
        )
        native._fields(value, "size sha256")
        require(
            type(value["size"]) is int and 0 <= value["size"] <= research.MAX_FILE_BYTES,
            "GOLD_RESEARCH_ROLE_LEAF_BUDGET",
        )
        native._hash(value["sha256"])
        total += value["size"]
        require(total <= research.MAX_BYTES, "GOLD_RESEARCH_ROLE_BYTE_BUDGET")
    require(
        type(inventory["files"]) is int
        and inventory["files"] == len(files)
        and type(inventory["semantic_view"]["research"]["bytes"]) is int
        and inventory["semantic_view"]["research"]["bytes"] == inventory["payload_bytes"] == total,
        "GOLD_RESEARCH_ROLE_FILE_POPULATION",
    )
    return files


def check_index_entries(inventory, entries):
    """Bind the parsed original index to the bounded manifest before payload IO."""
    files = _file_population(inventory)
    uid = inventory["research_layout"]["uid"]
    observed = {}
    require(
        len(entries) == inventory["files"] + inventory["directories"] < research.MAX_FILES,
        "GOLD_RESEARCH_ROLE_INDEX_BUDGET",
    )
    for row in entries:
        research._relative(row["relative"])
        require(
            row["component"] == "research" and row["source_identity"]["uid"] == row["source_identity"]["gid"] == uid,
            "GOLD_RESEARCH_ROLE_INDEX_OWNER",
        )
        if row["kind"] == "FILE":
            require(
                type(row["logical_bytes"]) is int and 0 <= row["logical_bytes"] <= research.MAX_FILE_BYTES,
                "GOLD_RESEARCH_ROLE_LEAF_BUDGET",
            )
            observed[row["relative"]] = dict(size=row["logical_bytes"], sha256=row["stored_sha256"])
    require(observed == files, "GOLD_RESEARCH_ROLE_INDEX_POPULATION")


def verify_restored(root, *, inventory, role_receipt, public_bundle, source_attachment, trees, cut_request):
    inventory_limits(
        inventory, public_bundle=public_bundle, source_attachment=source_attachment, verified_source_trees=trees
    )
    _scope(inventory["research_layout"], public_bundle, cut_request)
    native._fields(
        role_receipt,
        "schema operation_sha256 cut_request_sha256 namespace source_commit registry_sha256 "
        "inventory_sha256 semantic_view_sha256 source_attachment_sha256 before_sha256 after_sha256 "
        "started_at_ms completed_at_ms stable worker_identity submission_enabled economic_authority",
    )
    worker = role_receipt["worker_identity"]
    native._fields(worker, "uid gid groups pid start_ticks")
    uid = inventory["research_layout"]["uid"]
    require(
        all(type(worker[key]) is int and worker[key] > 0 for key in ("uid", "gid", "pid", "start_ticks"))
        and worker["uid"] == worker["gid"] == uid
        and type(worker["groups"]) is list
        and all(type(group) is int for group in worker["groups"])
        and worker["groups"] == sorted(set(worker["groups"]))
        and set(worker["groups"]) <= {uid},
        "GOLD_RESEARCH_ROLE_RECEIPT_OWNER",
    )
    require(
        role_receipt["schema"] == native.RECEIPT_SCHEMA
        and role_receipt["namespace"] == "research"
        and role_receipt["inventory_sha256"] == native._digest(inventory)
        and role_receipt["semantic_view_sha256"] == native._digest(inventory["semantic_view"])
        and role_receipt["cut_request_sha256"] == inventory["cut_request_sha256"] == native._digest(cut_request)
        and role_receipt["operation_sha256"] == inventory["operation_sha256"] == cut_request["operation_sha256"]
        and role_receipt["source_commit"] == inventory["source_commit"]
        and role_receipt["registry_sha256"] == inventory["registry_sha256"]
        and role_receipt["source_attachment_sha256"] == inventory["source_attachment_sha256"]
        and role_receipt["stable"] is True
        and role_receipt["submission_enabled"] is role_receipt["economic_authority"] is False
        and type(role_receipt["started_at_ms"]) is type(role_receipt["completed_at_ms"]) is int
        and cut_request["opening_at_ms"] <= role_receipt["started_at_ms"] <= role_receipt["completed_at_ms"],
        "GOLD_RESEARCH_ROLE_RECEIPT",
    )
    root = Path(root).absolute()
    original = Path(inventory["source_root"]["path"])
    require(
        root.resolve() == root
        and ".." not in root.parts
        and root != original
        and not root.is_relative_to(original)
        and not original.is_relative_to(root)
        and all(
            root != protected and protected not in root.parents
            for protected in (*checkpoint.PROTECTED_ROOTS, *native.ROLE_ROOTS.values())
        ),
        "GOLD_RESEARCH_ROLE_ISOLATED_RESTORE",
    )
    entries = list(native._index_rows(root.parent, inventory["index"], os.geteuid(), original_private=False))
    check_index_entries(inventory, entries)
    require(
        all(
            row["component"] == "research" and row["source_identity"]["uid"] == row["source_identity"]["gid"] == uid
            for row in entries
        )
        and len(entries) == inventory["files"] + inventory["directories"]
        and sum(row["kind"] == "FILE" for row in entries) == inventory["files"]
        and native._source_digest(entries) == role_receipt["before_sha256"] == role_receipt["after_sha256"],
        "GOLD_RESEARCH_ROLE_INDEX_BINDING",
    )
    if 'fixed_public' in public_bundle['records']:
        from tools.demo_gold_checkpoint_public import validate_archived
        validate_archived(public_bundle['records']['fixed_public'], public_bundle=public_bundle,
            cut_request=cut_request, inventory=inventory, role_receipt=role_receipt,
            index_entries=entries, as_of_ms=role_receipt['completed_at_ms'])
    files = {
        row["relative"]: dict(size=row["logical_bytes"], sha256=row["stored_sha256"])
        for row in entries
        if row["kind"] == "FILE"
    }
    require(files == inventory["semantic_view"]["research"]["files"], "GOLD_RESEARCH_ROLE_FILE_POPULATION")
    expected = {row["relative"]: row for row in entries}
    before = native._restored_snapshot(root, expected, os.geteuid())
    held = research.capture_verified(
        root,
        layout=inventory["research_layout"],
        isolated_restore_owner=(os.geteuid(), os.getegid()),
        original_root=original,
    )
    actual = _view(held.view())
    require(actual == inventory["semantic_view"], "GOLD_RESEARCH_ROLE_NATIVE_REPLAY")
    held.recheck()
    trees.recheck(source_attachment)
    require(before == native._restored_snapshot(root, expected, os.geteuid()), "GOLD_RESEARCH_ROLE_RESTORE_CHANGED")
    return actual
