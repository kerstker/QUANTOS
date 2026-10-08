"""Read an installed research roster under the actual root capture coordinator.

The fixed root-owned document supplies the managed roots, every original
research UID and project container. Live descriptor reads bind the complete
configured project population to one fixed-public cut. This module installs
nothing and does not establish a writer barrier, transport verification, native
research semantics or whole-set completeness. Those are separate consumers.
"""

from copy import deepcopy
import itertools
import os
from pathlib import Path, PurePosixPath
import re
import stat

from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from services.research.research_evidence_reader import _open
from services.validation.demo_submission import validate_registry
from tools import demo_gold_checkpoint_public as fixed
from tools.demo_gold_research_checkpoint import validate_layout
from tools.gold_pilot_retention_store import canonical

CONFIG = Path("/etc/quantos/research-checkpoint/layout.json")
DOCUMENT_SCHEMA = "GOLD_RESEARCH_INSTALLED_LAYOUT_V1"
SCHEMA = "GOLD_RESEARCH_FIXED_LAYOUT_V1"
MAX_PROJECTS = 1024
MAX_NODES = 8192
_TOKEN = object()
_STAT = fixed._STAT


def require(ok, reason):
    if not ok:
        raise ValueError("GOLD_RESEARCH_LAYOUT_" + reason)


def _identity(info):
    return {name: getattr(info, name) for name in _STAT}


def _fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()), "FIELDS")


def _absolute(value):
    require(type(value) is str and 0 < len(value) <= 4096, "ROOT_PATH")
    path = Path(value)
    require(
        path.is_absolute()
        and str(path) == value
        and path != Path("/")
        and ".." not in path.parts
        and "." not in path.parts,
        "ROOT_PATH",
    )
    require(not any(part.lower() in ("key", "keys", ".ssh", ".env") for part in path.parts), "SECRET_SCOPE")
    return path


def _relative(value):
    require(type(value) is str and 0 < len(value) <= 4096, "COLLECTION_PATH")
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in ("", ".", "..") for part in path.parts)
        and not any(part.lower() in ("key", "keys", ".ssh", ".env") for part in path.parts)
        and "token" not in path.name.lower(),
        "COLLECTION_PATH",
    )
    return path


def _document(raw, *, public):
    require(0 < len(raw) <= 65536, "DOCUMENT_BOUND")
    document = decode(raw)
    _fields(
        document,
        "schema source_commit account_ref_sha256 registry_sha256 research_roots submission_enabled economic_authority",
    )
    require(
        document["schema"] == DOCUMENT_SCHEMA
        and document["source_commit"] == public["source_commit"]
        and document["account_ref_sha256"] == public["account_ref_sha256"]
        and document["registry_sha256"] == fixed._digest(public["registry"])
        and document["submission_enabled"] is document["economic_authority"] is False,
        "DOCUMENT_BINDING",
    )
    registry = validate_registry(public["registry"])
    roots = document["research_roots"]
    require(type(roots) is list and 1 <= len(roots) <= 16, "ROOT_BOUND")
    names, paths, uids = [], [], []
    for row in roots:
        _fields(row, "name root layout project_collections")
        require(type(row["name"]) is str and re.fullmatch("[a-zA-Z0-9_-]{1,80}", row["name"]), "ROOT_NAME")
        names.append(row["name"])
        paths.append(_absolute(row["root"]))
        layout = validate_layout(row["layout"])
        require(
            layout["source_commit"] == document["source_commit"]
            and layout["uid"] > 0
            and layout["gid"] == layout["uid"],
            "ROLE_IDENTITY",
        )
        uids.append(layout["uid"])
        collections = row["project_collections"]
        require(type(collections) is list and 1 <= len(collections) <= MAX_PROJECTS, "COLLECTION_BOUND")
        collections = [_relative(value) for value in collections]
        require(collections == sorted(set(collections)), "COLLECTION_ORDER")
        for index, path in enumerate(collections):
            require(
                not any(path.is_relative_to(other) or other.is_relative_to(path) for other in collections[index + 1 :]),
                "COLLECTION_OVERLAP",
            )
        for item in layout["components"]:
            item_path = PurePosixPath(item["path"])
            if item["kind"] == "PROJECT":
                require(item_path.parent in collections, "PROJECT_SCOPE")
            else:
                require(
                    not any(
                        item_path.is_relative_to(collection) or collection.is_relative_to(item_path)
                        for collection in collections
                    ),
                    "COLLECTION_OVERLAP",
                )
            if item["registry_path"] is not None:
                registry_path = PurePosixPath(item["registry_path"])
                require(
                    not any(
                        registry_path.is_relative_to(collection) or collection.is_relative_to(registry_path)
                        for collection in collections
                    ),
                    "COLLECTION_OVERLAP",
                )
    require(
        names == sorted(set(names)) and len(set(uids)) == len(uids) and set(uids) == set(registry["research_uids"]),
        "RESEARCH_UID_POPULATION",
    )
    for index, path in enumerate(paths):
        require(
            not any(path.is_relative_to(other) or other.is_relative_to(path) for other in paths[index + 1 :]),
            "ROOT_OVERLAP",
        )
    return document


