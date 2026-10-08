"""Delegated issuer admission uses the original reservation ledger and slice."""

from contextlib import closing
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_store import IssuerStore, consume_group
from services.execution.perpetual_ledger_v2 import canonical_decimal
from tests.live.test_gold_strategy_constraints import case as case
from tests.live.test_gold_reserved_exit_budget import budget, stored_plan
from tests.live.test_gold_strategy_constraints import NOW
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence


def anchor_for(rows, policy):
    groups = {}
    reserved, signed = set(), set()
    for row in rows:
        if row["kind"] == "RESERVED":
            value = row["payload"]
            consume_group(groups, value["policy"], value["plan"])
            reserved.add(row["proposal_sha256"])
        else:
            signed.add(row["proposal_sha256"])
    group = groups[policy["budget_group"]["group_id"]]
    return dict(
        head_sha256=rows[-1]["record_hash"],
        records=len(rows),
        history_sha256=_digest(rows),
        group_binding_sha256=group["binding"],
        reserved=len(reserved),
        unsigned_reservations=len(reserved - signed),
        group_consumed={
            purpose: dict(grants=value[0], orders=value[1], notional=canonical_decimal(value[2]))
            for purpose, value in group.items()
            if purpose != "binding"
        },
    )


def delegated_plan(args, tmp_path, index, registry, anchor, *, reduction=False):
    # Store-only adversarial fixture. Actual proof/signature/replayed strategy
    # admission is exercised separately; this does not claim a legal exit.
    plan, policy = stored_plan(args, tmp_path, index, registry, reduce_only=reduction)
    proof = _digest(dict(synthetic_decision=index))
    policy["policy_id"] = "gold-delegated-" + proof
    plan["context"]["delegated_admission_sha256"] = proof
    plan["proposal_sha256"] = _digest(plan)
    delegation = dict(
        schema="DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V1",
        issuer_anchor=deepcopy(anchor),
        enable_issuer_anchor=deepcopy(anchor),
        delegated_admission_sha256=proof,
        enable_sha256="e" * 64,
        delegation_sha256="d" * 64,
        base_policy_sha256=_digest(args["issuer_policy"]),
    )
    return plan, policy, delegation


@pytest.fixture
def original_history(case, tmp_path):
    _, _, _, registry, _, log = case
    args = budget(case)
    args["issuer_policy"]["budget_group"]["max_grants"] = 3
    plan, policy = stored_plan(args, tmp_path, 1, registry)
    log.reserve(plan, policy=policy, registry=registry)
    return args, registry, log, anchor_for(log.snapshot(), policy)


def test_original_unsigned_anchor_accepts_append_and_survives_restart(original_history, tmp_path):
    args, registry, log, anchor = original_history
    assert anchor["unsigned_reservations"] == 1
    plan, policy, evidence = delegated_plan(args, tmp_path, 2, registry, anchor)
    log.reserve(plan, policy=policy, registry=registry, delegation=evidence)
    original = log.snapshot()
    assert len(original) == 2 and original[-1]["payload"]["delegation"] == evidence
    with closing(IssuerStore(log.path)) as reopened:
        assert reopened.snapshot() == original
        exit_plan, exit_policy, exit_evidence = delegated_plan(args, tmp_path, 3, registry, anchor, reduction=True)
        reopened.reserve(exit_plan, policy=exit_policy, registry=registry, delegation=exit_evidence)
        assert len(reopened.snapshot()) == 3


@pytest.mark.parametrize("damage", ["head", "count", "hash", "group", "consumed", "unsigned", "reserved", "bool"])
def test_invalid_anchor_rejects_before_ledger_mutation(original_history, tmp_path, damage):
    args, registry, log, anchor = original_history
    bad = deepcopy(anchor)
    if damage == "head":
        bad["head_sha256"] = "f" * 64
    elif damage == "count":
        bad["records"] += 1
    elif damage == "hash":
        bad["history_sha256"] = "f" * 64
    elif damage == "group":
        bad["group_binding_sha256"] = "f" * 64
    elif damage == "consumed":
        bad["group_consumed"]["submission"]["grants"] = 0
    elif damage == "unsigned":
        bad["unsigned_reservations"] = 0
    elif damage == "reserved":
        bad["reserved"] = 0
    else:
        bad["records"] = True
    plan, policy, evidence = delegated_plan(args, tmp_path, 2, registry, bad)
    before = log.snapshot()
    with pytest.raises(ValueError):
        log.reserve(plan, policy=policy, registry=registry, delegation=evidence)
    assert log.snapshot() == before


