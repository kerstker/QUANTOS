"""Safety contract for the privileged Linux denied-unit isolation helper."""

from __future__ import annotations

import ast
from dataclasses import asdict
import hashlib
import json
import os
import pytest
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "isolate_l2_mvp_systemd.py"
RUNBOOK = ROOT / "docs" / "runbooks" / "l2-research-operations.md"


def test_frozen_denied_policy_is_exactly_the_central_closed_set() -> None:
    from quantos.contracts.l2_schedule_policy import (
        HARD_QUARANTINED_TASKS,
        MVP_DISABLED_TASKS,
    )
    from scripts.isolate_l2_mvp_systemd import DENIED_TASK_MODULE_PAIRS

    observed = dict(DENIED_TASK_MODULE_PAIRS)
    expected = {**HARD_QUARANTINED_TASKS, **MVP_DISABLED_TASKS}

    assert observed == expected
    assert len(DENIED_TASK_MODULE_PAIRS) == len(observed) == 28


def test_inventory_allowlist_tracks_safe_oneshots_and_exact_support_units() -> None:
    from quantos.contracts.l2_schedule_policy import (
        MVP_RESIDENT_MODULES,
        MVP_SAFE_MODULES,
    )
    from scripts.isolate_l2_mvp_systemd import SAFE_ONESHOT_MODULES, SUPPORT_UNITS

    assert SAFE_ONESHOT_MODULES == (
        MVP_SAFE_MODULES - MVP_RESIDENT_MODULES - {"l2_scheduler_watchdog"}
    )
    assert SUPPORT_UNITS == {
        "quantos-binance-realtime-stream.service",
        "quantos-bybit-realtime-stream.service",
        "quantos-console-api.service",
        "quantos-console-frontend.service",
        "quantos-l2-watchdog.service",
        "quantos-l2-watchdog.timer",
        "quantos-proxy-environment.service",
        "quantos-public-proxy-runtime.service",
    }


def test_policy_confirmation_digest_binds_ordered_task_module_pairs() -> None:
    from scripts.isolate_l2_mvp_systemd import (
        DENIED_TASK_MODULE_PAIRS,
        POLICY_SHA256,
    )

    payload = json.dumps(
        DENIED_TASK_MODULE_PAIRS,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    assert POLICY_SHA256 == hashlib.sha256(payload).hexdigest()


def test_mutation_targets_are_exact_concrete_units_without_globs() -> None:
    from scripts.isolate_l2_mvp_systemd import denied_unit_names

    units = denied_unit_names()
    assert len(units) == len(set(units)) == 56
    assert all(
        unit.startswith("quantos-l2@")
        and unit.endswith((".service", ".timer"))
        and not any(character in unit for character in "*?[]")
        for unit in units
    )
    assert "quantos-l2@.service" not in units
    assert "quantos-l2@.timer" not in units


def test_apply_artifact_is_self_contained_and_runbook_forbids_repo_sudo() -> None:
    source = HELPER.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert source.startswith("#!/usr/bin/python3 -I\n")
    for forbidden in (
        "from quantos",
        "import quantos",
        "from tools",
        "import tools",
        "shell=True",
    ):
        assert forbidden not in source
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == "system"
        for node in ast.walk(tree)
    )
    assert "/usr/bin/systemctl" in source
    assert "Never run a user-writable repository copy through sudo" in " ".join(
        runbook.split()
    )
    assert "generic quantos-l2@ service/timer templates must be absent" in runbook


def test_absent_host_plan_is_read_only_and_already_isolated(tmp_path: Path) -> None:
    from scripts.isolate_l2_mvp_systemd import build_plan

    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(list(command))
        assert command[0] == "/usr/bin/systemctl"
        assert kwargs["shell"] is False
        if command[1] in {"list-unit-files", "list-units"}:
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[1] == "show"
        unit = command[2]
        pid_lines = "" if unit.endswith(".timer") else "MainPID=0\nControlPID=0\n"
        return subprocess.CompletedProcess(
            command,
            0,
            (
                f"Id={unit}\n"
                "LoadState=not-found\n"
                "ActiveState=inactive\n"
                "SubState=dead\n"
                "UnitFileState=\n"
                "FragmentPath=\n"
                "DropInPaths=\n"
                "NeedDaemonReload=no\n"
                f"{pid_lines}"
                "Result=success\n"
            ),
            "",
        )

    plan = build_plan(
        runner=runner,
        unit_dir=tmp_path,
        required_owner_uid=0,
    )

    assert plan.isolated is True
    assert plan.verdict == "ALREADY_ISOLATED"
    assert len(plan.states) == 56
    assert {command[1] for command in calls} <= {
        "list-unit-files",
        "list-units",
        "show",
    }


