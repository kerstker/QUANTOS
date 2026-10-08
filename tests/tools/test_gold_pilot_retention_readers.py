"""Approval readers use the shared retention contract, preserving exact bytes."""

from copy import deepcopy
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import budget_group_binding
from tests.live.test_gold_strategy_constraints import case as case, request_inputs, NOW
from tests.tools.test_gold_strategy_approval_request import inputs
from tests.tools.test_gold_runtime_evidence import (
    original_evidence as original_evidence,
    cross_root as cross_root,
    evidence_snapshot,
    private_tree,
)

SOURCE = "c" * 40


@pytest.fixture
def reader(case, tmp_path, monkeypatch):
    """Only retention is a contract substitute; native approval/task checks run."""
    import tools
    from tools import gold_strategy_approval_request as approval
    from tools import gold_strategy_dispatch as dispatch
    from tools import demo_operating_bundle as bundle, demo_supervisor as supervisor

    result, runtime, policy = inputs(case)
    policy["bindings"]["execution_constraints_sha256"] = _digest(result["decision"]["execution_constraints"])
    args = request_inputs(case)
    root = tmp_path / "controller"
    root.mkdir(mode=0o700)
    for name in ("strategy-decisions", "strategy-approval-requests", "tasks", "task-claims"):
        (root / name).mkdir(mode=0o700)
    identity = _digest(result)
    account = policy["account_ref_sha256"]
    registry = {"executor_uid": os.getuid()}
    request = approval.prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 10)
    reference = dict(
        schema="GOLD_STRATEGY_TASK_APPROVAL_REFERENCE_V1",
        source_commit=SOURCE,
        result_sha256=identity,
        issuer_policy_sha256=_digest(policy),
        kill_switch=args["kill_state"].model_dump(mode="json"),
    )
    config = dict(
        schema="GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1",
        source_commit=SOURCE,
        account_ref_sha256=account,
        budget_group_binding_sha256=budget_group_binding(policy),
        kill_switch=reference["kill_switch"],
    )
    for name, value in (
        ("strategy-runtime.json", runtime),
        ("strategy-task-approval.json", reference),
        ("strategy-dispatch.json", config),
        ("profile.json", args["profile"]),
    ):
        (root / name).write_text(json.dumps(value))
        (root / name).chmod(0o600)

    def protected_read(path):
        path.relative_to(root)
        assert path.parent not in (root / "strategy-decisions", root / "strategy-approval-requests"), (
            "reader bypassed the retention contract"
        )
        return path.read_bytes()

    monkeypatch.setattr(supervisor, "STATE", root)
    monkeypatch.setattr(supervisor, "_root_private_directory", lambda path: path.relative_to(root))
    monkeypatch.setattr(supervisor, "_read_root_owned_json", protected_read)
    monkeypatch.setattr(supervisor, "configuration", lambda _: (registry, policy))
    monkeypatch.setattr(approval, "approval_configuration", lambda _: (registry, policy))
    monkeypatch.setattr(supervisor, "now_ms", lambda: NOW + 10)
    monkeypatch.setattr(dispatch, "PROFILE", root / "profile.json")
    monkeypatch.setattr(bundle, "write_new", lambda path, body: path.write_bytes(body))
    monkeypatch.setattr(dispatch.demo_task, "run", lambda **kw: pytest.fail("test may not submit"))
    value = SimpleNamespace(
        root=root,
        result=result,
        policy=policy,
        runtime=runtime,
        request=request,
        identity=identity,
        account=account,
        registry=registry,
        reads=[],
        approval_reads=[],
        selections=[],
        raw=bundle.canonical(result),
        approval_raw=bundle.canonical(request),
        returned_ids=[identity],
        changed_read=None,
        changed_approval_read=None,
        changed_selection=None,
    )
    contract = ModuleType("tools.gold_pilot_retention")

    def read_controller_result(root_arg, identity_arg, *, source_commit, account):
        assert (root_arg, identity_arg, source_commit, account) == (root, identity, SOURCE, value.account)
        value.reads.append(identity_arg)
        if value.changed_read is not None:
            return value.changed_read(len(value.reads))
        return value.raw

    def controller_result_ids(root_arg, *, source_commit, account, constraints_sha256):
        assert (root_arg, source_commit, account, constraints_sha256) == (
            root,
            SOURCE,
            value.account,
            policy["bindings"]["execution_constraints_sha256"],
        )
        value.selections.append(constraints_sha256)
        if value.changed_selection is not None:
            return value.changed_selection(len(value.selections))
        return list(value.returned_ids)

    def read_controller_approval(root_arg, identity_arg, *, source_commit, account):
        assert (root_arg, identity_arg, source_commit, account) == (root, _digest(request), SOURCE, value.account)
        value.approval_reads.append(identity_arg)
        if value.changed_approval_read is not None:
            return value.changed_approval_read(len(value.approval_reads))
        return value.approval_raw

    contract.read_controller_result = read_controller_result
    contract.controller_result_ids = controller_result_ids
    contract.read_controller_approval = read_controller_approval
    monkeypatch.setitem(sys.modules, contract.__name__, contract)
    monkeypatch.setattr(tools, "gold_pilot_retention", contract, raising=False)
    return value


