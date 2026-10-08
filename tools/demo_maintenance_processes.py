"""Bounded root-operated process metadata observation; never global quiescence.

No signals, command lines, environments, mapped memory, or state-file contents.
An idle root program may open state later: the exclusive operator window remains
a separate requirement. Results are current observations, never reusable permits.
"""
import os
from pathlib import Path
import re
import stat
import time

from tools.demo_retention_transaction import require

PROC = Path("/proc")
STATE_ROOTS = ("/var/lib/quantos-demo-executor", "/var/lib/quantos-demo-issuer",
               "/var/lib/quantos-demo-controller")
RELEASE_ROOTS = ("/opt/quantos/releases", "/opt/quantos/venvs")
MAX_PROCESSES = 32768
MAX_FDS = 32768


def _metadata(path, maximum=65536):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "PROCESS_METADATA")
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, "PROCESS_METADATA_BUDGET")
    return raw.decode("utf-8", errors="strict")


def _stat(path):
    raw = _metadata(path / "stat")
    prefix, delimiter, tail = raw.rpartition(") ")
    require(bool(delimiter) and " (" in prefix, "PROCESS_IDENTITY")
    pid = int(prefix.split(" (", 1)[0])
    fields = tail.split()
    require(len(fields) >= 20 and str(pid) == path.name and fields[19].isdigit(), "PROCESS_IDENTITY")
    return pid, int(fields[19]), fields[0], int(fields[6])


def _identity(path):
    return _stat(path)[:2]


def _uids(path):
    rows = [line.split()[1:] for line in _metadata(path / "status").splitlines() if line.startswith("Uid:")]
    require(len(rows) == 1 and len(rows[0]) == 4 and all(value.isdigit() for value in rows[0]), "PROCESS_UIDS")
    return tuple(int(value) for value in rows[0])


def _beneath(target, roots):
    target = target.removesuffix(" (deleted)")
    return any(target == root or target.startswith(root + "/") for root in roots)


def _names(path, maximum):
    names = []
    with os.scandir(path) as entries:
        for entry in entries:
            if re.fullmatch("[0-9]+", entry.name):
                names.append(entry.name)
                require(len(names) <= maximum, "PROCESS_SCAN_BUDGET")
    return sorted(names, key=int)


def _inspect_process(pid, dedicated_uids, *, state_roots=None, cgroup_prefixes=None,
                     stop_after_uid_match=False):
    state_roots = STATE_ROOTS if state_roots is None else state_roots
    cgroup_prefixes = ('quantos-demo-',) if cgroup_prefixes is None else cgroup_prefixes
    path = PROC / str(pid)
    before = _identity(path)
    uids = _uids(path)
    reasons = set()
    if set(uids) & dedicated_uids:
        reasons.add("DEDICATED_UID")
        if stop_after_uid_match:
            # This identity already makes the entire observation busy. Reading
            # other-UID fd/maps metadata is unnecessary and may need ptrace.
            require(before == _identity(path) and uids == _uids(path), "PROCESS_CHANGED")
            return {"pid": before[0], "start_ticks": before[1], "reasons": sorted(reasons)}
    rows = _metadata(path / "cgroup").splitlines()
    require(all(len(row.split(":", 2)) == 3 for row in rows), "PROCESS_CGROUP")
    for prefix in cgroup_prefixes:
        if any(part.startswith(prefix) for row in rows for part in row.split(':',2)[2].split('/')):
            reasons.add('DEMO_CGROUP' if prefix=='quantos-demo-' else 'GOLD_CGROUP')
    _, _, state, flags = _stat(path)
    # Kernel threads and exited zombies have no user-space cwd/executable.
    # Dedicated identities/cgroups still remain visible above.
    if state != "Z" and not flags & 0x00200000:
        for kind in ("cwd", "exe"):
            target = os.readlink(path / kind)
            if _beneath(target, state_roots):
                reasons.add("STATE_CONTEXT")
            if 0 in uids and _beneath(target, RELEASE_ROOTS):
                reasons.add("ROOT_RELEASE_CONTEXT")
        fds = _names(path / "fd", MAX_FDS)
        for name in fds:
            # Only symlink metadata, never opening the target descriptor/file.
            if _beneath(os.readlink(path / "fd" / name), state_roots):
                reasons.add("STATE_HANDLE")
        require(fds == _names(path / "fd", MAX_FDS), "PROCESS_FDS_CHANGED")
        # Mapping metadata detects a process that mmap'ed state and closed its
        # original fd. No mapped memory or backing-file bytes are read.
        for line in _metadata(path / "maps", 4 * 1024**2).splitlines():
            fields = line.split(maxsplit=5)
            require(len(fields) >= 5, "PROCESS_MAPS")
            if len(fields) == 6 and _beneath(fields[5], state_roots):
                reasons.add("STATE_MAPPING")
    require(before == _identity(path) and uids == _uids(path), "PROCESS_CHANGED")
    return {"pid": before[0], "start_ticks": before[1], "reasons": sorted(reasons)}