def _parents(path, owner):
    """Record custody on every resolved ancestor without reading its contents."""
    result = []
    for parent in reversed(path.parents):
        # _open forbids '/', whose descriptor is safe to open directly.
        fd = (
            os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            if parent == Path("/")
            else _open(parent, os.O_RDONLY | os.O_DIRECTORY)
        )
        try:
            before = _identity(os.fstat(fd))
            require(
                stat.S_ISDIR(before["st_mode"])
                and before["st_uid"] in (0, owner[0])
                and not before["st_mode"] & 0o022
                and (before["st_uid"] == 0 or before["st_gid"] == owner[1]),
                "PARENT_CUSTODY",
            )
            require(_identity(os.fstat(fd)) == before, "PARENT_CHANGED")
            result.append(dict(path=str(parent), **{key: before[key] for key in _STAT[:5]}))
        finally:
            os.close(fd)
    return result


def _node(path, *, owner, directory, members=False):
    parents = _parents(path, owner)
    flags = os.O_RDONLY | (os.O_DIRECTORY if directory else 0)
    fd = _open(path, flags)
    try:
        info = _identity(os.fstat(fd))
        require(
            (info["st_uid"], info["st_gid"]) == owner
            and not info["st_mode"] & 0o022
            and (
                stat.S_ISDIR(info["st_mode"]) if directory else stat.S_ISREG(info["st_mode"]) and info["st_nlink"] == 1
            ),
            "ROLE_CUSTODY",
        )
        names = None
        if members:
            with os.scandir(fd) as iterator:
                names = [entry.name for entry in itertools.islice(iterator, MAX_PROJECTS + 1)]
            require(len(names) <= MAX_PROJECTS, "PROJECT_BOUND")
            names.sort()
        require(_identity(os.fstat(fd)) == info, "ORIGINAL_CHANGED")
    finally:
        os.close(fd)
    # Detect a replaced leaf or ancestor between the descriptor and path reads.
    current = _open(path, flags)
    try:
        require(_identity(os.fstat(current)) == info and _parents(path, owner) == parents, "ORIGINAL_CHANGED")
    finally:
        os.close(current)
    return dict(path=str(path), parents=parents, identity=info, members=names)


def _observe(document):
    nodes = {}
    inodes = {}
    populations = {}
    projects = 0

    def record(path, *, owner, directory, members=False):
        require(len(nodes) < MAX_NODES or str(path) in nodes, "NODE_BOUND")
        value = _node(path, owner=owner, directory=directory, members=members)
        identity = value["identity"]
        key = identity["st_dev"], identity["st_ino"]
        require(inodes.setdefault(key, str(path)) == str(path), "PATH_ALIAS")
        previous = nodes.setdefault(str(path), value)
        require(previous == value, "ORIGINAL_CHANGED")
        return value

    for row in document["research_roots"]:
        root = Path(row["root"])
        layout = row["layout"]
        owner = (layout["uid"], layout["gid"])
        record(root, owner=owner, directory=True)
        groups = {value: [] for value in row["project_collections"]}
        for item in layout["components"]:
            directory = item["kind"] in ("PROJECT", "FACTORY", "GOLD_RESEARCH", "FEEDBACK")
            record(root / item["path"], owner=owner, directory=directory)
            if item["kind"] == "PROJECT":
                path = PurePosixPath(item["path"])
                groups[str(path.parent)].append(path.name)
                record(root / item["registry_path"], owner=owner, directory=False)
        for relative, expected in groups.items():
            value = record(root / relative, owner=owner, directory=True, members=True)
            require(value["members"] == sorted(expected), "PROJECT_POPULATION")
            projects += len(expected)
            require(projects <= MAX_PROJECTS, "PROJECT_BOUND")
        populations[row["name"]] = dict(
            uid=owner[0], gid=owner[1], projects={relative: sorted(names) for relative, names in groups.items()}
        )
    return dict(nodes=nodes, project_populations=populations, project_count=projects)