def test_retained_review_reads_and_rechecks_via_retention(reader):
    from tools.gold_strategy_approval_request import review_retained

    result = review_retained(source_commit=SOURCE, stored_request_sha256=_digest(reader.request))
    assert result["status"] == "REQUEST_REVIEWED_NOT_APPROVED"
    assert result["result_sha256"] == reader.identity
    assert len(reader.reads) == 2 and not reader.selections
    assert len(reader.approval_reads) == 2
    assert list((reader.root / "tasks").iterdir()) == []


@pytest.mark.parametrize("selection", ["explicit", "targeted", "untargeted"])
def test_dispatch_reads_original_retained_result_without_old_directory(reader, selection):
    from tools.gold_strategy_dispatch import run

    kwargs = {} if selection == "explicit" else {"use_approved_policy": True}
    if selection == "targeted":
        kwargs["result_sha256"] = reader.identity
    result = run(source_commit=SOURCE, **kwargs)
    assert result["status"] == "TASK_READY", result
    task = json.loads(next((reader.root / "tasks").iterdir()).read_bytes())
    assert task["execution_constraints"] == reader.result["decision"]["execution_constraints"]
    assert task["orders"] == reader.result["decision"]["draft"]["orders"]
    assert len(reader.reads) >= 2
    assert len(reader.selections) == (2 if selection == "untargeted" else 0)


def test_pending_reference_uses_shared_reader_and_keeps_no_task(reader):
    from tools.gold_strategy_dispatch import pending_scope_reference

    result = pending_scope_reference(SOURCE, reader.registry, reader.policy, reader.identity)
    assert result["status"] == "WAITING_INDEPENDENT_APPROVAL"
    assert result["result_sha256"] == reader.identity
    assert result["task_sha256"] is None and not result["retry_submission"]
    assert len(reader.reads) == 2 and not reader.selections
    assert len(reader.approval_reads) == 2
    assert list((reader.root / "tasks").iterdir()) == []


@pytest.mark.parametrize("consumer", ["review", "pending", "dispatch"])
def test_result_byte_drift_is_rejected_before_materialization(reader, consumer):
    from tools import gold_strategy_approval_request as approval, gold_strategy_dispatch as dispatch

    # Same JSON and digest, different original bytes: final review must still
    # reject a replacement even if the retention API has already validated it.
    reader.changed_read = lambda n: reader.raw if n == 1 else b" " + reader.raw
    if consumer == "review":
        with pytest.raises(ValueError, match="INPUTS_CHANGED"):
            approval.review_retained(source_commit=SOURCE, stored_request_sha256=_digest(reader.request))
    elif consumer == "pending":
        with pytest.raises(ValueError, match="INPUTS_CHANGED"):
            dispatch.pending_scope_reference(SOURCE, reader.registry, reader.policy, reader.identity)
    else:
        result = dispatch.run(source_commit=SOURCE)
        assert result["status"] == "INPUTS_UNRESOLVED" and result["phase"] == "ORIGINAL_TASK_MATERIALIZATION"
    assert len(reader.reads) == 2
    assert list((reader.root / "tasks").iterdir()) == []


