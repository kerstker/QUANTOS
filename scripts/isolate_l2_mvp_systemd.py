#!/usr/bin/python3 -I
"""Plan or apply exact systemd isolation for the QUANTOS L2 MVP deny set.

The privileged mode is deliberately self-contained: a root-owned installed copy
does not import policy or code from a user-writable checkout.  Repository tests
bind the frozen pairs below to the canonical scheduling policy.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Callable, Iterable, Mapping, Sequence


DENIED_TASK_MODULE_PAIRS: tuple[tuple[str, str], ...] = (
    ("QUANTOS_alpha_lifecycle_cockpit", "alpha_lifecycle_cockpit"),
    ("QUANTOS_alpha_maintenance", "alpha_maintenance_sweep"),
    ("QUANTOS_alpha_paper_cycle", "alpha_paper_cycle"),
    ("QUANTOS_alpha_production_workflow", "alpha_production_workflow"),
    ("QUANTOS_candidate_dry_run", "dry_run_candidate_multi"),
    ("QUANTOS_candidate_portfolio_evidence", "candidate_portfolio_evidence"),
    ("QUANTOS_factory_loop", "crypto_factory_loop"),
    ("QUANTOS_ic_panel_refresh", "ic_panel_research"),
    ("QUANTOS_paper_alpha_evaluation", "paper_alpha_evaluation"),
    ("QUANTOS_paper_realtime", "paper_realtime"),
    ("QUANTOS_paper_realtime_10k", "paper_realtime_10k"),
    ("QUANTOS_paper_research_risk_bundle", "paper_research_risk_bundle"),
    ("QUANTOS_register_alpha_catalog", "register_alpha_catalog"),
    ("QUANTOS_forward_track", "forward_track_advance"),
    ("QUANTOS_gonogo_report", "gonogo_report"),
    ("QUANTOS_hourly_alpha_advance", "hourly_alpha_advance"),
    ("QUANTOS_mft_l2_runtime", "mft_l2_runtime"),
    ("QUANTOS_paper_health", "paper_health"),
    ("QUANTOS_paper_multivenue_sor", "paper_multivenue_sor"),
    ("QUANTOS_paper_operational_evidence", "paper_operational_evidence"),
    ("QUANTOS_paper_order_preview", "paper_order_preview"),
    ("QUANTOS_paper_shadow_auto", "paper_shadow_auto"),
    ("QUANTOS_paper_shadow_realtime", "paper_shadow_realtime"),
    ("QUANTOS_realtime_alpha_discovery", "realtime_alpha_discovery"),
    (
        "QUANTOS_realtime_alpha_execution_paper",
        "realtime_alpha_execution_paper",
    ),
    ("QUANTOS_realtime_alpha_formal_shadow", "realtime_alpha_formal_shadow"),
    ("QUANTOS_realtime_alpha_forward_shadow", "realtime_alpha_forward_shadow"),
    ("QUANTOS_rpi_low_frequency_research", "rpi_low_frequency_research"),
)
POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        DENIED_TASK_MODULE_PAIRS,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
).hexdigest()

SAFE_ONESHOT_MODULES = frozenset(
    {
        "artifact_admission_seal",
        "binance_book_depth_forward_refresh",
        "binance_realtime_drift_alerts",
        "binance_realtime_features",
        "binance_realtime_market",
        "binance_rpi_depth",
        "binance_systemic_risk",
        "cftc_tff_forward_refresh",
        "cross_venue_liquidation_health",
        "defillama_stablecoin_forward_refresh",
        "defillama_tvl_forward_refresh",
        "domain_event_ops",
        "domain_event_seal",
        "external_heartbeat",
        "hyperliquid_public_forward_refresh",
        "l2_backup",
        "okx_borrow_forward_refresh",
        "raw_archive_audit",
        "wikimedia_attention_forward_refresh",
    }
)
SUPPORT_UNITS = frozenset(
    {
        "quantos-binance-realtime-stream.service",
        "quantos-bybit-realtime-stream.service",
        "quantos-console-api.service",
        "quantos-console-frontend.service",
        "quantos-l2-watchdog.service",
        "quantos-l2-watchdog.timer",
        "quantos-proxy-environment.service",
        "quantos-public-proxy-runtime.service",
    }
)
GENERIC_UNITS = ("quantos-l2@.service", "quantos-l2@.timer")
SYSTEMCTL = "/usr/bin/systemctl"
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
SYSTEMD_ENABLEMENT_ROOTS = (
    Path("/etc/systemd/system"),
    Path("/run/systemd/system"),
    Path("/usr/local/lib/systemd/system"),
    Path("/usr/lib/systemd/system"),
)
QUARANTINE_ROOT = Path("/var/lib/quantos/systemd-quarantine")
APPLY_LOCK = Path("/run/quantos-l2-mvp-isolation.lock")
_SYSTEMCTL_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "SYSTEMD_COLORS": "0",
    "SYSTEMD_PAGER": "",
}
_SHOW_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "FragmentPath",
    "DropInPaths",
    "NeedDaemonReload",
    "MainPID",
    "ControlPID",
    "Result",
)


class IsolationError(RuntimeError):
    """The host cannot be safely inspected or isolated."""


@dataclass(frozen=True)
class UnitState:
    unit: str
    load_state: str
    active_state: str
    sub_state: str
    unit_file_state: str
    fragment_path: str
    drop_in_paths: str
    need_daemon_reload: str
    main_pid: int
    control_pid: int
    result: str


@dataclass(frozen=True)
class FragmentRecord:
    unit: str
    source: str
    sha256: str
    size: int
    mode: int
    uid: int
    gid: int
    kind: str = "regular"


@dataclass(frozen=True)
class HostPlan:
    policy_sha256: str
    denied_modules: int
    denied_units: int
    active_denied_timers: int
    active_denied_services: int
    startable_denied_services: int
    regular_fragments_to_quarantine: int
    generic_templates_to_quarantine: int
    reviewed_non_denied_units: int
    enablement_links: tuple[str, ...]
    unknown_units: tuple[str, ...]
    isolated: bool
    verdict: str
    states: tuple[UnitState, ...]
    fragments: tuple[FragmentRecord, ...]
    generic_fragments: tuple[FragmentRecord, ...]

    def to_dict(self) -> dict[str, object]:
        document = asdict(self)
        document["states"] = [asdict(item) for item in self.states]
        document["fragments"] = [asdict(item) for item in self.fragments]
        document["generic_fragments"] = [
            asdict(item) for item in self.generic_fragments
        ]
        return document


Runner = Callable[..., subprocess.CompletedProcess[str]]


def denied_unit_names() -> tuple[str, ...]:
    """Return the fixed 56 concrete unit names; never a systemd glob."""

    return tuple(
        name
        for _task, module in DENIED_TASK_MODULE_PAIRS
        for name in (
            f"quantos-l2@{module}.service",
            f"quantos-l2@{module}.timer",
        )
    )


def _safe_concrete_units() -> frozenset[str]:
    return frozenset(
        name
        for module in SAFE_ONESHOT_MODULES
        for name in (
            f"quantos-l2@{module}.service",
            f"quantos-l2@{module}.timer",
        )
    )


def _known_units() -> frozenset[str]:
    return frozenset(
        {
            *_safe_concrete_units(),
            *SUPPORT_UNITS,
            *denied_unit_names(),
            *GENERIC_UNITS,
        }
    )


def _run_systemctl(
    arguments: Sequence[str],
    *,
    runner: Runner = subprocess.run,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    command = [SYSTEMCTL, *arguments]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
            env=dict(_SYSTEMCTL_ENV),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IsolationError(
            f"systemctl {' '.join(arguments[:2])} could not complete"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-800:]
        raise IsolationError(
            f"systemctl {' '.join(arguments[:2])} failed"
            + (f": {detail}" if detail else "")
        )
    return completed


def _parse_properties(raw: str, expected_unit: str) -> UnitState:
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            continue
        name, separator, value = line.partition("=")
        if not separator or name not in _SHOW_PROPERTIES or name in values:
            raise IsolationError("systemd returned invalid unit metadata")
        values[name] = value
    optional = {"MainPID", "ControlPID"} if expected_unit.endswith(".timer") else set()
    if (
        set(values) != set(_SHOW_PROPERTIES) - optional
        and set(values) != set(_SHOW_PROPERTIES)
    ) or values["Id"] != expected_unit:
        raise IsolationError("systemd returned incomplete or inconsistent metadata")
    for name in optional:
        values.setdefault(name, "0")
    for name in ("MainPID", "ControlPID"):
        if not values[name].isdigit():
            raise IsolationError("systemd returned a non-numeric process identifier")
    return UnitState(
        unit=expected_unit,
        load_state=values["LoadState"],
        active_state=values["ActiveState"],
        sub_state=values["SubState"],
        unit_file_state=values["UnitFileState"],
        fragment_path=values["FragmentPath"],
        drop_in_paths=values["DropInPaths"],
        need_daemon_reload=values["NeedDaemonReload"],
        main_pid=int(values["MainPID"]),
        control_pid=int(values["ControlPID"]),
        result=values["Result"],
    )


def inspect_unit(unit: str, *, runner: Runner = subprocess.run) -> UnitState:
    command: list[str] = ["show", unit, "--all", "--no-pager"]
    for name in _SHOW_PROPERTIES:
        command.extend(("--property", name))
    return _parse_properties(_run_systemctl(command, runner=runner).stdout, unit)


def _unit_inventory(*, runner: Runner) -> frozenset[str]:
    names: set[str] = set()
    files = _run_systemctl(
        ["list-unit-files", "quantos-*", "--no-legend", "--no-pager"],
        runner=runner,
    )
    for line in files.stdout.splitlines():
        fields = line.split()
        if not fields or not fields[0].startswith("quantos-"):
            raise IsolationError("systemd returned invalid QUANTOS unit-file metadata")
        names.add(fields[0])
    runtime = _run_systemctl(
        [
            "list-units",
            "quantos-*",
            "--all",
            "--plain",
            "--no-legend",
            "--no-pager",
        ],
        runner=runner,
    )
    for line in runtime.stdout.splitlines():
        fields = line.split()
        candidates = [field for field in fields[:2] if field.startswith("quantos-")]
        if len(candidates) != 1:
            raise IsolationError("systemd returned invalid QUANTOS runtime metadata")
        names.add(candidates[0])
    return frozenset(names)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_fragment_record(
    unit: str,
    path: Path,
    *,
    required_owner_uid: int,
) -> FragmentRecord:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IsolationError(f"unit fragment disappeared: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise IsolationError(f"unit fragment is not a regular file: {path}")
    if metadata.st_uid != required_owner_uid or metadata.st_mode & 0o022:
        raise IsolationError(f"unit fragment ownership or mode is unsafe: {path}")
    try:
        digest = _sha256_file(path)
    except OSError as exc:
        raise IsolationError(f"unit fragment cannot be hashed: {path}") from exc
    return FragmentRecord(
        unit=unit,
        source=os.fspath(path),
        sha256=digest,
        size=metadata.st_size,
        mode=stat.S_IMODE(metadata.st_mode),
        uid=metadata.st_uid,
        gid=metadata.st_gid,
    )


def _validate_denied_state(
    state: UnitState,
    *,
    unit_dir: Path,
    required_owner_uid: int,
) -> FragmentRecord | None:
    exact_path = unit_dir / state.unit
    if state.need_daemon_reload != "no":
        raise IsolationError(f"stale systemd metadata for {state.unit}")
    if state.drop_in_paths:
        raise IsolationError(f"unexpected drop-in for {state.unit}")
    if state.load_state == "not-found":
        if state.fragment_path or os.path.lexists(exact_path):
            raise IsolationError(f"unloaded fragment exists for {state.unit}")
        return None
    if state.load_state == "masked":
        if state.fragment_path not in {"", os.fspath(exact_path)}:
            raise IsolationError(f"masked fragment path is unexpected for {state.unit}")
        try:
            metadata = exact_path.lstat()
        except OSError as exc:
            raise IsolationError(f"masked fragment is missing for {state.unit}") from exc
        try:
            target = os.readlink(exact_path) if stat.S_ISLNK(metadata.st_mode) else ""
        except OSError as exc:
            raise IsolationError(f"masked fragment cannot be read: {state.unit}") from exc
        if target != "/dev/null" or metadata.st_uid != required_owner_uid:
            raise IsolationError(f"mask is not a permanent root-owned mask: {state.unit}")
        return None
    if state.load_state == "loaded" and not os.path.lexists(exact_path):
        raise IsolationError(f"loaded unit fragment is missing: {state.unit}")
    if state.load_state != "loaded" or state.fragment_path != os.fspath(exact_path):
        raise IsolationError(f"unit is loaded outside the reviewed local path: {state.unit}")
    return _regular_fragment_record(
        state.unit,
        exact_path,
        required_owner_uid=required_owner_uid,
    )


def _generic_fragment_record(
    state: UnitState,
    *,
    unit_dir: Path,
    required_owner_uid: int,
) -> FragmentRecord | None:
    path = unit_dir / state.unit
    if state.need_daemon_reload != "no" or state.drop_in_paths:
        raise IsolationError(f"generic template metadata is not stable: {state.unit}")
    if state.load_state == "not-found":
        if state.fragment_path or os.path.lexists(path):
            raise IsolationError(f"unloaded generic template exists: {state.unit}")
        return None
    if not os.path.lexists(path):
        raise IsolationError(f"generic template fragment is missing: {state.unit}")
    if state.fragment_path not in {"", os.fspath(path)}:
        raise IsolationError(f"generic template is outside the reviewed local path: {state.unit}")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IsolationError(f"generic template cannot be inspected: {state.unit}") from exc
    if metadata.st_uid != required_owner_uid:
        raise IsolationError(f"generic template is not root-owned: {state.unit}")
    if stat.S_ISLNK(metadata.st_mode):
        try:
            target = os.readlink(path)
        except OSError as exc:
            raise IsolationError(
                f"generic template symlink cannot be inspected: {state.unit}"
            ) from exc
        if target != "/dev/null":
            raise IsolationError(f"generic template has an unexpected symlink: {state.unit}")
        return FragmentRecord(
            unit=state.unit,
            source=os.fspath(path),
            sha256=hashlib.sha256(b"symlink:/dev/null").hexdigest(),
            size=metadata.st_size,
            mode=stat.S_IMODE(metadata.st_mode),
            uid=metadata.st_uid,
            gid=metadata.st_gid,
            kind="symlink",
        )
    return _regular_fragment_record(
        state.unit,
        path,
        required_owner_uid=required_owner_uid,
    )


def _is_permanently_masked_and_inactive(state: UnitState) -> bool:
    return bool(
        state.load_state == "masked"
        and state.unit_file_state == "masked"
        and state.active_state == "inactive"
        and state.sub_state == "dead"
        and state.main_pid == 0
        and state.control_pid == 0
        and state.need_daemon_reload == "no"
    )


def _is_absent_and_inactive(state: UnitState) -> bool:
    return bool(
        state.load_state == "not-found"
        and state.unit_file_state == ""
        and state.active_state == "inactive"
        and state.sub_state == "dead"
        and state.main_pid == 0
        and state.control_pid == 0
        and state.need_daemon_reload == "no"
    )


def _inspect_non_denied_inventory(
    inventory: frozenset[str],
    *,
    runner: Runner,
    reject_unknown: bool = True,
) -> tuple[UnitState, ...]:
    unknown = tuple(sorted(inventory - _known_units()))
    if unknown and reject_unknown:
        raise IsolationError(
            "unknown QUANTOS units block isolation: " + ", ".join(unknown)
        )
    units = tuple(sorted(inventory & (_safe_concrete_units() | SUPPORT_UNITS)))
    states = tuple(inspect_unit(unit, runner=runner) for unit in units)
    for state in states:
        if state.need_daemon_reload != "no":
            raise IsolationError(
                f"non-denied unit has pending daemon reload metadata: {state.unit}"
            )
        if (
            state.unit in _safe_concrete_units()
            and state.fragment_path
            and Path(state.fragment_path).name in GENERIC_UNITS
        ):
            raise IsolationError(
                f"safe unit depends on a generic template: {state.unit}"
            )
    return states


def _find_exact_enablement_links(
    *,
    roots: Iterable[Path],
) -> tuple[str, ...]:
    units = denied_unit_names()
    found: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            entries = tuple(root.iterdir())
        except OSError as exc:
            raise IsolationError(f"cannot inspect systemd enablement root: {root}") from exc
        for entry in entries:
            if not entry.name.endswith((".wants", ".requires")):
                continue
            try:
                metadata = entry.lstat()
            except OSError as exc:
                raise IsolationError(
                    f"cannot inspect systemd enablement directory: {entry}"
                ) from exc
            if not stat.S_ISDIR(metadata.st_mode):
                raise IsolationError(f"systemd enablement path is not a directory: {entry}")
            for unit in units:
                candidate = entry / unit
                if os.path.lexists(candidate):
                    found.append(os.fspath(candidate))
    return tuple(sorted(found))


def build_plan(
    *,
    runner: Runner = subprocess.run,
    unit_dir: Path = SYSTEMD_UNIT_DIR,
    required_owner_uid: int = 0,
    enablement_roots: Iterable[Path] | None = None,
) -> HostPlan:
    """Inspect the exact policy surface without mutating the host."""

    inventory = _unit_inventory(runner=runner)
    if enablement_roots is None:
        roots = SYSTEMD_ENABLEMENT_ROOTS if unit_dir == SYSTEMD_UNIT_DIR else (unit_dir,)
    else:
        roots = tuple(enablement_roots)
    enablement_links = _find_exact_enablement_links(roots=roots)
    unknown = tuple(sorted(inventory - _known_units()))
    non_denied_states = _inspect_non_denied_inventory(
        inventory,
        runner=runner,
        reject_unknown=False,
    )
    states = tuple(inspect_unit(unit, runner=runner) for unit in denied_unit_names())
    fragments = tuple(
        record
        for state in states
        if (
            record := _validate_denied_state(
                state,
                unit_dir=unit_dir,
                required_owner_uid=required_owner_uid,
            )
        )
        is not None
    )
    generic_states = tuple(
        inspect_unit(unit, runner=runner)
        for unit in GENERIC_UNITS
        if unit in inventory or os.path.lexists(unit_dir / unit)
    )
    generic_fragments = tuple(
        record
        for state in generic_states
        if (
            record := _generic_fragment_record(
                state,
                unit_dir=unit_dir,
                required_owner_uid=required_owner_uid,
            )
        )
        is not None
    )
    states_safe = all(
        _is_permanently_masked_and_inactive(state)
        or (
            _is_absent_and_inactive(state)
            and state.unit not in inventory
        )
        for state in states
    )
    generic_absent = not generic_states and not generic_fragments
    isolated = bool(
        states_safe
        and generic_absent
        and not unknown
        and not enablement_links
    )
    if unknown:
        verdict = "BLOCKED_UNKNOWN_UNITS"
    elif isolated:
        verdict = "ALREADY_ISOLATED"
    else:
        verdict = "NEEDS_APPLY"
    return HostPlan(
        policy_sha256=POLICY_SHA256,
        denied_modules=len(DENIED_TASK_MODULE_PAIRS),
        denied_units=len(states),
        active_denied_timers=sum(
            state.unit.endswith(".timer")
            and state.active_state in {"active", "activating", "reloading"}
            for state in states
        ),
        active_denied_services=sum(
            state.unit.endswith(".service")
            and state.active_state in {"active", "activating", "reloading"}
            for state in states
        ),
        startable_denied_services=sum(
            state.unit.endswith(".service")
            and not (
                _is_permanently_masked_and_inactive(state)
                or (
                    _is_absent_and_inactive(state)
                    and state.unit not in inventory
                )
            )
            for state in states
        ),
        regular_fragments_to_quarantine=len(fragments),
        generic_templates_to_quarantine=len(generic_fragments),
        reviewed_non_denied_units=len(non_denied_states),
        enablement_links=enablement_links,
        unknown_units=unknown,
        isolated=isolated,
        verdict=verdict,
        states=states,
        fragments=fragments,
        generic_fragments=generic_fragments,
    )


def _assert_trusted_installed_script(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IsolationError("installed helper cannot be inspected") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
        or not metadata.st_mode & 0o100
    ):
        raise IsolationError("apply requires a root-owned, non-writable executable copy")
    current = path.parent
    while current != current.parent:
        try:
            parent_metadata = current.lstat()
        except OSError as exc:
            raise IsolationError("installed helper parent cannot be inspected") from exc
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != 0
            or parent_metadata.st_mode & 0o022
        ):
            raise IsolationError("installed helper has an untrusted parent directory")
        current = current.parent


def _assert_trusted_directory_chain(path: Path, *, required_owner_uid: int = 0) -> None:
    current = path
    while True:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise IsolationError(f"trusted directory is missing: {current}") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != required_owner_uid
            or metadata.st_mode & 0o022
        ):
            raise IsolationError(f"directory chain is writable or untrusted: {current}")
        if current == current.parent:
            break
        current = current.parent


@contextmanager
def _exclusive_apply_lock(path: Path = APPLY_LOCK):
    _assert_trusted_directory_chain(path.parent)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise IsolationError("cannot open the privileged isolation lock") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise IsolationError("the privileged isolation lock is not root-private")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IsolationError("another L2 isolation apply is already running") from exc
        except OSError as exc:
            raise IsolationError("cannot lock the privileged isolation transaction") from exc
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _ensure_private_directory(path: Path, *, required_owner_uid: int = 0) -> None:
    try:
        exists = path.exists()
        metadata = path.lstat() if exists else None
    except OSError as exc:
        raise IsolationError(f"cannot inspect quarantine directory: {path}") from exc
    if metadata is not None:
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != required_owner_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise IsolationError(f"quarantine directory is not root-private: {path}")
        return
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise IsolationError(f"cannot create quarantine directory: {path}") from exc


def _prepare_quarantine(
    root: Path,
    *,
    unit_dir: Path,
    required_owner_uid: int = 0,
) -> Path:
    parent = root.parent
    grandparent = parent.parent
    if grandparent != Path("/var/lib"):
        # Internal tests may inject a temporary root; production remains fixed.
        try:
            grandparent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise IsolationError("cannot prepare injected quarantine parent") from exc
    _ensure_private_directory(parent, required_owner_uid=required_owner_uid)
    _ensure_private_directory(root, required_owner_uid=required_owner_uid)
    try:
        if root.stat().st_dev != unit_dir.stat().st_dev:
            raise IsolationError(
                "quarantine and systemd unit paths are on different filesystems"
            )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        transaction = root / f"{stamp}-{os.getpid()}-{POLICY_SHA256[:12]}"
        transaction.mkdir(mode=0o700)
        (transaction / "units").mkdir(mode=0o700)
    except IsolationError:
        raise
    except OSError as exc:
        raise IsolationError("cannot create isolation transaction") from exc
    return transaction


def _write_receipt(path: Path, document: Mapping[str, object]) -> None:
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    payload = json.dumps(document, sort_keys=True, indent=2) + "\n"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise IsolationError(f"isolation receipt write failed: {path}") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise IsolationError(f"directory durability sync failed: {path}") from exc


def _same_fragment(record: FragmentRecord) -> bool:
    path = Path(record.source)
    try:
        metadata = path.lstat()
    except OSError:
        return False
    try:
        if record.kind == "symlink":
            return bool(
                stat.S_ISLNK(metadata.st_mode)
                and os.readlink(path) == "/dev/null"
                and metadata.st_uid == record.uid
            )
        return bool(
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_size == record.size
            and stat.S_IMODE(metadata.st_mode) == record.mode
            and metadata.st_uid == record.uid
            and metadata.st_gid == record.gid
            and _sha256_file(path) == record.sha256
        )
    except OSError:
        return False


def _stored_fragment_matches(record: FragmentRecord, path: Path) -> bool:
    stored = FragmentRecord(
        **{
            **asdict(record),
            "source": os.fspath(path),
        }
    )
    return _same_fragment(stored)


def _json_without_duplicate_keys(raw: str) -> object:
    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for name, value in pairs:
            if name in document:
                raise IsolationError("isolation receipt contains duplicate keys")
            document[name] = value
        return document

    try:
        return json.loads(raw, object_pairs_hook=pairs_hook)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise IsolationError("isolation receipt is not valid JSON") from exc


def _receipt_fragment(
    item: object,
    *,
    transaction: Path,
    unit_dir: Path,
    required_owner_uid: int,
) -> tuple[FragmentRecord, Path]:
    expected = {
        "unit",
        "source",
        "sha256",
        "size",
        "mode",
        "uid",
        "gid",
        "kind",
        "stored",
    }
    if not isinstance(item, dict) or set(item) != expected:
        raise IsolationError("isolation receipt has an invalid fragment record")
    unit = item["unit"]
    allowed = set(denied_unit_names()) | set(GENERIC_UNITS)
    if not isinstance(unit, str) or unit not in allowed:
        raise IsolationError("isolation receipt names an unauthorized unit")
    source = unit_dir / unit
    stored = transaction / "units" / unit
    if item["source"] != os.fspath(source) or item["stored"] != os.fspath(stored):
        raise IsolationError("isolation receipt fragment path is outside the transaction")
    integer_fields = ("size", "mode", "uid", "gid")
    if any(
        isinstance(item[name], bool) or not isinstance(item[name], int)
        for name in integer_fields
    ):
        raise IsolationError("isolation receipt fragment metadata is invalid")
    if item["uid"] != required_owner_uid:
        raise IsolationError("isolation receipt fragment owner is invalid")
    if (
        not isinstance(item["sha256"], str)
        or len(item["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in item["sha256"])
        or not isinstance(item["kind"], str)
        or item["kind"] not in {"regular", "symlink"}
    ):
        raise IsolationError("isolation receipt fragment digest is invalid")
    record = FragmentRecord(
        unit=unit,
        source=os.fspath(source),
        sha256=item["sha256"],
        size=item["size"],
        mode=item["mode"],
        uid=item["uid"],
        gid=item["gid"],
        kind=item["kind"],
    )
    return record, stored


def _resume_in_progress_receipts(
    *,
    runner: Runner = subprocess.run,
    unit_dir: Path = SYSTEMD_UNIT_DIR,
    quarantine_root: Path = QUARANTINE_ROOT,
    required_owner_uid: int = 0,
) -> tuple[Path, ...]:
    """Finish only moves bound by a trusted root-only IN_PROGRESS receipt."""

    inventory = _unit_inventory(runner=runner)
    _inspect_non_denied_inventory(inventory, runner=runner)
    if not quarantine_root.exists():
        return ()
    _ensure_private_directory(
        quarantine_root,
        required_owner_uid=required_owner_uid,
    )
    recovered: list[Path] = []
    try:
        transactions = sorted(quarantine_root.iterdir())
    except OSError as exc:
        raise IsolationError("cannot enumerate prior isolation receipts") from exc
    for transaction in transactions:
        try:
            metadata = transaction.lstat()
            units_metadata = (transaction / "units").lstat()
        except OSError as exc:
            raise IsolationError(
                "quarantine contains an incomplete transaction"
            ) from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != required_owner_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise IsolationError("quarantine contains an untrusted transaction")
        if (
            not stat.S_ISDIR(units_metadata.st_mode)
            or units_metadata.st_uid != required_owner_uid
            or stat.S_IMODE(units_metadata.st_mode) != 0o700
        ):
            raise IsolationError("quarantine transaction unit directory is untrusted")
        receipt_path = transaction / "receipt.json"
        try:
            receipt_metadata = receipt_path.lstat()
        except FileNotFoundError:
            try:
                transaction_entries = {entry.name for entry in transaction.iterdir()}
                unit_entries = tuple((transaction / "units").iterdir())
            except OSError as exc:
                raise IsolationError(
                    "cannot inspect an incomplete quarantine transaction"
                ) from exc
            if transaction_entries == {"units"} and not unit_entries:
                # A crash before the first atomic receipt publication cannot
                # have moved a unit. Retain the empty root-only directory for audit.
                continue
            raise IsolationError("quarantine transaction is missing its receipt")
        except OSError as exc:
            raise IsolationError("cannot inspect a prior isolation receipt") from exc
        if (
            not stat.S_ISREG(receipt_metadata.st_mode)
            or receipt_metadata.st_uid != required_owner_uid
            or stat.S_IMODE(receipt_metadata.st_mode) != 0o600
        ):
            raise IsolationError("quarantine contains an untrusted receipt")
        try:
            document = _json_without_duplicate_keys(
                receipt_path.read_text(encoding="utf-8")
            )
        except OSError as exc:
            raise IsolationError("cannot read a prior isolation receipt") from exc
        if not isinstance(document, dict):
            raise IsolationError("isolation receipt root is invalid")
        if document.get("status") not in {"IN_PROGRESS", "PARTIAL_FAILURE"}:
            continue
        if (
            document.get("schema_version")
            != "quantos.systemd_isolation_receipt.v1"
            or document.get("policy_sha256") != POLICY_SHA256
        ):
            raise IsolationError("in-progress receipt policy is invalid")
        planned = document.get("planned_fragments")
        if not isinstance(planned, list):
            raise IsolationError("in-progress receipt has no planned fragment list")
        parsed = tuple(
            _receipt_fragment(
                item,
                transaction=transaction,
                unit_dir=unit_dir,
                required_owner_uid=required_owner_uid,
            )
            for item in planned
        )
        if len({record.unit for record, _stored in parsed}) != len(parsed):
            raise IsolationError("in-progress receipt repeats a unit")

        errors: list[str] = []

        def best_effort(arguments: Sequence[str]) -> None:
            try:
                _run_systemctl(arguments, runner=runner, timeout=120)
            except IsolationError as exc:
                errors.append(str(exc))

        concrete = tuple(
            record.unit
            for record, _stored in parsed
            if record.unit in denied_unit_names()
        )
        timers = tuple(unit for unit in concrete if unit.endswith(".timer"))
        services = tuple(unit for unit in concrete if unit.endswith(".service"))
        if timers:
            best_effort(("stop", *timers))
            best_effort(("disable", "--no-reload", *timers))
        if services:
            best_effort(("stop", *services))

        moved: list[dict[str, object]] = []
        for record, stored in parsed:
            source = Path(record.source)
            source_present = os.path.lexists(source)
            stored_present = os.path.lexists(stored)
            try:
                source_is_mask = bool(
                    source_present
                    and source.is_symlink()
                    and os.readlink(source) == "/dev/null"
                )
            except OSError as exc:
                raise IsolationError(
                    f"cannot inspect recovery source for {record.unit}"
                ) from exc
            if stored_present and _stored_fragment_matches(record, stored):
                if source_present and not source_is_mask:
                    raise IsolationError(
                        f"recovery found both source and stored fragment: {record.unit}"
                    )
            elif not stored_present and _same_fragment(record):
                try:
                    os.replace(source, stored)
                    _fsync_directory(stored.parent)
                    _fsync_directory(unit_dir)
                except (OSError, IsolationError) as exc:
                    raise IsolationError(
                        f"cannot resume fragment move for {record.unit}"
                    ) from exc
                if not _stored_fragment_matches(record, stored):
                    raise IsolationError(
                        f"resumed fragment digest mismatch: {record.unit}"
                    )
            else:
                raise IsolationError(
                    f"recovery fragment state is inconsistent: {record.unit}"
                )
            moved.append({**asdict(record), "stored": os.fspath(stored)})
            if record.unit in denied_unit_names() and not source_is_mask:
                best_effort(("mask", record.unit))
        best_effort(("daemon-reload",))
        if errors:
            raise IsolationError(
                "in-progress isolation recovery did not complete: " + "; ".join(errors)
            )
        document.update(
            {
                "status": "RECOVERED_FOR_RETRY",
                "recovered_at_utc": datetime.now(timezone.utc).isoformat(),
                "moved_fragments": moved,
                "prior_errors": document.get("errors", []),
                "errors": [],
            }
        )
        _write_receipt(receipt_path, document)
        recovered.append(receipt_path)
    return tuple(recovered)


def apply_plan(
    plan: HostPlan,
    *,
    runner: Runner = subprocess.run,
    unit_dir: Path = SYSTEMD_UNIT_DIR,
    quarantine_root: Path = QUARANTINE_ROOT,
    required_owner_uid: int = 0,
) -> tuple[HostPlan, Path | None, tuple[str, ...]]:
    """Apply a preflighted plan. Failures never restore a denied unit."""

    if plan.policy_sha256 != POLICY_SHA256:
        raise IsolationError("plan policy digest does not match this helper")
    if plan.unknown_units:
        raise IsolationError("unknown QUANTOS units block privileged apply")
    if plan.isolated:
        return plan, None, ()

    try:
        helper_sha256 = _sha256_file(Path(__file__))
    except OSError as exc:
        raise IsolationError("cannot hash the installed isolation helper") from exc
    transaction = _prepare_quarantine(
        quarantine_root,
        unit_dir=unit_dir,
        required_owner_uid=required_owner_uid,
    )
    receipt_path = transaction / "receipt.json"
    started = datetime.now(timezone.utc).isoformat()
    records = (*plan.fragments, *plan.generic_fragments)
    planned = [
        {
            **asdict(record),
            "stored": os.fspath(transaction / "units" / record.unit),
        }
        for record in records
    ]
    receipt: dict[str, object] = {
        "schema_version": "quantos.systemd_isolation_receipt.v1",
        "status": "IN_PROGRESS",
        "policy_sha256": POLICY_SHA256,
        "helper_sha256": helper_sha256,
        "started_at_utc": started,
        "pre_state": plan.to_dict(),
        "planned_fragments": planned,
        "moved_fragments": [],
        "errors": [],
        "recovery": (
            "Controlled review only: unmask exact names, verify retained SHA-256, "
            "restore root-owned fragments, daemon-reload, and keep timers disabled."
        ),
    }
    _write_receipt(receipt_path, receipt)
    _fsync_directory(transaction)
    errors: list[str] = []

    def mutate(arguments: Sequence[str], *, timeout: int = 120) -> bool:
        try:
            _run_systemctl(arguments, runner=runner, timeout=timeout)
        except IsolationError as exc:
            errors.append(str(exc))
            return False
        return True

    pre_state = {state.unit: state for state in plan.states}
    linked_units = {Path(path).name for path in plan.enablement_links}
    timer_stop_targets = tuple(
        unit
        for unit in denied_unit_names()
        if unit.endswith(".timer")
        and pre_state[unit].load_state != "not-found"
        and not _is_permanently_masked_and_inactive(pre_state[unit])
    )
    timer_disable_targets = tuple(
        unit
        for unit in denied_unit_names()
        if unit.endswith(".timer")
        and (
            not (
                _is_absent_and_inactive(pre_state[unit])
                or _is_permanently_masked_and_inactive(pre_state[unit])
            )
            or unit in linked_units
        )
    )
    service_targets = tuple(
        unit
        for unit in denied_unit_names()
        if unit.endswith(".service")
        and pre_state[unit].load_state != "not-found"
        and not (
            _is_permanently_masked_and_inactive(pre_state[unit])
        )
    )
    service_disable_targets = tuple(
        unit
        for unit in denied_unit_names()
        if unit.endswith(".service") and unit in linked_units
    )
    moved: list[dict[str, object]] = []
    successfully_masked: set[str] = {
        unit
        for unit, state in pre_state.items()
        if state.load_state == "masked" and state.unit_file_state == "masked"
    }
    unexpected: BaseException | None = None
    try:
        # One exact-name batch closes the timer firing window before any slower
        # per-unit archival work begins. No action argument is a glob.
        if timer_stop_targets:
            mutate(("stop", *timer_stop_targets))
        if timer_disable_targets:
            mutate(("disable", "--no-reload", *timer_disable_targets))
        if service_targets:
            mutate(("stop", *service_targets))
        if service_disable_targets:
            mutate(("disable", "--no-reload", *service_disable_targets))

        # Re-observe before removing definitions. Retry an exact stop for any
        # target that survived the batch, then retain the breach if it persists.
        for unit in denied_unit_names():
            try:
                state = inspect_unit(unit, runner=runner)
            except IsolationError as exc:
                errors.append(str(exc))
                continue
            if state.active_state in {"active", "activating", "reloading"}:
                mutate(("stop", unit))
                try:
                    state = inspect_unit(unit, runner=runner)
                except IsolationError as exc:
                    errors.append(str(exc))
                    continue
            if (
                state.active_state != "inactive"
                or state.sub_state != "dead"
                or state.main_pid != 0
                or state.control_pid != 0
            ):
                errors.append(f"denied unit did not quiesce: {unit}")

        for record in records:
            source = Path(record.source)
            target = transaction / "units" / record.unit
            if not _same_fragment(record):
                errors.append(f"fragment changed after preflight: {record.unit}")
                continue
            try:
                os.replace(source, target)
                _fsync_directory(target.parent)
                _fsync_directory(unit_dir)
            except (OSError, IsolationError) as exc:
                errors.append(f"failed to quarantine {record.unit}: {exc}")
                continue
            if not _stored_fragment_matches(record, target):
                errors.append(f"quarantined fragment hash mismatch: {record.unit}")
                continue
            moved.append({**asdict(record), "stored": os.fspath(target)})
            if record.unit in denied_unit_names() and mutate(("mask", record.unit)):
                successfully_masked.add(record.unit)

        # Also mask units that were already absent. This closes future manual,
        # package, and template activation under the frozen deny policy.
        for unit in denied_unit_names():
            if unit not in successfully_masked:
                mutate(("mask", unit))
    except BaseException as exc:  # pragma: no cover - process-level recovery path
        unexpected = exc
        errors.append(f"unexpected apply interruption: {type(exc).__name__}")
    finally:
        # Normal exceptions always leave systemd synchronized. SIGKILL/power
        # loss is resumed from the pre-written root-only receipt on the next apply.
        mutate(("daemon-reload",))

    if unexpected is not None:
        receipt.update(
            {
                "status": "PARTIAL_FAILURE",
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                "moved_fragments": moved,
                "errors": errors,
            }
        )
        _write_receipt(receipt_path, receipt)
        raise IsolationError("isolation apply was interrupted; receipt retained") from unexpected

    try:
        post_plan = build_plan(
            runner=runner,
            unit_dir=unit_dir,
            required_owner_uid=required_owner_uid,
        )
    except IsolationError as exc:
        errors.append(str(exc))
        post_plan = plan
    if not post_plan.isolated:
        errors.append("post-state is not fully isolated")
    receipt.update(
        {
            "status": "COMPLETE" if not errors else "PARTIAL_FAILURE",
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "post_state": post_plan.to_dict(),
            "moved_fragments": moved,
            "errors": errors,
        }
    )
    _write_receipt(receipt_path, receipt)
    return post_plan, receipt_path, tuple(errors)


def _render_plan(plan: HostPlan, *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(plan.to_dict(), sort_keys=True, indent=2)
    unknown = ", ".join(plan.unknown_units) if plan.unknown_units else "none"
    return "\n".join(
        (
            "QUANTOS L2 systemd isolation plan",
            f"verdict: {plan.verdict}",
            f"policy_sha256: {plan.policy_sha256}",
            f"denied_modules: {plan.denied_modules}",
            f"denied_units: {plan.denied_units}",
            f"active_denied_timers: {plan.active_denied_timers}",
            f"active_denied_services: {plan.active_denied_services}",
            f"startable_denied_services: {plan.startable_denied_services}",
            (
                "regular_fragments_to_quarantine: "
                f"{plan.regular_fragments_to_quarantine}"
            ),
            (
                "generic_templates_to_quarantine: "
                f"{plan.generic_templates_to_quarantine}"
            ),
            f"reviewed_non_denied_units: {plan.reviewed_non_denied_units}",
            f"enablement_links: {len(plan.enablement_links)}",
            f"unknown_units: {unknown}",
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or apply exact QUANTOS L2 denied-unit isolation."
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-policy-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.apply:
        if os.geteuid() != 0:
            print("error: --apply requires root", file=sys.stderr)
            return 2
        if arguments.confirm_policy_sha256 != POLICY_SHA256:
            print("error: exact --confirm-policy-sha256 is required", file=sys.stderr)
            return 2
        if not sys.flags.isolated:
            print(
                "error: --apply must run through the installed isolated shebang",
                file=sys.stderr,
            )
            return 2
        try:
            _assert_trusted_installed_script(Path(__file__))
            _assert_trusted_directory_chain(SYSTEMD_UNIT_DIR)
        except IsolationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    elif arguments.confirm_policy_sha256 is not None:
        print("error: policy confirmation is valid only with --apply", file=sys.stderr)
        return 2

    if not arguments.apply:
        try:
            plan = build_plan()
        except IsolationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(_render_plan(plan, output_format=arguments.format))
        return 0 if plan.isolated else 3

    os.umask(0o077)
    recovered_receipts: tuple[Path, ...] = ()
    try:
        with _exclusive_apply_lock():
            recovered_receipts = _resume_in_progress_receipts()
            # The plan is always rebuilt while the exclusive lock is held.
            plan = build_plan()
            post_plan, receipt, errors = apply_plan(plan)
    except IsolationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if arguments.format == "json":
        print(
            json.dumps(
                {
                    "plan": post_plan.to_dict(),
                    "receipt": None if receipt is None else os.fspath(receipt),
                    "recovered_receipts": [
                        os.fspath(path) for path in recovered_receipts
                    ],
                    "errors": list(errors),
                },
                sort_keys=True,
                indent=2,
            )
        )
    else:
        print(_render_plan(post_plan, output_format="text"))
        for recovered in recovered_receipts:
            print(f"recovered_receipt: {recovered}")
        print(f"receipt: {receipt if receipt is not None else 'not-created'}")
        if errors:
            print("errors:")
            for error in errors:
                print(f"- {error}")
    return 0 if post_plan.isolated and not errors else 3


if __name__ == "__main__":
    raise SystemExit(main())