def _context(verified_public, public, cut):
    require(type(public) is dict and type(cut) is dict, "CONTEXT")
    verified = fixed.require_verified(
        verified_public, source_commit=public.get("source_commit"), account_ref_sha256=public.get("account_ref_sha256")
    )
    require(
        verified.public_bundle_sha256 == fixed._digest(public)
        and verified.cut_request_sha256 == fixed._digest(cut)
        and type(cut.get("roles")) is list
        and set(cut["roles"]) == {"executor", "issuer", "controller", "research"},
        "CUT_BINDING",
    )
    return verified


class VerifiedResearchLayout:
    __slots__ = ("_snapshot", "_document", "_public", "_cut", "_verified_public", "_guard")

    def __init__(self, token, snapshot, document, *, public, cut, verified_public, guard):
        require(token is _TOKEN, "CAPTURED_HANDLE_REQUIRED")
        self._snapshot = deepcopy(snapshot)
        self._document = deepcopy(document)
        self._public = deepcopy(public)
        self._cut = deepcopy(cut)
        self._verified_public = verified_public
        self._guard = guard

    def snapshot(self):
        return deepcopy(self._snapshot)

    def recheck(self):
        require(os.geteuid() == 0, "ROOT_REQUIRED")
        self._guard()
        _context(self._verified_public, self._public, self._cut)
        configuration = fixed._leaf(CONFIG)
        require(canonical(configuration) == canonical(self._snapshot["configuration"]), "CONFIGURATION_CHANGED")
        document = _document(fixed._raw(configuration, "research_layout"), public=self._public)
        observed = _observe(document)
        require(canonical(observed) == canonical(self._snapshot["observed"]), "ORIGINAL_CHANGED")
        self._guard()
        require(canonical(fixed._leaf(CONFIG)) == canonical(configuration), "CONFIGURATION_CHANGED")
        require(canonical(_observe(document)) == canonical(observed), "ORIGINAL_CHANGED")
        return self

    def _role(self, uid):
        require(type(uid) is int and uid > 0, "RESEARCH_UID")
        self.recheck()
        rows = [row for row in self._document["research_roots"] if row["layout"]["uid"] == uid]
        require(len(rows) == 1, "RESEARCH_UID")
        return rows[0]

    def layout_for_uid(self, uid):
        return deepcopy(self._role(uid)["layout"])

    def root_for_uid(self, uid):
        return Path(self._role(uid)["root"])


def capture(*, verified_public, public_bundle, cut_request, guard):
    require(os.geteuid() == 0 and callable(guard), "ROOT_GUARD_REQUIRED")
    guard()
    verified = _context(verified_public, public_bundle, cut_request)
    configuration = fixed._leaf(CONFIG)
    identity = configuration["identity"]
    require(identity["st_gid"] == 0 and stat.S_IMODE(identity["st_mode"]) == 0o644, "CONFIGURATION_CUSTODY")
    document = _document(fixed._raw(configuration, "research_layout"), public=public_bundle)
    observed = _observe(document)
    snapshot = dict(
        schema=SCHEMA,
        source_commit=verified.source_commit,
        account_ref_sha256=verified.account_ref_sha256,
        operation_sha256=cut_request["operation_sha256"],
        registry_sha256=cut_request["registry_sha256"],
        public_bundle_sha256=verified.public_bundle_sha256,
        cut_request_sha256=verified.cut_request_sha256,
        fixed_public_sha256=verified.snapshot_sha256,
        configuration=configuration,
        observed=observed,
        configured_project_roster_verified=True,
        writer_barrier_verified=False,
        native_research_verified=False,
        transport_verified=False,
        whole_set_verified=False,
        submission_enabled=False,
        economic_authority=False,
    )
    handle = VerifiedResearchLayout(
        _TOKEN, snapshot, document, public=public_bundle, cut=cut_request, verified_public=verified, guard=guard
    )
    handle.recheck()
    return handle


