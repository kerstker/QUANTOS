"""Maintenance resumes only previously running, explicitly scoped Demo jobs."""

import pytest
from tools import demo_maintenance_services as maintenance


class Backend:
    def __init__(self):
        self.states = {name: "inactive" for name in maintenance.UNITS}
        self.calls = []
        self.fail = None

    def inspect(self, name):
        return {"active": self.states[name], "fingerprint": "a" * 64}

    def idle(self, name):
        return self.states[name] in ("inactive", "failed")

    def change(self, action, name):
        self.calls.append((action, name))
        if self.fail == (action, name):
            raise RuntimeError("fixture failure")
        self.states[name] = "active" if action == "start" else "inactive"


def test_pause_and_restore_only_previously_active_units(tmp_path):
    backend = Backend()
    timer = "quantos-demo-executor-publish.timer"
    stream = "quantos-demo-executor-stream.service"
    backend.states[timer] = backend.states[stream] = "active"
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    maintenance.pause(path, backend)
    assert backend.calls == [("stop", timer), ("stop", stream)]
    maintenance.restore(path, backend)
    assert backend.calls[-2:] == [("start", stream), ("start", timer)]
    assert set(name for _, name in backend.calls) == {timer, stream}
    assert maintenance.inspect_journal(path)["phase"] == "RESTORED"


def test_partial_pause_failure_remains_recoverable(tmp_path):
    backend = Backend()
    timer = "quantos-demo-executor-publish.timer"
    stream = "quantos-demo-executor-stream.service"
    backend.states[timer] = backend.states[stream] = "active"
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    backend.fail = ("stop", stream)
    with pytest.raises(RuntimeError):
        maintenance.pause(path, backend)
    assert maintenance.inspect_journal(path)["phase"] == "PAUSING"
    backend.fail = None
    maintenance.restore(path, backend)
    assert backend.states[timer] == backend.states[stream] == "active"


def test_drifted_unit_configuration_never_restarted(tmp_path):
    backend = Backend()
    name = "quantos-demo-executor-stream.service"
    backend.states[name] = "active"
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    maintenance.pause(path, backend)
    backend.inspect = lambda name: {"active": "inactive", "fingerprint": "b" * 64}
    with pytest.raises(ValueError, match="CONFIGURATION_CHANGED"):
        maintenance.restore(path, backend)
    assert not any(action == "start" for action, _ in backend.calls)


def test_originally_failed_or_inactive_jobs_never_started(tmp_path):
    backend = Backend()
    backend.states["quantos-demo-executor-observe.service"] = "failed"
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    maintenance.pause(path, backend)
    maintenance.restore(path, backend)
    assert backend.calls == []


def test_scope_excludes_live_orders_and_other_account_monitors():
    assert all(name.startswith("quantos-demo-") for name in maintenance.UNITS)
    assert not any("gold" in name or "wallet" in name or "rf3" in name for name in maintenance.UNITS)
    backend = maintenance.SystemdBackend()
    with pytest.raises(ValueError, match="UNIT_SCOPE"):
        backend.change("start", "quantos-gold-account-monitor.service")


def test_transitioning_unit_prevents_any_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "_settle_sleep", lambda _: None)
    backend = Backend()
    backend.states["quantos-demo-executor-stream.service"] = "activating"
    with pytest.raises(ValueError, match="UNIT_TRANSITIONING"):
        maintenance.prepare(tmp_path / "journal.json", backend)
    assert backend.calls == [] and not (tmp_path / "journal.json").exists()


def test_service_restart_failure_keeps_timers_stopped(tmp_path):
    backend = Backend()
    timer = "quantos-demo-executor-publish.timer"
    stream = "quantos-demo-executor-stream.service"
    backend.states[timer] = backend.states[stream] = "active"
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    maintenance.pause(path, backend)
    backend.fail = ("start", stream)
    with pytest.raises(RuntimeError):
        maintenance.restore(path, backend)
    assert backend.states[timer] == "inactive"
    assert maintenance.inspect_journal(path)["phase"] == "RESTORING"
    backend.fail = None
    maintenance.restore(path, backend)
    assert backend.states[timer] == "active"


def test_changed_scope_journal_cannot_control_other_service(tmp_path):
    import json

    backend = Backend()
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    document = json.loads(path.read_bytes())
    document["units"]["arbitrary.service"] = {"active": "active", "fingerprint": "a" * 64}
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="JOURNAL_SCOPE"):
        maintenance.restore(path, backend)
    assert backend.calls == []


def test_unit_lock_contention_cannot_interleave_restore(tmp_path):
    backend = Backend()
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    with maintenance._locked(path):
        with pytest.raises(BlockingIOError):
            maintenance.restore(path, backend)
    assert backend.calls == []