def _scan(dedicated_uids, **scope):
    names = _names(PROC, MAX_PROCESSES)
    # The root coordinator is the only excluded process; no caller exemption list.
    rows = [_inspect_process(int(name), dedicated_uids, **scope) for name in names if int(name) != os.getpid()]
    require(names == _names(PROC, MAX_PROCESSES), "PROCESS_SET_CHANGED")
    return rows


def observe(*, executor_uid, issuer_uid):
    require(os.geteuid() == 0, "OPERATOR_ROOT_REQUIRED")
    require(type(executor_uid) is int and type(issuer_uid) is int and
            0 < executor_uid < 2**32 and 0 < issuer_uid < 2**32 and executor_uid != issuer_uid, "PROCESS_UIDS")
    result = {"schema": "DEMO_PROCESS_METADATA_OBSERVATION_V1", "observed_at_ns": time.time_ns(),
              "quiescence_proven": False, "manual_exclusion_proven": False, "atomic_snapshot": False,
              "processes": None, "status": "PROCESS_SCAN_INCOMPLETE"}
    try:
        first = _scan({executor_uid, issuer_uid})
        second = _scan({executor_uid, issuer_uid})
        require(first == second, "PROCESS_SCAN_CHANGED")
        rows = [row for row in second if row["reasons"]]
        result.update(processes=rows, status="SCOPED_PROCESSES_PRESENT" if rows else "NO_SCOPED_PROCESS_OBSERVED")
    except (OSError, ValueError, UnicodeError):
        # Never export /proc error strings, paths or arbitrary process names.
        pass
    return result


def observe_gold(*, verified_layout, verified_public):
    """Observe every configured research identity and fixed original role root.

    This is current metadata evidence, not writer exclusion or a capture grant.
    Caller-selected roots, UID lists and exemption lists are not accepted.
    """
    from tools import demo_gold_research_layout as roster

    require(os.getresuid() == os.getresgid() == (0,0,0), 'OPERATOR_ROOT_REQUIRED')
    captured=roster.require_captured(verified_layout, verified_public=verified_public)
    return _observe_gold(captured)


def observe_gold_scope(*, verified_scope, verified_public):
    """Observe the configured writers before the stopped-data snapshot exists."""
    from tools import demo_gold_research_layout as roster
    require(os.getresuid() == os.getresgid() == (0, 0, 0), 'OPERATOR_ROOT_REQUIRED')
    captured = roster.require_scope(verified_scope, verified_public=verified_public)
    result = _observe_gold(captured)
    result['schema'] = 'GOLD_INSTALLED_SCOPE_PROCESS_OBSERVATION_V1'
    result['fixed_scope_sha256'] = result.pop('fixed_layout_sha256')
    return result


def _observe_gold(captured):
    from tools.demo_gold_file_leases import _roots
    from tools import demo_gold_namespace_checkpoint as native
    roots=_roots(captured)
    uids={uid for _,uid,_ in roots if uid>0}
    scope=dict(state_roots=tuple(str(path) for _,_,path in roots),
        cgroup_prefixes=('quantos-demo-','quantos-gold-','quantos-research-runner.'),
        stop_after_uid_match=True)
    snapshot=native._digest(captured.snapshot())
    result=dict(schema='GOLD_PROCESS_METADATA_OBSERVATION_V1',observed_at_ns=time.time_ns(),
        operation_sha256=captured._cut['operation_sha256'],source_commit=captured._public['source_commit'],
        fixed_layout_sha256=snapshot,
        scope_sha256=native._digest(dict(layout=snapshot,roots=[(role,uid,str(path)) for role,uid,path in roots])),
        research_uids=sorted(uid for role,uid,_ in roots if role=='research'),
        quiescence_proven=False,manual_exclusion_proven=False,atomic_snapshot=False,
        writer_barrier_verified=False,whole_set_verified=False,submission_enabled=False,economic_authority=False,
        processes=None,status='PROCESS_SCAN_INCOMPLETE')
    try:
        first=_scan(uids,**scope)
        captured.recheck()
        second=_scan(uids,**scope)
        require(first==second,'PROCESS_SCAN_CHANGED')
        captured.recheck()
        rows=[row for row in second if row['reasons']]
        result.update(processes=rows,status='SCOPED_PROCESSES_PRESENT' if rows else 'NO_SCOPED_PROCESS_OBSERVED')
    except (OSError,ValueError,UnicodeError):
        pass
    return result
