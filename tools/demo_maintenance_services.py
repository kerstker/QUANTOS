"""Scoped Demo service pause/restore primitives, without an operational CLI.

This does not establish namespace quiescence or authorize archive operations.
The real backend requires the existing root operator; research cannot elevate.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from time import monotonic as _settle_clock, sleep as _settle_sleep

from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.demo_retention_transaction import directory, read, sync, write_new, require

_STEMS = (
    "executor-stream",
    "executor-observe",
    "executor-publish",
    "executor-origin",
    "executor-origin-BTCUSDC",
    "executor-origin-BTCUSDT",
    "checkpoint-executor-capture",
    "checkpoint-executor-seal",
    "checkpoint-issuer-capture",
    "checkpoint-issuer-seal",
    "checkpoint-controller-capture",
    "checkpoint-controller-seal",
    "checkpoint-research-capture",
    "checkpoint-research-seal",
    "checkpoint-monitor",
    "view-resident",
    "view-issuer",
    "view-oms",
    "view-functional-cycle",
    "view-cash-chain",
    "view-portfolio-cycle",
    "view-portfolio-cash",
)
_TIMERS = tuple(s for s in _STEMS if s not in ("executor-stream", "executor-observe"))
UNITS = tuple("quantos-demo-" + s + ".service" for s in _STEMS) + tuple("quantos-demo-" + s + ".timer" for s in _TIMERS)
FEEDBACK_UNITS=('quantos-demo-feedback.service','quantos-demo-feedback.timer')
ALLOWED_UNITS=UNITS+FEEDBACK_UNITS
GOLD_UNITS=ALLOWED_UNITS+('quantos-gold-pilot.service','quantos-research-runner.service')
JOURNAL_SCOPES={
    'DEMO_SCOPED_SERVICE_MAINTENANCE_V1':UNITS,
    'DEMO_SCOPED_SERVICE_MAINTENANCE_V2':ALLOWED_UNITS,
    'GOLD_SCOPED_SERVICE_MAINTENANCE_V1':GOLD_UNITS,
}
LONG_RUNNING_UNITS={'quantos-demo-executor-stream.service','quantos-gold-pilot.service',
    'quantos-research-runner.service'}
AUTOMATION_PROFILE=Path('/etc/quantos/demo-observation/feedback-automation.json')


def validate_scope(units):
    units=tuple(units)
    require(len(units)==len(set(units)) and any(set(units)==set(scope)
        for scope in JOURNAL_SCOPES.values()), 'UNIT_SCOPE')
    return units


def configured_units():
    from live.execution.demo_authority import _read_root_owned_json
    try:
        body=_read_root_owned_json(AUTOMATION_PROFILE)
    except FileNotFoundError:
        return UNITS
    value=load_json_without_duplicate_keys(body)
    require(type(value) is dict and set(value)=={'schema','source_commit','units'} and
        value['schema']=='DEMO_FEEDBACK_AUTOMATION_V1' and value['units']==list(FEEDBACK_UNITS) and
        type(value['source_commit']) is str and re.fullmatch('[a-f0-9]{40}',value['source_commit']),
        'FEEDBACK_AUTOMATION_PROFILE')
    return ALLOWED_UNITS


def scoped_units(backend):
    return validate_scope(getattr(backend,'units',UNITS))

_STABLE = {"active", "inactive", "failed"}
_PHASES = {"PREPARED", "PAUSING", "PAUSED", "RESTORING", "RESTORED"}
CGROUP_ROOT = Path("/sys/fs/cgroup")


class SystemdBackend:
    allowed_units=ALLOWED_UNITS
    @property
    def units(self):return configured_units()

    def __init__(self, *, inhibitor_operation=None, require_inhibition=True):
        self.inhibitor_operation = inhibitor_operation
        self.require_inhibition = require_inhibition

    def idle(self, name):
        require(name in self.allowed_units, "UNIT_SCOPE")

        def state():
            result = subprocess.run(
                ["/usr/bin/systemctl", "show", name, "--no-pager", "--property=LoadState,ActiveState,ControlGroup"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

        before = state()
        require(before.get("LoadState") == "loaded", "UNIT_CONFIGURATION_UNRESOLVED")
        if before.get("ActiveState") not in ("inactive", "failed"):
            return False
        require(not name.endswith(".service") or "ControlGroup" in before, "UNIT_CGROUP_UNRESOLVED")
        group = before.get("ControlGroup", "")
        if group:
            relative = Path(group)
            require(
                relative.is_absolute()
                and ".." not in relative.parts
                and relative.as_posix() == group
                and relative.name == name,
                "UNIT_CGROUP_PATH",
            )
            path = CGROUP_ROOT.joinpath(*relative.parts[1:]) / "cgroup.events"
            require(not any(p.is_symlink() for p in (path, *path.parents)), "UNIT_CGROUP_PATH")
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                raw = stream.read(4097)
            require(len(raw) <= 4096, "UNIT_CGROUP_EVENTS")
            rows = [line.split() for line in raw.decode("ascii").splitlines()]
            require(all(len(row) == 2 for row in rows), "UNIT_CGROUP_EVENTS")
            require(len({row[0] for row in rows}) == len(rows), "UNIT_CGROUP_EVENTS")
            values = dict(rows)
            require(values.get("populated") in ("0", "1"), "UNIT_CGROUP_EVENTS")
            if values["populated"] == "1":
                return False
        # A changed group or state makes this observation unusable.
        return state() == before

    def configuration(self, name, *, allow_absent=False):
        """Return the same checked configuration plus its normalized file hashes.

        Explicit absence is only usable for the fixed, newly introduced feedback
        pair. It never makes an absent original scoped unit restorable.
        """
        require(name in self.allowed_units, "UNIT_SCOPE")
        result = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                name,
                "--no-pager",
                "--property=ActiveState,LoadState,FragmentPath,DropInPaths,NeedDaemonReload",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if allow_absent and name in FEEDBACK_UNITS and values.get("LoadState") == "not-found":
            require(values.get("ActiveState") == "inactive" and values.get("FragmentPath") == "",
                    "UNIT_CONFIGURATION_UNRESOLVED")
            paths = values.get("DropInPaths", "").split()
            if paths:
                from tools import demo_maintenance_inhibit as inhibit
                require(self.inhibitor_operation is not None and
                        paths == [str(inhibit.dropin(name))] and
                        inhibit._existing(inhibit.dropin(name), inhibit._payload(self.inhibitor_operation)),
                        "UNIT_CONFIGURATION_UNRESOLVED")
            return {"exists": False, "active": "inactive", "fingerprint": None, "configuration": []}
        # A cleanup/pre-reload read may encounter this operation's partially
        # installed/removed drop-ins. It cannot be used as an inhibited backend
        # or to resume services: those paths still require a completed reload.
        reload_states = {"no", "yes"} if self.inhibitor_operation is not None and not self.require_inhibition else {"no"}
        require(values["LoadState"] == "loaded" and values["NeedDaemonReload"] in reload_states,
                "UNIT_CONFIGURATION_UNRESOLVED")
        paths = [values["FragmentPath"], *values["DropInPaths"].split()]
        configuration = []
        own = None
        if self.inhibitor_operation is not None:
            from tools import demo_maintenance_inhibit as inhibit
            own = inhibit.dropin(name)
            payload = inhibit._payload(self.inhibitor_operation)
            if self.require_inhibition:
                require(str(own) in paths[1:], "INHIBITOR_NOT_LOADED")
                inhibit.verify_loaded(name, self.inhibitor_operation)
        for value in paths:
            path = Path(value)
            if path == own:
                # Only this operation's exact, verified addition is normalized.
                # Cleanup may resume after removal but before daemon-reload.
                found = inhibit._existing(path, payload)
                require(found or not self.require_inhibition, "INHIBITOR_MISSING")
                continue
            require(path.is_absolute() and not path.is_symlink() and path.is_file(), "UNIT_FRAGMENT")
            info = path.stat()
            require(info.st_uid == 0 and not info.st_mode & 0o022, "UNIT_FRAGMENT_CUSTODY")
            # Hash only unit definitions, never EnvironmentFile/credential targets.
            configuration.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
        digest = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        return {"exists": True, "active": values["ActiveState"], "fingerprint": digest,
                "configuration": configuration}

    def inspect(self, name):
        value = self.configuration(name)
        return {key: value[key] for key in ("active", "fingerprint")}

    def change(self, action, name):
        require(name in self.allowed_units and action in ("start", "stop"), "UNIT_SCOPE")
        require(action != "start" or self.inhibitor_operation is None, "INHIBITOR_BACKEND_CANNOT_START")
        require(os.geteuid() == 0, "OPERATOR_ROOT_REQUIRED")
        subprocess.run(
            ["/usr/bin/systemctl", action, name],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
        )


class GoldSystemdBackend(SystemdBackend):
    """Explicit complete Gold service scope, never inferred from a legacy log."""
    allowed_units=GOLD_UNITS

    @property
    def units(self):
        return GOLD_UNITS


def inspect_journal(path):
    path = Path(path)
    directory(path.parent)
    document = load_json_without_duplicate_keys(read(path, 128 * 1024))
    require(
        document.get("schema") in JOURNAL_SCOPES
        and document.get("phase") in _PHASES
        and set(document["units"]) == set(JOURNAL_SCOPES[document['schema']]),
        "JOURNAL_SCOPE",
    )
    for value in document["units"].values():
        require(
            type(value) is dict
            and set(value) == {"active", "fingerprint"}
            and value["active"] in _STABLE
            and type(value["fingerprint"]) is str
            and re.fullmatch("[a-f0-9]{64}", value["fingerprint"]),
            "JOURNAL_UNIT",
        )
    return document


def _save(path, document):
    raw = json.dumps(document, sort_keys=True, indent=2).encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".maintenance-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
            sync(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def _locked(path):
    path = Path(path)
    directory(path.parent)
    lock = path.with_suffix(path.suffix + ".lock")
    require(read(lock, 1) == b"", "LOCK_CONTENT")
    with os.fdopen(os.open(lock, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield path, inspect_journal(path)


def _settled_observation(backend, name):
    """Wait for ordinary transitions; never normalize a changed configuration."""
    observed = backend.inspect(name)
    fingerprint = observed["fingerprint"]
    deadline = _settle_clock() + 10.0
    for attempt in range(21):
        require(observed["fingerprint"] == fingerprint, "CONFIGURATION_CHANGED")
        if observed["active"] in _STABLE:
            return observed
        require(observed["active"] in {"activating", "deactivating", "reloading"}, "UNIT_TRANSITIONING")
        remaining = deadline - _settle_clock()
        require(attempt < 20 and remaining > 0, "UNIT_TRANSITIONING")
        _settle_sleep(min(0.5, remaining))
        observed = backend.inspect(name)
    raise ValueError("UNIT_TRANSITIONING")  # Defensive: loop exits via return/require.


def _observations(document, backend):
    require(set(scoped_units(backend))==set(document["units"]),"UNIT_SCOPE_CHANGED")
    observations = {}
    for name, original in document["units"].items():
        observed = _settled_observation(backend, name)
        require(observed["fingerprint"] == original["fingerprint"], "CONFIGURATION_CHANGED")
        require(observed["active"] in _STABLE, "UNIT_TRANSITIONING")
        observations[name] = observed
    return observations


def prepare(path, backend):
    path = Path(path)
    directory(path.parent)
    require(not path.exists() and not path.is_symlink(), "JOURNAL_EXISTS")
    units = {name: _settled_observation(backend, name) for name in scoped_units(backend)}
    require(all(row["active"] in _STABLE for row in units.values()), "UNIT_TRANSITIONING")
    schema=next(name for name,scope in JOURNAL_SCOPES.items() if set(units)==set(scope))
    document = {"schema": schema, "phase": "PREPARED", "units": units}
    write_new(path.with_suffix(path.suffix + ".lock"), b"")
    write_new(path, json.dumps(document, sort_keys=True, indent=2).encode())
    inspect_journal(path)
    return {"status": "PREPARED", "quiescence_proven": False}


def pause(path, backend):
    with _locked(path) as (path, document):
        require(document["phase"] in ("PREPARED", "PAUSING", "PAUSED"), "PAUSE_PHASE")
        current = _observations(document, backend)
        if document["phase"] == "PREPARED":
            require(
                all(row["active"] == document["units"][name]["active"] for name, row in current.items()),
                "PREPARED_STATE_CHANGED",
            )
        if document["phase"] == "PAUSED":
            require(all(row["active"] in ("inactive", "failed") for row in current.values()), "PAUSE_LOST")
            require(all(backend.idle(name) for name in document["units"]), "UNIT_PROCESSES_REMAIN")
            return {"status": "SCOPED_SERVICES_PAUSED", "quiescence_proven": False}
        document["phase"] = "PAUSING"
        _save(path, document)
        # Disable triggers before stopping their in-flight services.
        for name in sorted(document["units"], key=lambda n: (not n.endswith(".timer"), n)):
            observed = _settled_observation(backend, name)
            require(observed["fingerprint"] == current[name]["fingerprint"], "CONFIGURATION_CHANGED")
            require(observed["active"] in _STABLE, "UNIT_TRANSITIONING")
            if observed["active"] == "active":
                backend.change("stop", name)
        after = _observations(document, backend)
        require(all(row["active"] in ("inactive", "failed") for row in after.values()), "PAUSE_INCOMPLETE")
        require(all(backend.idle(name) for name in document["units"]), "UNIT_PROCESSES_REMAIN")
        document["phase"] = "PAUSED"
        _save(path, document)
        return {"status": "SCOPED_SERVICES_PAUSED", "quiescence_proven": False}


def restore(path, backend):
    with _locked(path) as (path, document):
        if document["phase"] == "RESTORED":
            return {"status": "ALREADY_RESTORED", "quiescence_proven": False}
        if document["phase"] == "PREPARED":
            # No stop was attempted; restoring the old snapshot would undo
            # operator actions that happened after preparation.
            document["phase"] = "RESTORED"
            _save(path, document)
            return {"status": "NOT_PAUSED_NO_CHANGES", "quiescence_proven": False}
        _observations(document, backend)  # Check all configurations before starting anything.
        document["phase"] = "RESTORING"
        _save(path, document)
        for name in sorted(document["units"], key=lambda n: (n.endswith(".timer"), n)):
            if document["units"][name]["active"] != "active":
                continue
            observed = _settled_observation(backend, name)
            require(observed["fingerprint"] == document["units"][name]["fingerprint"], "CONFIGURATION_CHANGED")
            require(observed["active"] in _STABLE, "UNIT_TRANSITIONING")
            if observed["active"] != "active":
                backend.change("start", name)
            after = _settled_observation(backend, name)
            require(after["fingerprint"] == document["units"][name]["fingerprint"], "CONFIGURATION_CHANGED")
            require(
                after["active"] == "active"
                or name.endswith(".service")
                and name not in LONG_RUNNING_UNITS
                and after["active"] == "inactive",
                "RESTORE_INCOMPLETE",
            )
        document["phase"] = "RESTORED"
        _save(path, document)
        return {"status": "ORIGINAL_ACTIVE_UNITS_RESTORED", "quiescence_proven": False}