def test_completed_restore_does_not_undo_later_operator_stop(tmp_path):
    backend = Backend()
    stream = "quantos-demo-executor-stream.service"
    backend.states[stream] = "active"
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    maintenance.pause(path, backend)
    maintenance.restore(path, backend)
    backend.states[stream] = "inactive"
    backend.calls.clear()
    assert maintenance.restore(path, backend)["status"] == "ALREADY_RESTORED"
    assert backend.calls == [] and backend.states[stream] == "inactive"


def test_lost_pause_is_reported_without_stopping_newly_started_work(tmp_path):
    backend = Backend()
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    maintenance.pause(path, backend)
    backend.states["quantos-demo-executor-stream.service"] = "active"
    with pytest.raises(ValueError, match="PAUSE_LOST"):
        maintenance.pause(path, backend)
    assert backend.calls == []


def test_restore_without_pause_preserves_external_stop(tmp_path):
    backend = Backend()
    stream = "quantos-demo-executor-stream.service"
    backend.states[stream] = "active"
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    backend.states[stream] = "inactive"
    assert maintenance.restore(path, backend)["status"] == "NOT_PAUSED_NO_CHANGES"
    assert backend.calls == [] and backend.states[stream] == "inactive"
    assert maintenance.inspect_journal(path)["phase"] == "RESTORED"
    assert maintenance.restore(path, backend)["status"] == "ALREADY_RESTORED"


@pytest.mark.parametrize("before,after", [("inactive", "active"), ("active", "inactive")])
def test_initial_pause_rejects_changed_running_state(tmp_path, before, after):
    backend = Backend()
    stream = "quantos-demo-executor-stream.service"
    backend.states[stream] = before
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    backend.states[stream] = after
    with pytest.raises(ValueError, match="PREPARED_STATE_CHANGED"):
        maintenance.pause(path, backend)
    assert backend.calls == []
    assert maintenance.inspect_journal(path)["phase"] == "PREPARED"


def test_inactive_service_with_remaining_children_cannot_complete_pause(tmp_path):
    backend = Backend()
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    backend.idle = lambda name: name != "quantos-demo-executor-observe.service"
    with pytest.raises(ValueError, match="UNIT_PROCESSES_REMAIN"):
        maintenance.pause(path, backend)
    assert maintenance.inspect_journal(path)["phase"] == "PAUSING"
    assert backend.calls == []


def test_pause_recheck_detects_children_without_restarting_or_stopping(tmp_path):
    backend = Backend()
    path = tmp_path / "journal.json"
    maintenance.prepare(path, backend)
    maintenance.pause(path, backend)
    backend.idle = lambda name: False
    with pytest.raises(ValueError, match="UNIT_PROCESSES_REMAIN"):
        maintenance.pause(path, backend)
    assert backend.calls == []


@pytest.mark.parametrize("populated,expected", [("0", True), ("1", False)])
def test_real_backend_reads_descendant_cgroup_population(tmp_path, monkeypatch, populated, expected):
    from types import SimpleNamespace

    name = "quantos-demo-executor-observe.service"
    group = tmp_path / "system.slice" / name
    group.mkdir(parents=True)
    (group / "cgroup.events").write_text("populated " + populated + "\nfrozen 0\n")
    monkeypatch.setattr(maintenance, "CGROUP_ROOT", tmp_path)
    monkeypatch.setattr(
        maintenance.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            stdout="LoadState=loaded\nActiveState=failed\nControlGroup=/system.slice/" + name + "\n"
        ),
    )
    assert maintenance.SystemdBackend().idle(name) is expected


def test_real_backend_rejects_escaped_cgroup_path(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        maintenance.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="LoadState=loaded\nActiveState=inactive\nControlGroup=/../../etc\n"),
    )
    with pytest.raises(ValueError, match="UNIT_CGROUP_PATH"):
        maintenance.SystemdBackend().idle("quantos-demo-executor-observe.service")


@pytest.mark.parametrize("events", ["frozen 0\n", "populated 0\npopulated 1\n", "populated unknown\n"])
def test_malformed_cgroup_observation_is_not_idle(tmp_path, monkeypatch, events):
    from types import SimpleNamespace

    name = "quantos-demo-executor-observe.service"
    group = tmp_path / "system.slice" / name
    group.mkdir(parents=True)
    (group / "cgroup.events").write_text(events)
    monkeypatch.setattr(maintenance, "CGROUP_ROOT", tmp_path)
    monkeypatch.setattr(
        maintenance.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            stdout="LoadState=loaded\nActiveState=failed\nControlGroup=/system.slice/" + name + "\n"
        ),
    )
    with pytest.raises(ValueError, match="UNIT_CGROUP_EVENTS"):
        maintenance.SystemdBackend().idle(name)