def test_dangling_exact_enablement_link_prevents_absent_green_plan(
    tmp_path: Path,
) -> None:
    from scripts import isolate_l2_mvp_systemd as helper

    wants = tmp_path / "timers.target.wants"
    wants.mkdir()
    timer = helper.denied_unit_names()[1]
    link = wants / timer
    link.symlink_to(tmp_path / timer)

    def runner(command, **_kwargs):
        if command[1] in {"list-unit-files", "list-units"}:
            return subprocess.CompletedProcess(command, 0, "", "")
        unit = command[2]
        pid_lines = "" if unit.endswith(".timer") else "MainPID=0\nControlPID=0\n"
        return subprocess.CompletedProcess(
            command,
            0,
            (
                f"Id={unit}\n"
                "LoadState=not-found\n"
                "ActiveState=inactive\n"
                "SubState=dead\n"
                "UnitFileState=\n"
                "FragmentPath=\n"
                "DropInPaths=\n"
                "NeedDaemonReload=no\n"
                f"{pid_lines}"
                "Result=success\n"
            ),
            "",
        )

    plan = helper.build_plan(
        runner=runner,
        unit_dir=tmp_path,
        required_owner_uid=os.getuid(),
    )

    assert plan.isolated is False
    assert plan.verdict == "NEEDS_APPLY"
    assert plan.enablement_links == (str(link),)


def test_non_root_apply_is_rejected_before_host_inspection(monkeypatch) -> None:
    from scripts import isolate_l2_mvp_systemd as helper

    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        helper,
        "build_plan",
        lambda: (_ for _ in ()).throw(AssertionError("host inspection reached")),
    )

    assert (
        helper.main(
            [
                "--apply",
                "--confirm-policy-sha256",
                helper.POLICY_SHA256,
            ]
        )
        == 2
    )


def test_wrong_confirmation_is_rejected_before_script_or_host_inspection(
    monkeypatch,
) -> None:
    from scripts import isolate_l2_mvp_systemd as helper

    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        helper,
        "_assert_trusted_installed_script",
        lambda _path: (_ for _ in ()).throw(AssertionError("trust check reached")),
    )
    monkeypatch.setattr(
        helper,
        "build_plan",
        lambda: (_ for _ in ()).throw(AssertionError("host inspection reached")),
    )

    assert helper.main(["--apply", "--confirm-policy-sha256", "0" * 64]) == 2


def test_unknown_unit_blocks_apply_before_systemctl_or_quarantine(tmp_path: Path) -> None:
    from scripts import isolate_l2_mvp_systemd as helper

    plan = helper.HostPlan(
        policy_sha256=helper.POLICY_SHA256,
        denied_modules=28,
        denied_units=56,
        active_denied_timers=0,
        active_denied_services=0,
        startable_denied_services=0,
        regular_fragments_to_quarantine=0,
        generic_templates_to_quarantine=0,
        reviewed_non_denied_units=0,
        enablement_links=(),
        unknown_units=("quantos-unreviewed.service",),
        isolated=False,
        verdict="BLOCKED_UNKNOWN_UNITS",
        states=(),
        fragments=(),
        generic_fragments=(),
    )

    with pytest.raises(helper.IsolationError, match="unknown QUANTOS"):
        helper.apply_plan(
            plan,
            runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("systemctl mutation reached")
            ),
            unit_dir=tmp_path / "systemd",
            quarantine_root=tmp_path / "quarantine",
            required_owner_uid=os.getuid(),
        )
    assert not (tmp_path / "quarantine").exists()