@pytest.mark.parametrize("consumer", ["review", "pending"])
def test_approval_attachment_byte_drift_is_rejected_without_new_request(reader, consumer):
    from tools import gold_strategy_approval_request as approval, gold_strategy_dispatch as dispatch

    reader.changed_approval_read = lambda n: reader.approval_raw if n == 1 else b" " + reader.approval_raw
    with pytest.raises(ValueError, match="INPUTS_CHANGED"):
        if consumer == "review":
            approval.review_retained(source_commit=SOURCE, stored_request_sha256=_digest(reader.request))
        else:
            dispatch.pending_scope_reference(SOURCE, reader.registry, reader.policy, reader.identity)
    assert len(reader.approval_reads) == 2
    assert list((reader.root / "strategy-approval-requests").iterdir()) == []
    assert list((reader.root / "tasks").iterdir()) == []


def test_untargeted_new_ambiguity_is_rejected_before_materialization(reader):
    from tools.gold_strategy_dispatch import run

    reader.changed_selection = lambda n: [reader.identity] if n == 1 else [reader.identity, "e" * 64]
    result = run(source_commit=SOURCE, use_approved_policy=True)
    assert result["status"] == "INPUTS_UNRESOLVED" and result["phase"] == "ORIGINAL_TASK_MATERIALIZATION"
    assert len(reader.selections) == 2
    assert list((reader.root / "tasks").iterdir()) == []


def test_targeted_does_not_scan_other_retained_results(reader):
    from tools.gold_strategy_dispatch import run

    reader.changed_selection = lambda _: pytest.fail("targeted selection scanned history")
    reader.returned_ids = [reader.identity, "e" * 64]
    result = run(source_commit=SOURCE, use_approved_policy=True, result_sha256=reader.identity)
    assert result["status"] == "TASK_READY" and not reader.selections


def test_matching_constraints_are_still_ambiguous(reader):
    from tools.gold_strategy_dispatch import run

    reader.returned_ids = [reader.identity, "e" * 64]
    result = run(source_commit=SOURCE, use_approved_policy=True)
    assert result["status"] == "INPUTS_UNRESOLVED"
    assert len(reader.selections) == 1
    assert list((reader.root / "tasks").iterdir()) == []


def test_missing_protected_result_never_falls_back_to_legacy_copy(reader):
    from tools.gold_strategy_dispatch import run

    (reader.root / "strategy-decisions" / (reader.identity + ".json")).write_bytes(reader.raw)

    def missing(_):
        raise FileNotFoundError("retained original missing")

    reader.changed_read = missing
    result = run(source_commit=SOURCE)
    assert result["status"] == "INPUTS_UNRESOLVED" and result["phase"] == "RETAINED_DECISION"
    assert len(reader.reads) == 1 and list((reader.root / "tasks").iterdir()) == []


def test_changed_result_identity_is_rejected_before_task(reader):
    from tools.gold_strategy_dispatch import run

    changed = deepcopy(reader.result)
    changed["unapproved_metadata"] = True
    reader.raw = json.dumps(changed).encode()
    result = run(source_commit=SOURCE)
    assert result["status"] == "INPUTS_UNRESOLVED" and result["phase"] == "RETAINED_DECISION"
    assert list((reader.root / "tasks").iterdir()) == []


def test_original_claim_still_prevents_resend_after_lost_result(reader, monkeypatch):
    from tools import gold_strategy_dispatch as dispatch

    attempts = []

    def interrupted(**kwargs):
        attempts.append(kwargs["task_sha256"])
        dispatch.supervisor.claim(
            reader.root / "task-claims", {"task_sha256": kwargs["task_sha256"]}, identity_field="task_sha256"
        )
        raise OSError("isolated response loss after original claim")

    monkeypatch.setattr(dispatch.demo_task, "run", interrupted)
    first = dispatch.run(source_commit=SOURCE, execute=True, use_approved_policy=True, result_sha256=reader.identity)
    assert first["status"] == "INPUTS_UNRESOLVED" and first["phase"] == "ORIGINAL_TASK"
    again = dispatch.run(source_commit=SOURCE, execute=True, use_approved_policy=True, result_sha256=reader.identity)
    assert again["status"] == "QUERY_ONLY" and len(attempts) == 1
    assert not reader.selections