def test_missing_cgroup_property_cannot_prove_service_idle(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        maintenance.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="LoadState=loaded\nActiveState=inactive\n"),
    )
    with pytest.raises(ValueError, match="UNIT_CGROUP_UNRESOLVED"):
        maintenance.SystemdBackend().idle("quantos-demo-executor-observe.service")


def test_prepare_waits_for_transient_service_without_mutations(tmp_path, monkeypatch):
    backend = Backend()
    target = maintenance.UNITS[0]
    inspect = backend.inspect
    attempts = []

    def transient(name):
        row = inspect(name)
        if name == target:
            attempts.append(name)
            row['active'] = 'activating' if len(attempts) < 3 else 'active'
        return row

    backend.inspect = transient
    monkeypatch.setattr(maintenance, '_settle_sleep', lambda _: None, raising=False)
    path = tmp_path / 'journal.json'
    maintenance.prepare(path, backend)
    assert len(attempts) == 3 and backend.calls == []
    assert maintenance.inspect_journal(path)['units'][target]['active'] == 'active'


def test_settling_configuration_change_rejected_before_journal(tmp_path, monkeypatch):
    backend = Backend()
    calls = []

    def drift(name):
        calls.append(name)
        return {'active': 'activating' if len(calls) == 1 else 'active',
                'fingerprint': ('a' if len(calls) == 1 else 'b') * 64}

    backend.inspect = drift
    monkeypatch.setattr(maintenance, '_settle_sleep', lambda _: None, raising=False)
    path = tmp_path / 'journal.json'
    with pytest.raises(ValueError, match='CONFIGURATION_CHANGED'):
        maintenance.prepare(path, backend)
    assert backend.calls == [] and not path.exists()


def test_restore_waits_for_transition_but_restores_only_original_active(tmp_path, monkeypatch):
    backend = Backend()
    target = 'quantos-demo-executor-stream.service'
    backend.states[target] = 'active'
    path = tmp_path / 'journal.json'
    maintenance.prepare(path, backend)
    maintenance.pause(path, backend)
    inspect = backend.inspect
    pending = [True]

    def transient(name):
        row = inspect(name)
        if name == target and pending:
            pending.pop()
            row['active'] = 'deactivating'
        return row

    backend.inspect = transient
    monkeypatch.setattr(maintenance, '_settle_sleep', lambda _: None)
    maintenance.restore(path, backend)
    assert backend.calls == [('stop', target), ('start', target)]
    assert maintenance.inspect_journal(path)['phase'] == 'RESTORED'


def test_enrolled_feedback_pair_is_paused_drained_and_restored_with_legacy_jobs(tmp_path):
    backend=Backend();backend.units=maintenance.UNITS+maintenance.FEEDBACK_UNITS
    backend.states.update({name:'active' for name in maintenance.FEEDBACK_UNITS})
    path=tmp_path/'services.json'
    maintenance.prepare(path,backend)
    document=maintenance.inspect_journal(path)
    assert document['schema']=='DEMO_SCOPED_SERVICE_MAINTENANCE_V2'
    maintenance.pause(path,backend)
    assert backend.calls[:2]==[('stop','quantos-demo-feedback.timer'),('stop','quantos-demo-feedback.service')]
    maintenance.restore(path,backend)
    assert backend.calls[-2:]==[('start','quantos-demo-feedback.service'),('start','quantos-demo-feedback.timer')]


def test_feedback_enrollment_change_after_snapshot_stops_before_mutation(tmp_path):
    backend=Backend();path=tmp_path/'services.json';maintenance.prepare(path,backend)
    backend.units=maintenance.UNITS+maintenance.FEEDBACK_UNITS
    backend.states.update({name:'active' for name in maintenance.FEEDBACK_UNITS})
    with pytest.raises(ValueError,match='UNIT_SCOPE_CHANGED'):maintenance.pause(path,backend)
    assert backend.calls==[]


def test_feedback_scope_requires_exact_protected_enrollment(monkeypatch):
    import json
    from live.execution import demo_authority
    def missing(_):raise FileNotFoundError()
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',missing)
    assert maintenance.configured_units()==maintenance.UNITS
    value={'schema':'DEMO_FEEDBACK_AUTOMATION_V1','source_commit':'a'*40,'units':list(maintenance.FEEDBACK_UNITS)}
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',lambda _:json.dumps(value).encode())
    assert maintenance.configured_units()==maintenance.ALLOWED_UNITS
    value['units'].append('unrelated.service')
    with pytest.raises(ValueError):maintenance.configured_units()