def test_recovery_inventory_blocks_unknown_before_any_unit_mutation(
    tmp_path: Path,
) -> None:
    from scripts import isolate_l2_mvp_systemd as helper

    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(list(command))
        if command[1] == "list-unit-files":
            return subprocess.CompletedProcess(
                command,
                0,
                "quantos-unreviewed.service enabled enabled\n",
                "",
            )
        if command[1] == "list-units":
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unit mutation reached: {command!r}")

    with pytest.raises(helper.IsolationError, match="unknown QUANTOS"):
        helper._resume_in_progress_receipts(
            runner=runner,
            unit_dir=tmp_path / "systemd",
            quarantine_root=tmp_path / "missing-quarantine",
            required_owner_uid=os.getuid(),
        )
    assert [command[1] for command in calls] == [
        "list-unit-files",
        "list-units",
    ]


def test_apply_quarantines_then_masks_only_exact_denied_units(tmp_path: Path) -> None:
    from scripts import isolate_l2_mvp_systemd as helper

    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    first_module = helper.DENIED_TASK_MODULE_PAIRS[0][1]
    first_service = f"quantos-l2@{first_module}.service"
    first_timer = f"quantos-l2@{first_module}.timer"
    for unit in (first_service, first_timer):
        path = unit_dir / unit
        path.write_text(f"# retained {unit}\n", encoding="utf-8")
        path.chmod(0o644)

    states: dict[str, dict[str, object]] = {}
    for unit in helper.denied_unit_names():
        present = unit in {first_service, first_timer}
        states[unit] = {
            "load": "loaded" if present else "not-found",
            "active": "active" if unit == first_timer else "inactive",
            "sub": "waiting" if unit == first_timer else "dead",
            "ufs": (
                "enabled"
                if unit == first_timer
                else "static"
                if unit == first_service
                else ""
            ),
            "fragment": str(unit_dir / unit) if present else "",
            "main_pid": 0,
            "control_pid": 0,
        }

    calls: list[list[str]] = []

    def show_payload(unit: str) -> str:
        state = states[unit]
        pid_lines = (
            ""
            if unit.endswith(".timer")
            else (
                f"MainPID={state['main_pid']}\n"
                f"ControlPID={state['control_pid']}\n"
            )
        )
        return (
            f"Id={unit}\n"
            f"LoadState={state['load']}\n"
            f"ActiveState={state['active']}\n"
            f"SubState={state['sub']}\n"
            f"UnitFileState={state['ufs']}\n"
            f"FragmentPath={state['fragment']}\n"
            "DropInPaths=\n"
            "NeedDaemonReload=no\n"
            f"{pid_lines}"
            "Result=success\n"
        )

    def runner(command, **kwargs):
        calls.append(list(command))
        assert command[0] == "/usr/bin/systemctl"
        assert kwargs["shell"] is False
        verb = command[1]
        if verb == "list-unit-files":
            output = "".join(
                f"{unit} {state['ufs']} -\n"
                for unit, state in sorted(states.items())
                if state["load"] != "not-found"
            )
        elif verb == "list-units":
            output = "".join(
                f"{unit} {state['load']} {state['active']} {state['sub']} fake\n"
                for unit, state in sorted(states.items())
                if state["load"] != "not-found"
            )
        elif verb == "show":
            output = show_payload(command[2])
        elif verb == "disable":
            for unit in (item for item in command[2:] if not item.startswith("--")):
                states[unit].update(active="inactive", sub="dead", ufs="disabled")
            output = ""
        elif verb == "stop":
            for unit in command[2:]:
                states[unit].update(
                    active="inactive",
                    sub="dead",
                    main_pid=0,
                    control_pid=0,
                )
            output = ""
        elif verb == "mask":
            unit = command[-1]
            path = unit_dir / unit
            assert not os.path.lexists(path)
            path.symlink_to("/dev/null")
            states[unit].update(
                load="masked",
                active="inactive",
                sub="dead",
                ufs="masked",
                fragment=str(path),
                main_pid=0,
                control_pid=0,
            )
            output = ""
        elif verb == "daemon-reload":
            output = ""
        else:
            raise AssertionError(f"unexpected command: {command!r}")
        return subprocess.CompletedProcess(command, 0, output, "")

    owner = os.getuid()
    plan = helper.build_plan(
        runner=runner,
        unit_dir=unit_dir,
        required_owner_uid=owner,
    )
    assert plan.regular_fragments_to_quarantine == 2
    post, receipt, errors = helper.apply_plan(
        plan,
        runner=runner,
        unit_dir=unit_dir,
        quarantine_root=tmp_path / "var/lib/quantos/systemd-quarantine",
        required_owner_uid=owner,
    )

    assert errors == ()
    assert post.isolated is True
    assert receipt is not None and receipt.is_file()
    receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_document["status"] == "COMPLETE"
    assert {row["unit"] for row in receipt_document["moved_fragments"]} == {
        first_service,
        first_timer,
    }
    assert all(
        (unit_dir / unit).is_symlink()
        and os.readlink(unit_dir / unit) == "/dev/null"
        for unit in helper.denied_unit_names()
    )

    mutations = [
        command
        for command in calls
        if command[1] in {"disable", "stop", "mask", "daemon-reload"}
    ]
    verbs = [command[1] for command in mutations]
    assert verbs[:3] == ["stop", "disable", "stop"]
    assert verbs.index("mask") > 2
    assert verbs[-1] == "daemon-reload"
    mutation_units = {
        unit
        for command in mutations
        if command[1] in {"disable", "stop", "mask"}
        for unit in command[2:]
        if not unit.startswith("--")
    }
    assert mutation_units <= set(helper.denied_unit_names())
    assert not mutation_units & helper.SUPPORT_UNITS
    assert all(not any(character in unit for character in "*?[]") for unit in mutation_units)