def require_captured(value, *, verified_public):
    require(
        type(value) is VerifiedResearchLayout
        and type(verified_public) is fixed.VerifiedPublic
        and value._snapshot["fixed_public_sha256"] == verified_public.snapshot_sha256
        and value._snapshot["public_bundle_sha256"] == verified_public.public_bundle_sha256
        and value._snapshot["cut_request_sha256"] == verified_public.cut_request_sha256,
        "CAPTURED_HANDLE_REQUIRED",
    )
    return value.recheck()


class InstalledResearchScope:
    """Fixed configuration and original roots before any data cut is sealed.

    Mutable registry/project contents are deliberately captured only after stop.
    This handle establishes a scope, never a writer barrier or archive success.
    """
    __slots__ = ('_guard', '_snapshot', '_document', '_public', '_cut', '_verified_public')

    def __init__(self, token, snapshot, *, document, public, cut, verified_public, guard):
        require(token is _TOKEN, 'INSTALLED_SCOPE_REQUIRED')
        self._guard = guard
        self._document = deepcopy(document)
        self._public, self._cut = deepcopy(public), deepcopy(cut)
        self._verified_public = verified_public
        self._snapshot = deepcopy(snapshot)
        self.recheck()

    def snapshot(self):
        return deepcopy(self._snapshot)

    def recheck(self):
        require(os.getresuid() == os.getresgid() == (0, 0, 0), 'ROOT_REQUIRED')
        self._guard()
        _context(self._verified_public, self._public, self._cut)
        configuration = fixed._leaf(CONFIG)
        require(configuration == self._snapshot['configuration'], 'CONFIGURATION_CHANGED')
        require(_document(fixed._raw(configuration, 'research_layout'), public=self._public)
            == self._document, 'CONFIGURATION_CHANGED')
        for row in self._document['research_roots']:
            root = row['root']
            uid = row['layout']['uid']
            observed = _node(Path(root), owner=(uid, uid), directory=True)
            require(dict(identity={name: observed['identity'][name] for name in _STAT[:5]},
                parents=observed['parents']) == self._snapshot['roots'][root], 'ROOT_CHANGED')
        require(fixed._leaf(CONFIG) == configuration, 'CONFIGURATION_CHANGED')
        self._guard()
        return self

def installed_scope(value, *, verified_public):
    require(type(value) is VerifiedResearchLayout and value._verified_public is verified_public,
        'CAPTURED_HANDLE_REQUIRED')
    _context(verified_public, value._public, value._cut)
    return InstalledResearchScope(_TOKEN, _installed_snapshot(value._snapshot['configuration'],
        value._document, value._snapshot['observed']['nodes'], value._public, value._cut),
        document=value._document, public=value._public, cut=value._cut,
        verified_public=verified_public, guard=value._guard)


def _installed_snapshot(configuration, document, nodes, public, cut):
    return dict(schema='GOLD_RESEARCH_INSTALLED_SCOPE_V1', configuration=deepcopy(configuration),
        public_bundle_sha256=fixed._digest(public), cut_request_sha256=fixed._digest(cut),
        roots={row['root']: dict(identity={name: nodes[row['root']]['identity'][name] for name in _STAT[:5]},
            parents=deepcopy(nodes[row['root']]['parents'])) for row in document['research_roots']})


def capture_installed_scope(*, verified_public, public_bundle, cut_request, guard):
    """Read the protected declaration without opening mutable research files."""
    require(os.getresuid() == os.getresgid() == (0, 0, 0) and callable(guard), 'ROOT_GUARD_REQUIRED')
    guard()
    _context(verified_public, public_bundle, cut_request)
    configuration = fixed._leaf(CONFIG)
    identity = configuration['identity']
    require(identity['st_gid'] == 0 and stat.S_IMODE(identity['st_mode']) == 0o644, 'CONFIGURATION_CUSTODY')
    document = _document(fixed._raw(configuration, 'research_layout'), public=public_bundle)
    nodes = {row['root']: _node(Path(row['root']), owner=(row['layout']['uid'], row['layout']['gid']),
        directory=True) for row in document['research_roots']}
    return InstalledResearchScope(_TOKEN, _installed_snapshot(configuration, document, nodes,
        public_bundle, cut_request), document=document, public=public_bundle, cut=cut_request,
        verified_public=verified_public, guard=guard)


def require_scope(value, *, verified_public):
    require(type(value) is InstalledResearchScope and value._verified_public is verified_public,
        'INSTALLED_SCOPE_REQUIRED')
    return value.recheck()