@pytest.fixture(scope="module")
def pilot_original_evidence(original_evidence, tmp_path_factory):
    """AUTO requires full capture receipts; build them through native capture."""
    from live.exchange import demo_network
    from tools import gold_bar_capture as capture, gold_bar_forward as forward
    from tests.tools.test_gold_bar_observe import recorded
    from tests.research.test_gold_bar_dataset import raw_rows, STEP

    root = tmp_path_factory.mktemp("retention-public-originals")
    public = root / "recorded-transport"
    public.mkdir()
    history, observation = root / "history", root / "observation"
    raw = json.dumps(raw_rows()).encode()
    digest = hashlib.sha256(raw).hexdigest()
    end = raw_rows()[-1][6] + 1

    def fetch(output, endpoint, requested, *, protected_network):
        assert protected_network is True
        (output / "response.json").write_bytes(raw)
        (output / "response.wire").write_bytes(raw)
        (output / "receipt.json").write_text(
            json.dumps(
                dict(
                    endpoint=endpoint,
                    requested_at_ms=end,
                    received_at_ms=end + 10,
                    content_encoding="identity",
                    response_sha256=digest,
                    wire_sha256=digest,
                    network_mode="PROTECTED_DEMO_NETWORK_V1",
                ),
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        return raw, end + 10

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(capture, "_fetch", fetch)
        patch.setattr(capture.time, "time_ns", lambda: end * 1_000_000)
        assert capture.capture(history, protected_network=True)["status"] == "CAPTURED_FOR_RESEARCH"
        patch.setattr(demo_network, "proxy_url", lambda: None)
        patch.setattr(demo_network, "tls_context", lambda: object())
        assert recorded(public, patch, stop_after_close_ms=end + 100, protected_network=True)[0]["status"] == "OBSERVED"
        times = iter([end + 100, end + STEP + 21])
        patch.setattr(forward, "now_ms", lambda: next(times))
        patch.setattr(forward, "source_identity", lambda: {"fixture": True})
        fresh = original_evidence["fresh"]
        result = asyncio.run(
            forward.run(
                original_evidence["root"] / "strategy-bar-trials" / fresh["trial_manifest_sha256"],
                candidate=fresh["candidate"],
                source_directory=original_evidence["root"] / "strategy-bar-sources" / fresh["archive_source_commit"],
                history=history,
                output=observation,
                seconds=fresh["seconds"],
                first_close=True,
                protected_network=True,
            )
        )
        assert result["status"] == "FORWARD_SIGNALS_RECORDED"
    private_tree(root)
    return dict(history=history, observation=observation)


@pytest.fixture
def native_pilot(cross_root, pilot_original_evidence, tmp_path, monkeypatch, request):
    """Real pilot enrollment, bar originals and both custody retention stores."""
    from live.execution import demo_authority, demo_gold_pilot as pilot
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution import demo_position_context as position, demo_market_partition_runtime as partition
    from tests.live.test_demo_market_partition_runtime import shift
    from tests.live.test_demo_pre_submit import stores
    from tools import demo_operating_bundle as bundle, demo_supervisor as supervisor
    from tools import gold_strategy_controller as controller, gold_strategy_approval_request as approval
    from tools import gold_bar_pilot_capacity as capacity, gold_pilot_retention as retention
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_bar_fresh_inputs import FreshEpisode
    from tools.gold_bar_episode_tail import EpisodeTail, restore_snapshot
    from tools.gold_strategy_request import constraints_from_draft

    runtime_kind = getattr(request, "param", "auto")
    # The inherited issuer fixture virtualizes euid=2001. Native filesystem
    # custody here uses this process's actual owner for both isolated roots.
    monkeypatch.setattr(os, "geteuid", os.getuid)
    f = cross_root
    root, state = f["executor"], f["controller"]
    at = f["original"]["entry_ms"]
    registry, policy = supervisor.configuration(SOURCE)
    account = policy["account_ref_sha256"]
    monkeypatch.setattr(position, "_ROOT", root)
    monkeypatch.setattr(demo_authority, "load_registry_for_executor", lambda: registry)
    monkeypatch.setattr(demo_authority, "_require_protected", lambda info, **kw: None)
    for name in ("EXECUTOR_RECORD", "ISSUER_RECORD", "ACTIVATION"):
        monkeypatch.setattr(partition, name, tmp_path / name)
    monkeypatch.setattr(controller, "observation_configuration", lambda _: (registry, policy))
    request = dict(
        schema="GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1",
        source_commit=SOURCE,
        account_ref_sha256=account,
        duration_seconds=86400,
        max_loss_fraction="0.05",
        submission_enabled=False,
    )
    pilot.retain_once(root / "gold-pilot-request.json", request)
    dispatch_store, recovery, oms = stores(root)
    try:
        origin = shift(f["stores"]["origin"], at - NOW - 1000)
        config = enrollment.enroll_requested(
            root=root,
            source_commit=SOURCE,
            origin=origin,
            dispatch=dispatch_store,
            recovery=recovery,
            oms=oms,
            now_ms=at - 1000,
        )
        assert enrollment.completed_configuration(root / "gold-pilot", request=request) == config
        template_kind = "fresh" if runtime_kind == "auto" else runtime_kind
        base = deepcopy(f["original"][template_kind])
        original = dict(request=request, configuration=config)
        prefix = f["original"]["prefix"] if template_kind == "fresh" else None
        if runtime_kind == "auto":
            base.update(schema="GOLD_BAR_AUTO_EPISODE_INPUT_V1", episode_id=base.pop("history_sha256"))
            assert capacity.context(root, base, uid=os.getuid(), account=account) == original
            # Move only freshly generated fixture material before taking any
            # consumption snapshot; the production reader never migrates it.
            old_observation = root / "strategy-bar-observation-work" / _digest(f["original"]["fresh"])
            shutil.move(old_observation, tmp_path / "recorded-observation")
            old_observation.parent.rmdir()
            for kind, source in (
                ("history", pilot_original_evidence["history"]),
                ("observations", pilot_original_evidence["observation"]),
            ):
                area, binding = capacity.storage(root, kind, original, base, uid=os.getuid())
                capacity.allocate_capture(area, binding, kind, os.getuid(), base)
                shutil.copytree(source, area / _digest(base))
                private_tree(area / _digest(base))
            episode = FreshEpisode(root, base, source_commit=SOURCE, uid=os.getuid(), read_only=True)
            tail = EpisodeTail(root, bundle_sha256=_digest(base), engine=episode.engine, entry_decision_ms=at)
            _, prefix, _ = tail.snapshot()
            rebuilt = restore_snapshot(
                root, bundle_sha256=_digest(base), engine=episode.engine, entry_decision_ms=at, snapshot_sha256=prefix
            )
            assert rebuilt.signal_at(at) == episode.engine.signal_at(at)

        prior_request = f["install"](template_kind)
        prior_path = state / "strategy-decisions" / (prior_request["result_sha256"] + ".json")
        result = json.loads(prior_path.read_bytes())
        prior_path.unlink()
        (state / "strategy-approval-requests" / (_digest(prior_request) + ".json")).unlink()
        control = dict(status="ACTIVE", configuration_sha256=_digest(config), submission_enabled=False)
        decision = result["decision"]
        decision.update(bundle_sha256=_digest(base), pilot_control=control)
        if prefix is not None:
            decision["bar_continuation_sha256"] = prefix
        draft = decision["draft"]
        draft["pilot_control"] = control
        draft.pop("result_hash")
        draft["result_hash"] = _digest(draft)
        decision["execution_constraints"] = constraints_from_draft(draft, now_ms=at + 10)
        result.update(
            bundle_sha256=_digest(base),
            decision_sha256=_digest(decision),
            original_pilot_window=dict(
                schema="GOLD_PILOT_ORIGINAL_WINDOW_V1",
                source_commit=SOURCE,
                account_ref_sha256=account,
                request_sha256=_digest(request),
                configuration_sha256=_digest(config),
                started_at_ms=config["started_at_ms"],
                ends_at_ms=config["ends_at_ms"],
                submission_enabled=False,
            ),
        )
        policy["bindings"]["execution_constraints_sha256"] = _digest(decision["execution_constraints"])
        approved = approval.prepare(
            result, runtime=base, base_runtime=base, policy=policy, registry=registry, now_ms=at + 10
        )
        identity = _digest(result)
        reference = json.loads((state / "strategy-task-approval.json").read_bytes())
        reference.update(result_sha256=identity, issuer_policy_sha256=_digest(policy))
        dispatch_config = dict(
            schema="GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1",
            source_commit=SOURCE,
            account_ref_sha256=account,
            budget_group_binding_sha256=budget_group_binding(policy),
            kill_switch=reference["kill_switch"],
        )
        for name, value in (
            ("strategy-runtime.json", base),
            ("strategy-task-approval.json", reference),
            ("strategy-dispatch.json", dispatch_config),
        ):
            path = state / name
            path.write_bytes(bundle.canonical(value))
            path.chmod(0o600)
        with Retention(root, original=original, base_runtime=base, uid=os.getuid(), role="executor") as db:
            claim = db.claim(bundle=base, now_ms=at)
            db.put(claim, "decision", decision)
            db.put(claim, "result", result)
            original_result_bytes = db.read("result", identity)
        with retention.ControllerRetention(
            state, base_runtime=base, source_commit=SOURCE, account=account, executor_uid=os.getuid()
        ) as session:
            session.retain(result)
            session.attach(result, "approval", approved)
            original_approval_bytes = session.store.read("approval", _digest(approved))
        assert list((state / "strategy-decisions").iterdir()) == []
        assert list((state / "strategy-approval-requests").iterdir()) == []
        assert (
            retention.read_controller_result(state, identity, source_commit=SOURCE, account=account)
            == original_result_bytes
        )
        assert (
            retention.read_controller_approval(state, _digest(approved), source_commit=SOURCE, account=account)
            == original_approval_bytes
        )
        yield SimpleNamespace(
            f=f,
            root=root,
            state=state,
            request=approved,
            result=result,
            identity=identity,
            registry=registry,
            policy=policy,
            base=base,
            account=account,
            original=original,
            at=at,
            prefix=prefix,
        )
        assert dispatch_store.snapshot() == [] and recovery.latest_records() == () and oms.orders() == []
    finally:
        for store in (dispatch_store, recovery, oms):
            store.close()


@pytest.mark.parametrize("consumer", ["review", "pending", "explicit", "targeted", "untargeted"])
def test_native_pilot_original_store_and_prefix_reach_each_reader_without_writes(native_pilot, consumer):
    from tools import gold_strategy_approval_request as approval, gold_strategy_dispatch as dispatch

    f = native_pilot
    before = evidence_snapshot(f.root)
    retained_before = {p: p.read_bytes() for p in f.state.rglob("*") if p.is_file()}
    if consumer == "review":
        result = approval.review_retained(source_commit=SOURCE, stored_request_sha256=_digest(f.request))
        assert result["status"] == "REQUEST_REVIEWED_NOT_APPROVED"
        if f.prefix is not None:
            assert result["bar_prefix_review"]["snapshot_sha256"] == f.prefix
    elif consumer == "pending":
        result = dispatch.pending_scope_reference(SOURCE, f.registry, f.policy, f.identity)
        assert result["status"] == "WAITING_INDEPENDENT_APPROVAL" and result["task_sha256"] is None
    else:
        kwargs = {} if consumer == "explicit" else {"use_approved_policy": True}
        if consumer == "targeted":
            kwargs["result_sha256"] = f.identity
        result = dispatch.run(source_commit=SOURCE, **kwargs)
        assert result["status"] == "TASK_READY", result
        task = json.loads(next((f.state / "tasks").iterdir()).read_bytes())
        assert task["execution_constraints"] == f.result["decision"]["execution_constraints"]
        assert task["orders"] == f.result["decision"]["draft"]["orders"]
    assert evidence_snapshot(f.root) == before
    assert all(path.read_bytes() == body for path, body in retained_before.items())
    assert list((f.state / "strategy-decisions").iterdir()) == []
    assert list((f.state / "strategy-approval-requests").iterdir()) == []
    assert not f.f["forbidden"]


@pytest.mark.parametrize("native_pilot", ["fresh", "retained"], indirect=True)
@pytest.mark.parametrize("consumer", ["review", "targeted"])
def test_native_non_auto_bar_originals_use_same_pilot_retention(native_pilot, consumer):
    test_native_pilot_original_store_and_prefix_reach_each_reader_without_writes(native_pilot, consumer)


def damage_record(db, *, kind, identity, replacement=None):
    """Inject offline damage below SQL guards, then restore the exact schema."""
    name = "records_no_delete" if replacement is None else "records_no_update"
    trigger = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
    db.execute("DROP TRIGGER " + name)
    if replacement is None:
        db.execute("DELETE FROM records WHERE kind=? AND identity=?", (kind, identity))
    else:
        db.execute("UPDATE records SET payload=? WHERE kind=? AND identity=?", (replacement, kind, identity))
    db.execute(trigger)


@pytest.mark.parametrize("consumer", ["review", "dispatch"])
@pytest.mark.parametrize("damage", ["missing_result", "tampered_result", "missing_enrollment", "executor_uid"])
def test_native_missing_changed_or_wrong_custody_original_cannot_fall_back(native_pilot, consumer, damage):
    from tools import demo_operating_bundle as bundle
    from tools import gold_strategy_approval_request as approval, gold_strategy_dispatch as dispatch
    from tools.gold_pilot_retention_store import AREA

    f = native_pilot
    # A same-identity legacy copy must never mask damage to an active native
    # retention contract. Only this isolated test tree is deliberately damaged.
    legacy = f.state / "strategy-decisions" / (f.identity + ".json")
    legacy.write_bytes(bundle.canonical(f.result))
    legacy.chmod(0o600)
    if damage in ("missing_result", "tampered_result"):
        with sqlite3.connect(f.state / AREA / "records.sqlite3") as db:
            if damage == "missing_result":
                damage_record(db, kind="result", identity=f.identity)
            else:
                changed = deepcopy(f.result)
                changed["decision"]["draft"]["orders"][0]["quantity"] = "0.009"
                damage_record(db, kind="result", identity=f.identity, replacement=bundle.canonical(changed))
    elif damage == "missing_enrollment":
        (f.root / "gold-pilot" / "enrollment.json").unlink()
    else:
        f.registry["executor_uid"] += 1
    before = evidence_snapshot(f.root)
    controller_before = evidence_snapshot(f.state)
    if consumer == "review":
        with pytest.raises((ValueError, FileNotFoundError)):
            approval.review_retained(source_commit=SOURCE, stored_request_sha256=_digest(f.request))
    else:
        result = dispatch.run(source_commit=SOURCE)
        assert result["status"] == "INPUTS_UNRESOLVED", result
    assert evidence_snapshot(f.root) == before
    # The existing controller lease may be created before the first read.
    controller_after = evidence_snapshot(f.state)
    assert all(controller_after.get(name) == value for name, value in controller_before.items() if name)
    assert not list((f.state / "tasks").iterdir()) and not f.f["forbidden"]


@pytest.mark.parametrize("consumer", ["review", "pending"])
@pytest.mark.parametrize("damage", ["missing_approval", "tampered_approval"])
def test_native_approval_attachment_cannot_fall_back_to_old_request(native_pilot, consumer, damage):
    from tools import demo_operating_bundle as bundle
    from tools import gold_strategy_approval_request as approval, gold_strategy_dispatch as dispatch
    from tools.gold_pilot_retention_store import AREA

    f = native_pilot
    identity = _digest(f.request)
    legacy = f.state / "strategy-approval-requests" / (identity + ".json")
    legacy.write_bytes(bundle.canonical(f.request))
    legacy.chmod(0o600)
    with sqlite3.connect(f.state / AREA / "records.sqlite3") as db:
        if damage == "missing_approval":
            damage_record(db, kind="approval", identity=identity)
        else:
            changed = deepcopy(f.request)
            changed["result_sha256"] = "f" * 64
            damage_record(db, kind="approval", identity=identity, replacement=bundle.canonical(changed))
    before = evidence_snapshot(f.root)
    with pytest.raises((ValueError, FileNotFoundError)):
        if consumer == "review":
            approval.review_retained(source_commit=SOURCE, stored_request_sha256=identity)
        else:
            dispatch.pending_scope_reference(SOURCE, f.registry, f.policy, f.identity)
    assert evidence_snapshot(f.root) == before
    assert not list((f.state / "tasks").iterdir()) and not f.f["forbidden"]