def test_in_progress_receipt_resumes_only_hash_bound_exact_moves(tmp_path: Path) -> None:
    from scripts import isolate_l2_mvp_systemd as helper

    owner = os.getuid()
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir(mode=0o700)
    quarantine_root = tmp_path / "var/lib/quantos/systemd-quarantine"
    quarantine_root.mkdir(mode=0o700, parents=True)
    quarantine_root.chmod(0o700)
    transaction = quarantine_root / "transaction"
    units_dir = transaction / "units"
    units_dir.mkdir(mode=0o700, parents=True)
    transaction.chmod(0o700)
    units_dir.chmod(0o700)

    module = helper.DENIED_TASK_MODULE_PAIRS[0][1]
    concrete = (
        f"quantos-l2@{module}.service",
        f"quantos-l2@{module}.timer",
    )
    records = []
    for unit in concrete:
        source = unit_dir / unit
        source.write_text(f"# {unit}\n", encoding="utf-8")
        source.chmod(0o644)
        records.append(
            helper._regular_fragment_record(
                unit,
                source,
                required_owner_uid=owner,
            )
        )
    planned = [
        {
            **asdict(record),
            "stored": str(units_dir / record.unit),
        }
        for record in records
    ]
    receipt = transaction / "receipt.json"
    helper._write_receipt(
        receipt,
        {
            "schema_version": "quantos.systemd_isolation_receipt.v1",
            "status": "IN_PROGRESS",
            "policy_sha256": helper.POLICY_SHA256,
            "planned_fragments": planned,
        },
    )

    # Simulate power loss after the first atomic move and mask but before the
    # receipt status changed. The second fragment remains in the unit path.
    first_record = records[0]
    os.replace(first_record.source, units_dir / first_record.unit)
    Path(first_record.source).symlink_to("/dev/null")

    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(list(command))
        assert command[0] == "/usr/bin/systemctl"
        assert kwargs["shell"] is False
        if command[1] == "mask":
            unit = command[2]
            path = unit_dir / unit
            assert not os.path.lexists(path)
            path.symlink_to("/dev/null")
        return subprocess.CompletedProcess(command, 0, "", "")

    recovered = helper._resume_in_progress_receipts(
        runner=runner,
        unit_dir=unit_dir,
        quarantine_root=quarantine_root,
        required_owner_uid=owner,
    )

    assert recovered == (receipt,)
    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document["status"] == "RECOVERED_FOR_RETRY"
    assert {row["unit"] for row in document["moved_fragments"]} == set(concrete)
    assert all((units_dir / unit).is_file() for unit in concrete)
    assert all(
        (unit_dir / unit).is_symlink()
        and os.readlink(unit_dir / unit) == "/dev/null"
        for unit in concrete
    )
    mutation_verbs = [
        command[1]
        for command in calls
        if command[1] in {"stop", "disable", "mask", "daemon-reload"}
    ]
    assert mutation_verbs == [
        "stop",
        "disable",
        "stop",
        "mask",
        "daemon-reload",
    ]
    assert (
        helper._resume_in_progress_receipts(
            runner=runner,
            unit_dir=unit_dir,
            quarantine_root=quarantine_root,
            required_owner_uid=owner,
        )
        == ()
    )