def test_recreated_ledger_cannot_reuse_original_enabled_budget(original_history, tmp_path):
    args, registry, _, anchor = original_history
    plan, policy, evidence = delegated_plan(args, tmp_path, 2, registry, anchor)
    with closing(IssuerStore(tmp_path / "rolled-back.db", create=True)) as empty:
        with pytest.raises(ValueError):
            empty.reserve(plan, policy=policy, registry=registry, delegation=evidence)
        assert empty.snapshot() == []


def test_two_delegated_decisions_share_original_last_opening_slot(original_history, tmp_path):
    args, registry, log, anchor = original_history
    proposals = [delegated_plan(args, tmp_path, index, registry, anchor) for index in (2, 3)]
    gate = Barrier(2)

    def reserve(value):
        plan, policy, evidence = value
        with closing(IssuerStore(log.path)) as own:
            gate.wait(timeout=5)
            try:
                own.reserve(plan, policy=policy, registry=registry, delegation=evidence)
                return "RESERVED"
            except ValueError as exc:
                assert "EXIT_RESERVE" in str(exc)
                return "RESERVE_PRESERVED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, proposals)) == ["RESERVED", "RESERVE_PRESERVED"]
    assert len(log.snapshot()) == 2


def test_context_and_compact_delegation_are_an_inseparable_pair(original_history, tmp_path):
    args, registry, log, anchor = original_history
    plan, policy, evidence = delegated_plan(args, tmp_path, 2, registry, anchor)
    with pytest.raises(ValueError):
        log.reserve(plan, policy=policy, registry=registry)
    evidence["delegated_admission_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        log.reserve(plan, policy=policy, registry=registry, delegation=evidence)
    assert len(log.snapshot()) == 1


def test_registration_prefix_cannot_hide_rollback_of_later_enable_history(original_history, tmp_path):
    from live.execution.demo_gold_delegation_enable import verify_history_prefix

    args, registry, log, registration = original_history
    earlier = log.path.read_bytes()
    second, second_policy = stored_plan(args, tmp_path, 2, registry)
    log.reserve(second, policy=second_policy, registry=registry)
    enabled = anchor_for(log.snapshot(), second_policy)
    assert enabled["records"] > registration["records"]
    plan, policy, evidence = delegated_plan(args, tmp_path, 3, registry, registration, reduction=True)
    evidence["enable_issuer_anchor"] = enabled
    log.reserve(plan, policy=policy, registry=registry, delegation=evidence)
    assert len(log.snapshot()) == 3
    copied = tmp_path / "before-enable.db"
    copied.write_bytes(earlier)
    copied.chmod(0o600)
    with closing(IssuerStore(copied)) as rollback:
        verify_history_prefix(registration, rollback.snapshot(), policy=policy)
        with pytest.raises(ValueError, match="HISTORY_ROLLBACK"):
            rollback.reserve(plan, policy=policy, registry=registry, delegation=evidence)
        assert len(rollback.snapshot()) == 1


def test_private_signing_stage_keeps_compact_proof_in_original_signed_context(original_history, case, tmp_path):
    # This exercises the private post-admission signing stage and native ledger,
    # not public ENABLE/proof admission. No verifier is replaced by a stub.
    from live.execution.demo_issuer import _issue
    from services.validation import demo_submission

    args, registry, log, anchor = original_history
    plan, policy, evidence = delegated_plan(args, tmp_path, 2, registry, anchor)
    grant = _issue(
        plan,
        policy=policy,
        registry=registry,
        key=case[0],
        store=log,
        now_ms=NOW + 11,
        clock_ms=None,
        delegation=evidence,
    )
    assert demo_submission.verify(
        grant["envelope"], registry=registry, expected_context=plan["context"], orders=plan["orders"], now_ms=NOW + 11
    )["signature_verified"]
    assert (
        grant["envelope"]["payload"]["context"]["delegated_admission_sha256"] == evidence["delegated_admission_sha256"]
    )
    assert [row["kind"] for row in log.snapshot()] == ["RESERVED", "RESERVED", "SIGNED"]
    with closing(IssuerStore(log.path)) as reopened:
        assert reopened.snapshot() == log.snapshot()
    changed = deepcopy(grant["envelope"])
    changed["payload"]["context"]["delegated_admission_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        demo_submission.verify(
            changed,
            registry=registry,
            expected_context=changed["payload"]["context"],
            orders=plan["orders"],
            now_ms=NOW + 11,
        )


def test_refresh_failure_after_reserve_never_signs_or_refunds(original_history, case, tmp_path, monkeypatch):
    from live.execution import demo_issuer as issuer

    args, registry, log, anchor = original_history
    plan, policy, evidence = delegated_plan(args, tmp_path, 2, registry, anchor)
    monkeypatch.setattr(issuer, "_sign", lambda *a: pytest.fail("signing after failed independent refresh"))

    def revoked():
        assert len(log.snapshot()) == 2
        raise ValueError("synthetic enabled material revoked")

    with pytest.raises(ValueError, match="revoked"):
        issuer._issue(
            plan,
            policy=policy,
            registry=registry,
            key=case[0],
            store=log,
            now_ms=NOW + 11,
            clock_ms=None,
            delegation=evidence,
            before_sign=revoked,
        )
    with closing(IssuerStore(log.path)) as reopened:
        rows = reopened.snapshot()
        assert len(rows) == 2 and all(row["kind"] == "RESERVED" for row in rows)
        with pytest.raises(ValueError):
            issuer._issue(
                plan,
                policy=policy,
                registry=registry,
                key=case[0],
                store=reopened,
                now_ms=NOW + 11,
                clock_ms=None,
                delegation=evidence,
            )
        assert reopened.snapshot() == rows


def native_issuer_case(case, original_evidence, tmp_path, monkeypatch, *,
        configure_policy=None, configure_registry=None, prepare_episode=None, source_commit="c" * 40):
    """Native proof plus independent original mandate/risk declarations.

    The synthetic authority sets its own limits before signing the declaration;
    no production reviewer or verifier is replaced. Only custody/UID sources are
    isolated by the inherited fixtures. This helper materializes the exact new
    proposal wire after the original proposal builder has passed every gate.
    """
    from dataclasses import asdict, replace
    from live.execution import demo_authority, demo_pre_submit
    from live.execution.demo_batch_risk import _json_value
    from tests.live.test_gold_delegated_admission import native_case
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger

    values = case[1]
    at = original_evidence["entry_ms"] + 1
    mandate = replace(values["mandate"], issued_at=at - 1000, expires_at=at + 86400000)
    attestations = {sid: replace(value, attested_at=at - 1000) for sid, value in values["attestations"].items()}
    risk = deepcopy(values["risk_inputs"])
    risk["native_limits"] = replace(risk["native_limits"], default_max_position_qty=1, fat_finger_qty=1)

    def configure_original_policy(policy):
        policy["scope"]["max_quantity_per_order"] = "1"
        policy["budget_group"]["max_grants"] = 3
        policy["bindings"].update(
            mandate_sha256=_digest(_json_value(asdict(mandate))),
            validation_sha256=_digest(_json_value({sid: asdict(value) for sid, value in attestations.items()})),
            native_limits_sha256=_digest(_json_value(asdict(risk["native_limits"]))),
        )
        if configure_policy is not None:
            configure_policy(policy)

    f = native_case(case, original_evidence, tmp_path, monkeypatch,
        configure_policy=configure_original_policy, source_commit=source_commit,
        **({'configure_registry': configure_registry} if configure_registry is not None else {}),
        **({'prepare_episode': prepare_episode} if prepare_episode is not None else {}))
    monkeypatch.setattr(demo_authority, "load_registry_for_executor", lambda: f.registry)
    decision = f.proof["result"]["decision"]
    origin = f.proof["origin"]
    data = {row["kind"]: row["data"] for row in origin["reads"]}
    risk["now_ms"] = at
    risk["market"].update(
        reference_price=data["mark"]["markPrice"],
        daily_volume_base="100000",
        observed_at_ms=min(data["mark"]["time"], data["book"]["time"]),
    )
    native = (
        DemoDispatchStore(f.root / "dispatch.db"),
        SqliteOrderRecoveryStore(str(f.root / "recovery.db")),
        LiveLedger(str(f.root / "oms.db")),
    )
    try:
        proposal = demo_pre_submit.proposal(
            orders=decision["draft"]["orders"],
            origin=origin,
            risk_inputs=risk,
            mandate=mandate,
            attestations=attestations,
            now_ms=at,
            execution_constraints=decision["execution_constraints"],
            **dict(zip(("dispatch", "recovery", "oms"), native)),
        )
    finally:
        for value in native:
            value.close()
    proposal.update(schema="DEMO_GOLD_DELEGATED_PRE_SUBMISSION_PROPOSAL_V1", delegated_admission=f.proof)
    proposal["context"]["delegated_admission_sha256"] = _digest(f.proof)
    f.handoff = dict(
        schema="DEMO_EXECUTION_SESSION_PROPOSAL_V1",
        proposal=proposal,
        proposal_sha256=_digest(proposal),
        proposal_path="/isolated/executor/proposal.json",
        status="WAITING_INDEPENDENT_GRANT",
        submission_enabled=False,
    )
    f.ledger = tmp_path / "issuance.sqlite3"
    f.proposal_values = dict(
        orders=decision["draft"]["orders"],
        origin=origin,
        risk_inputs=risk,
        mandate=mandate,
        attestations=attestations,
        now_ms=at,
        execution_constraints=decision["execution_constraints"],
    )
    return f


@pytest.fixture
def native_issuer(case, original_evidence, tmp_path, monkeypatch):
    return native_issuer_case(case, original_evidence, tmp_path, monkeypatch)


def test_native_proof_risk_review_reserve_and_signature(native_issuer):
    from live.execution import demo_issuer as issuer
    from live.execution.demo_issuer_policy import review_submission
    from services.validation import demo_submission

    f = native_issuer
    with closing(IssuerStore(f.ledger)) as log:
        before = log.snapshot()
        with pytest.raises(ValueError):
            review_submission(f.handoff, policy=f.base_policy, registry=f.registry, now_ms=f.now_ms)
        grant = issuer.issue_delegated_submission(
            f.handoff,
            policy=f.base_policy,
            registry=f.registry,
            public_material=f.enable,
            key=f.private,
            store=log,
            now_ms=f.now_ms,
        )
        assert demo_submission.verify(
            grant["envelope"],
            registry=f.registry,
            expected_context=f.handoff["proposal"]["context"],
            orders=f.handoff["proposal"]["orders"],
            now_ms=f.now_ms,
        )["signature_verified"]
        rows = log.snapshot()
        assert rows[: len(before)] == before
        assert [row["kind"] for row in rows] == ["RESERVED", "RESERVED", "SIGNED"]
        assert (
            rows[1]["payload"]["delegation"]["enable_issuer_anchor"] == f.enable["original_binding"]["issuer_current"]
        )
        assert "delegated_admission" not in rows[1]["payload"]
        with pytest.raises(ValueError):
            issuer.issue_delegated_submission(
                f.handoff,
                policy=f.base_policy,
                registry=f.registry,
                public_material=f.enable,
                key=f.private,
                store=log,
                now_ms=f.now_ms,
            )
        assert log.snapshot() == rows


def test_native_changed_proof_policy_and_exact_risk_reject_before_reservation(native_issuer, monkeypatch):
    from live.execution import demo_issuer as issuer

    f = native_issuer
    monkeypatch.setattr(issuer, "_sign", lambda *a: pytest.fail("invalid admission reached signer"))
    with closing(IssuerStore(f.ledger)) as log:
        before = log.snapshot()
        for damage in ("enable", "signature", "proof", "constraints", "orders", "risk", "mandate", "policy", "expired"):
            handoff = deepcopy(f.handoff)
            enabled = deepcopy(f.enable)
            policy = deepcopy(f.base_policy)
            proposal = handoff["proposal"]
            at = f.now_ms
            if damage == "enable":
                enabled["consumer_enabled"] = False
            elif damage == "signature":
                enabled["delegation"]["signature_base64url"] = "A" * 86
            elif damage == "proof":
                proposal["delegated_admission"]["result"]["decision"]["status"] = "NOT_AN_APPROVAL"
                proposal["context"]["delegated_admission_sha256"] = _digest(proposal["delegated_admission"])
            elif damage == "constraints":
                proposal["execution_constraints"]["valid_until_ms"] += 1
                proposal["context"]["execution_constraints_sha256"] = _digest(proposal["execution_constraints"])
            elif damage == "orders":
                proposal["orders"][0]["quantity"] = "0.2"
            elif damage == "risk":
                proposal["risk"]["checks"][0]["notional"] = "1"
            elif damage == "mandate":
                proposal["mandate"]["approved_by"] = ["AUTO"]
            elif damage == "policy":
                policy["scope"]["max_quantity_per_order"] = "2"
            else:
                at = proposal["execution_constraints"]["valid_until_ms"]
            handoff["proposal_sha256"] = _digest(proposal)
            with pytest.raises(ValueError):
                issuer.issue_delegated_submission(
                    handoff,
                    policy=policy,
                    registry=f.registry,
                    public_material=enabled,
                    key=f.private,
                    store=log,
                    now_ms=at,
                )
            assert log.snapshot() == before, damage


@pytest.mark.parametrize(
    "change", ["enable_revoked", "registry_revoked", "policy_changed", "expired_after_replay", "crash"]
)
def test_native_post_reservation_change_never_refunds_original_budget(native_issuer, monkeypatch, change):
    from live.execution import demo_issuer as issuer

    f = native_issuer
    signatures = []

    def signer(*args):
        signatures.append(1)
        if change == "crash":
            raise RuntimeError("synthetic signer interruption")
        pytest.fail("changed runtime authority reached signer")

    monkeypatch.setattr(issuer, "_sign", signer)
    ticks = iter([f.now_ms, f.handoff["proposal"]["execution_constraints"]["valid_until_ms"]])

    def refresh():
        policy, registry, enabled = deepcopy((f.base_policy, f.registry, f.enable))
        if change == "enable_revoked":
            enabled["consumer_enabled"] = False
        elif change == "registry_revoked":
            registry["keys"][0]["status"] = "REVOKED"
        elif change == "policy_changed":
            policy["max_grants"] += 1
        return policy, registry, enabled

    with closing(IssuerStore(f.ledger)) as log:
        with pytest.raises((ValueError, RuntimeError)):
            issuer.issue_delegated_submission(
                f.handoff,
                policy=f.base_policy,
                registry=f.registry,
                public_material=f.enable,
                key=f.private,
                store=log,
                now_ms=f.now_ms,
                clock_ms=(lambda: next(ticks)) if change == "expired_after_replay" else None,
                refresh=refresh,
            )
        rows = log.snapshot()
        assert len(rows) == 2 and all(row["kind"] == "RESERVED" for row in rows)
    with closing(IssuerStore(f.ledger)) as restarted:
        with pytest.raises(ValueError):
            issuer.issue_delegated_submission(
                f.handoff,
                policy=f.base_policy,
                registry=f.registry,
                public_material=f.enable,
                key=f.private,
                store=restarted,
                now_ms=f.now_ms,
            )
        assert restarted.snapshot() == rows
    assert signatures == ([1] if change == "crash" else [])


def test_native_valid_risk_cannot_substitute_another_origin_for_admitted_proof(native_issuer):
    from live.execution import demo_issuer as issuer
    from live.execution.demo_gold_delegated_admission import admit
    from live.execution.demo_issuer_policy import _review_submission

    f = native_issuer
    handoff = deepcopy(f.handoff)
    proposal = handoff["proposal"]
    proposal["origin"]["source_sha256"] = "f" * 64
    proposal["context"]["origin_sha256"] = _digest(proposal["origin"])
    handoff["proposal_sha256"] = _digest(proposal)
    admitted = admit(
        delegation=f.delegation,
        enable=f.enable,
        base_policy=f.base_policy,
        registry=f.registry,
        proof=f.proof,
        now_ms=f.now_ms,
    )
    # Both independent components are individually valid; their cross-binding
    # must still prohibit combining different original account capture bytes.
    _review_submission(
        handoff, policy=admitted["effective_policy"], registry=f.registry, now_ms=f.now_ms, delegated=True
    )
    with closing(IssuerStore(f.ledger)) as log:
        before = log.snapshot()
        with pytest.raises(ValueError, match="ISSUER_DELEGATED_SNAPSHOT"):
            issuer.issue_delegated_submission(
                handoff,
                policy=f.base_policy,
                registry=f.registry,
                public_material=f.enable,
                key=f.private,
                store=log,
                now_ms=f.now_ms,
            )
        assert log.snapshot() == before


def test_native_filled_opening_then_delegated_stop_uses_original_exit_reserve(
    case, original_evidence, tmp_path, monkeypatch
):
    import os
    from live.execution import demo_issuer as issuer, demo_pre_submit, demo_position_context as position
    from tests.live.test_gold_delegated_admission import completed_bar_entry, retain_exit
    from tools.gold_delegated_proof import export
    from services.validation import demo_submission

    f = completed_bar_entry(case, original_evidence, tmp_path, monkeypatch)
    try:
        at = f.now_ms + 2000
        result = retain_exit(f, at=at, price="90")
        proof = export(
            root=f.root,
            runtime=f.proof["runtime"],
            result_sha256=_digest(result),
            enable=f.enable,
            registry=f.registry,
            source_commit="c" * 40,
            uid=os.getuid(),
            now_ms=at,
            stores=f.stores,
        )
        values = deepcopy(f.proposal_values)
        origin = proof["origin"]
        risk = values["risk_inputs"]
        risk["account"].update(position.validate_origin(origin, proof["execution"]["context"], as_of_ms=at))
        data = {row["kind"]: row["data"] for row in origin["reads"]}
        risk["market"].update(
            reference_price=data["mark"]["markPrice"], observed_at_ms=min(data["mark"]["time"], data["book"]["time"])
        )
        risk["now_ms"] = at
        values.update(
            orders=result["decision"]["draft"]["orders"],
            origin=origin,
            now_ms=at,
            execution_constraints=result["decision"]["execution_constraints"],
        )
        proposal = demo_pre_submit.proposal(**values, **f.stores)
        proposal.update(schema="DEMO_GOLD_DELEGATED_PRE_SUBMISSION_PROPOSAL_V1", delegated_admission=proof)
        proposal["context"]["delegated_admission_sha256"] = _digest(proof)
        handoff = dict(f.handoff, proposal=proposal, proposal_sha256=_digest(proposal))
        with closing(IssuerStore(f.ledger)) as log:
            before = log.snapshot()
            assert [row["kind"] for row in before] == ["RESERVED", "RESERVED", "SIGNED"]
            grant = issuer.issue_delegated_submission(
                handoff,
                policy=f.base_policy,
                registry=f.registry,
                public_material=f.enable,
                key=f.private,
                store=log,
                now_ms=at,
            )
            assert demo_submission.verify(
                grant["envelope"],
                registry=f.registry,
                expected_context=proposal["context"],
                orders=proposal["orders"],
                now_ms=at,
            )["signature_verified"]
            rows = log.snapshot()
            assert rows[: len(before)] == before
            assert [row["kind"] for row in rows] == ["RESERVED", "RESERVED", "SIGNED", "RESERVED", "SIGNED"]
            assert rows[-2]["payload"]["plan"]["orders"][0]["reduceOnly"] is True
        with closing(IssuerStore(f.ledger)) as restarted:
            assert restarted.snapshot() == rows
            with pytest.raises(ValueError):
                issuer.issue_delegated_submission(
                    handoff,
                    policy=f.base_policy,
                    registry=f.registry,
                    public_material=f.enable,
                    key=f.private,
                    store=restarted,
                    now_ms=at,
                )
            assert restarted.snapshot() == rows
    finally:
        f.events.close()
        f.stream.__exit__(None, None, None)
        for store in f.stores.values():
            store.close()
