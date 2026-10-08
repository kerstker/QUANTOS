"""Normalization never hides foreign configuration or missing effective gates."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import demo_maintenance_inhibit as inhibit
from tools import demo_maintenance_services as services


@pytest.fixture
def definitions(tmp_path, monkeypatch):
    unit = services.UNITS[0]
    fragment = tmp_path / unit
    fragment.write_text("[Service]\nExecStart=/usr/bin/true\n")
    fragment.chmod(0o600)
    own = tmp_path / (unit + ".d") / inhibit.NAME
    own.parent.mkdir()
    own.write_bytes(inhibit._payload("a" * 64))
    monkeypatch.setattr(inhibit, "SYSTEM", tmp_path)
    original = Path.stat
    def metadata(path, *a, **kw):
        info = original(path, *a, **kw)
        values = list(info)
        values[4] = 0
        return os.stat_result(values)
    monkeypatch.setattr(Path, "stat", metadata)
    # Custody of the own file is tested with real temporary files separately.
    monkeypatch.setattr(inhibit, "_existing", lambda path, raw: path.exists() and path.read_bytes() == raw)
    monkeypatch.setattr(inhibit, "verify_loaded", lambda *a: None)
    state = {"dropins": "", "reload": "no"}
    def show(*args, **kwargs):
        return SimpleNamespace(stdout="ActiveState=inactive\nLoadState=loaded\nFragmentPath=" + str(fragment)
                               + "\nDropInPaths=" + state["dropins"] + "\nNeedDaemonReload=" + state["reload"] + "\n")
    monkeypatch.setattr(services.subprocess, "run", show)
    return unit, fragment, own, state


def test_only_own_addition_normalized(definitions):
    unit, fragment, own, state = definitions
    original = services.SystemdBackend().inspect(unit)
    state["dropins"] = str(own)
    inhibited = services.SystemdBackend(inhibitor_operation="a" * 64)
    assert inhibited.inspect(unit) == original
    other = own.parent / "other.conf"
    other.write_text("[Service]\nTimeoutStopSec=45\n")
    other.chmod(0o600)
    state["dropins"] += " " + str(other)
    assert inhibited.inspect(unit)["fingerprint"] != original["fingerprint"]
    fragment.write_text("[Service]\nExecStart=/usr/bin/false\n")
    assert inhibited.inspect(unit)["fingerprint"] != original["fingerprint"]


def test_unloaded_inhibitor_cannot_pause(definitions):
    unit, _, _, _ = definitions
    with pytest.raises(ValueError, match="INHIBITOR_NOT_LOADED"):
        services.SystemdBackend(inhibitor_operation="a" * 64).inspect(unit)


def test_missing_file_is_only_tolerated_during_pre_reload_cleanup(definitions):
    unit, _, own, state = definitions
    original = services.SystemdBackend().inspect(unit)
    state.update(dropins=str(own), reload="yes")
    own.unlink()
    with pytest.raises(ValueError, match="UNIT_CONFIGURATION_UNRESOLVED"):
        services.SystemdBackend(inhibitor_operation="a" * 64).inspect(unit)
    cleanup = services.SystemdBackend(inhibitor_operation="a" * 64, require_inhibition=False)
    assert cleanup.inspect(unit) == original
    with pytest.raises(ValueError, match="UNIT_CONFIGURATION_UNRESOLVED"):
        services.SystemdBackend().inspect(unit)


def test_cleanup_backend_cannot_start_any_service():
    cleanup = services.SystemdBackend(inhibitor_operation="a" * 64, require_inhibition=False)
    with pytest.raises(ValueError, match="INHIBITOR_BACKEND_CANNOT_START"):
        cleanup.change("start", services.UNITS[0])
