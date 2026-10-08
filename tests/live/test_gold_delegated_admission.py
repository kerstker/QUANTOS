"""Native archived bar/cycle/pilot/retention with synthetic independent authority."""

from copy import deepcopy
from contextlib import closing
import json
import os
import shutil
from types import SimpleNamespace
import pytest
from live.execution.demo_batch_risk import _digest
from live.execution import demo_gold_delegation_enable as enabling
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence


def native_case(case, original_evidence, tmp_path, monkeypatch, *, configure_policy=None, configure_registry=None, prepare_episode=None, source_commit="c" * 40):
    """No admission/risk/replay verifier stubs; custody paths and input bytes are synthetic."""
    from live.execution import demo_position_context as position, demo_authority
    from live.execution import demo_market_partition_runtime as partition
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution.demo_issuer_policy import budget_group_binding
    from live.execution.demo_issuer_store import IssuerStore
    from services.validation.demo_submission import registry_sha256
    from tests.live.test_demo_pre_submit import stores
    from tests.live.test_gold_execution_delegation import fixture, sign
    from tests.tools.test_gold_strategy_entry import origin_at
    from tests.tools.test_gold_pilot_capacity import write
    from tools.gold_bar_worker_inputs import load
    from tools.gold_bar_demo_cycle import prepare
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_delegated_proof import freeze_prefix
    from tools.gold_strategy_worker import original_pilot_window

    root = tmp_path / "executor"
    shutil.copytree(original_evidence["root"], root)
    runtime = dict(deepcopy(original_evidence["retained"]), source_commit=source_commit)
    at = original_evidence["entry_ms"] + 1
    start = at - (100 if prepare_episode is None else 10000)
    monkeypatch.setattr(position, "_ROOT", root)

    def custody(path, *, directory):
        from tools.demo_resident_recovery import _check_path

        path.relative_to(root)
        _check_path(path, os.getuid(), directory=directory)

    monkeypatch.setattr(position, "_custody", custody)
    monkeypatch.setattr(demo_authority, "load_registry_for_executor", lambda: {"executor_uid": os.getuid()})
    monkeypatch.setattr(partition, "EXECUTOR_RECORD", tmp_path / "partition-executor.json")
    monkeypatch.setattr(partition, "ISSUER_RECORD", tmp_path / "partition-issuer.json")
    monkeypatch.setattr(partition, "ACTIVATION", tmp_path / "partition-activation.json")
    origin = origin_at(at)
    request = dict(
        schema="GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1",
        source_commit=source_commit,
        account_ref_sha256=origin["account_ref_sha256"],
        duration_seconds=86400,
        max_loss_fraction="0.05",
        submission_enabled=False,
    )
    write(root / "gold-pilot-request.json", request)
    native = stores(root)
    try:
        config = enrollment.enroll_requested(
            root=root,
            source_commit=source_commit,
            origin=origin_at(start),
            **dict(zip(("dispatch", "recovery", "oms"), native)),
            now_ms=start,
        )
        if prepare_episode is None:
            engine, _ = load(root, runtime, source_commit=source_commit, uid=os.getuid())
            entry_ms = runtime["entry_decision_ms"]
        else:
            engine, entry_ms, runtime = prepare_episode(root=root, stores=native, runtime=runtime, now_ms=at)
        continuation = None
        if prepare_episode is not None:
            from tools.gold_bar_episode_tail import EpisodeTail
            engine, continuation, _ = EpisodeTail(root, bundle_sha256=_digest(runtime),
                engine=engine, entry_decision_ms=entry_ms).snapshot()
        definition = engine.verified_definition()
        decision = prepare(
            engine=engine,
            entry_decision_ms=entry_ms,
            origin=origin,
            **dict(zip(("dispatch", "recovery", "oms"), native)),
            now_ms=at,
        )
        from live.execution.demo_pre_submit import _state

        execution = dict(state=_state(*native), context=None, exit_trigger=None, trigger_evidence=None)
    finally:
        for value in native:
            value.close()
    assert decision["status"] == "DECISION_REQUIRES_APPROVAL"
    decision.update(
        source_commit=source_commit,
        bundle_sha256=_digest(runtime),
        account_ref_sha256=origin["account_ref_sha256"],
        origin_sha256=_digest(origin),
    )
    if continuation is not None:
        decision["bar_continuation_sha256"] = continuation
    original = original_context(root, runtime, uid=os.getuid(), account=origin["account_ref_sha256"])
    with Retention(root, original=original, base_runtime=runtime, uid=os.getuid(), role="executor") as retained:
        claim = retained.claim(bundle=runtime, now_ms=at)
        retained.put(claim, "origin", origin)
        retained.put(claim, "decision", decision)
        result = dict(
            schema="GOLD_STRATEGY_WORKER_RESULT_V1",
            status="DECISION_RETAINED",
            source_commit=source_commit,
            bundle_sha256=_digest(runtime),
            decision_sha256=_digest(decision),
            decision=decision,
            submission_enabled=False,
            economic_authority=False,
            original_pilot_window=original_pilot_window(
                root, decision=decision, source_commit=source_commit, account=origin["account_ref_sha256"]
            ),
        )
        retained.put(claim, "result", result)
        capacity = deepcopy(retained.binding)
    contract, private, registry, delegation = fixture(case)

    def shifted(value):
        if type(value) is int and NOW - 86400000 <= value <= NOW + 2 * 86400000:
            return value + start - NOW
        if type(value) is dict:
            return {k: shifted(v) for k, v in value.items()}
        if type(value) is list:
            return [shifted(v) for v in value]
        return value

    registry = shifted(registry)
    delegation = shifted(delegation)
    policy = delegation["policy_template"]
    policy["executor_release_commit"] = source_commit
    if configure_registry is not None:
        configure_registry(registry)
    if configure_policy is not None:
        configure_policy(policy)
    policy["registry_sha256"] = registry_sha256(registry)
    policy["budget_group"]["strategy_definition_sha256"] = definition["definition_hash"]
    delegation["terms"].update(
        runtime_sha256=_digest(runtime),
        strategy_source_commit=runtime["archive_source_commit"],
        trial_manifest_sha256=runtime["trial_manifest_sha256"],
        pilot_request_sha256=_digest(request),
        pilot_configuration=config,
        confirmed_at_ms=start + 1,
        original_budget_group_binding_sha256=budget_group_binding(policy),
    )
    sign(contract, private, delegation)
    log = IssuerStore(tmp_path / "issuance.sqlite3", create=True)
    try:
        context = deepcopy(case[2]["proposal"]["context"])
        for key in ("deployment_sha256", "registry_sha256", "account_ref_sha256"):
            context[key] = policy[key]
        context["execution_constraints_sha256"] = policy["bindings"]["execution_constraints_sha256"]
        plan = dict(
            purpose="submission",
            proposal_sha256="d" * 64,
            context=context,
            orders=deepcopy(case[1]["orders"]),
            commands=[],
            issued_at_ms=start + 2,
            expires_at_ms=start + 50,
            notional="120",
        )
        log.reserve(plan, policy=policy, registry=registry)
        rows = log.snapshot()
    finally:
        log.close()
    anchor = dict(
        head_sha256=rows[-1]["record_hash"],
        records=len(rows),
        history_sha256=_digest(rows),
        group_binding_sha256=budget_group_binding(policy),
        group_consumed=dict(
            submission=dict(grants=1, orders=len(plan["orders"]), notional="120"),
            cancellation=dict(grants=0, orders=0, notional="0"),
        ),
        reserved=1,
        unsigned_reservations=1,
    )
    enabling.verify_history_prefix(anchor, rows, policy=policy)
    enable = dict(
        schema=enabling.SCHEMA,
        delegation=delegation,
        runtime=runtime,
        definition=definition,
        registration_sha256="a" * 64,
        registration_candidate_sha256="b" * 64,
        issuer_anchor=anchor,
        original_binding=dict(
            registry_sha256=_digest(registry),
            policy_sha256=_digest(policy),
            profile_sha256="c" * 64,
            dispatcher_sha256="c" * 64,
            runtime_sha256=_digest(runtime),
            definition_sha256=_digest(definition),
            original_setup_sha256="d" * 64,
            original_spec_sha256="e" * 64,
            executor_ledger_state_hash="f" * 64,
            issuer_current=deepcopy(anchor),
        ),
        consumer_enabled=True,
        submission_enabled=False,
        economic_authority=False,
    )
    enable["operator_confirmation"] = dict(
        schema="GOLD_EXECUTION_DELEGATION_ENABLE_APPROVAL_V1",
        purpose="ENABLE_BOUNDED_FUTURE_SIGNALS",
        candidate_sha256=_digest(enabling.candidate(enable)),
        operator_id="isolated-independent-test-operator",
        approved_at_ms=start + 3,
        consumer_enabled=True,
        submission_enabled=False,
        economic_authority=False,
    )
    enabling.validate(
        enable, registry=registry, source_commit=source_commit, account_ref_sha256=origin["account_ref_sha256"], now_ms=at
    )
    pilot = dict(
        request=request,
        configuration=config,
        receipt=json.loads((root / "gold-pilot/enrollment.json").read_bytes()),
        intent=json.loads((root / "gold-pilot/enrollment-intent.json").read_bytes()),
        stop=None,
    )
    proof = dict(
        schema="DEMO_GOLD_DELEGATED_DECISION_PROOF_V1",
        source_commit=source_commit,
        enable_sha256=_digest(enable),
        delegation_sha256=_digest(delegation),
        runtime=runtime,
        result=result,
        origin=origin,
        claim=claim,
        capacity=capacity,
        pilot=pilot,
        execution=execution,
        prefix=freeze_prefix(root=root, runtime=runtime, decision=decision, source_commit=source_commit, uid=os.getuid()),
        submission_enabled=False,
        economic_authority=False,
    )
    return SimpleNamespace(
        proof=proof,
        enable=enable,
        registry=registry,
        base_policy=policy,
        delegation=delegation,
        now_ms=at,
        root=root,
        private=private,
        history=rows,
        plan=plan,
    )


@pytest.fixture
def admitted_case(case, original_evidence, tmp_path, monkeypatch):
    return native_case(case, original_evidence, tmp_path, monkeypatch)


def test_native_original_entry_independently_admitted_without_budget_rebind(admitted_case):
    from live.execution.demo_gold_delegated_admission import admit

    f = admitted_case
    result = admit(
        delegation=f.delegation,
        enable=f.enable,
        base_policy=f.base_policy,
        registry=f.registry,
        proof=f.proof,
        now_ms=f.now_ms,
    )
    assert result["orders"] == f.proof["result"]["decision"]["draft"]["orders"]
    assert result["constraints"] == f.proof["result"]["decision"]["execution_constraints"]
    expected = deepcopy(f.base_policy)
    expected["policy_id"] = "gold-delegated-" + _digest(f.proof)
    expected["bindings"]["execution_constraints_sha256"] = _digest(result["constraints"])
    assert result["effective_policy"] == expected
    assert result["valid_until_ms"] == f.proof["result"]["decision"]["draft"]["valid_until_ms"]
    assert result["history_anchor"] == f.enable["issuer_anchor"]


def test_proof_cannot_change_rules_original_window_orders_or_wait_past_deadline(admitted_case):
    from live.execution.demo_gold_delegated_admission import admit

    f = admitted_case
    for damage in (
        "orders",
        "signal",
        "origin",
        "pilot",
        "stopped",
        "claim",
        "history",
        "runtime",
        "enable",
        "authority",
        "source",
        "future",
        "expired",
        "prefix",
        "state",
    ):
        proof = deepcopy(f.proof)
        at = f.now_ms
        decision = proof["result"]["decision"]
        if damage == "orders":
            decision["draft"]["orders"][0]["quantity"] = "0.2"
            decision["draft"]["result_hash"] = _digest(
                {k: v for k, v in decision["draft"].items() if k != "result_hash"}
            )
        elif damage == "signal":
            decision["bar_signal_sha256"] = "a" * 64
        elif damage == "origin":
            proof["origin"]["account_ref_sha256"] = "a" * 64
        elif damage == "pilot":
            proof["pilot"]["configuration"]["ends_at_ms"] += 1000
        elif damage == "stopped":
            proof["pilot"]["stop"] = {}
        elif damage == "claim":
            proof["claim"]["ordinal"] = 0
        elif damage == "history":
            proof["execution"]["state"]["reservations"].append({})
        elif damage == "runtime":
            proof["runtime"]["entry_decision_ms"] += 1
        elif damage == "enable":
            proof["enable_sha256"] = "a" * 64
        elif damage == "authority":
            proof["submission_enabled"] = True
        elif damage == "source":
            proof["source_commit"] = "a" * 40
        elif damage == "future":
            decision["observed_at_ms"] = at + 1
        elif damage == "expired":
            at = decision["draft"]["valid_until_ms"]
        elif damage == "prefix":
            proof["prefix"]["files"].pop("journal/messages.jsonl")
        else:
            decision["execution_state_sha256"] = "a" * 64
        proof["result"]["decision_sha256"] = _digest(decision)
        with pytest.raises((ValueError, KeyError, TypeError), match=None):
            admit(
                delegation=f.delegation,
                enable=f.enable,
                base_policy=f.base_policy,
                registry=f.registry,
                proof=proof,
                now_ms=at,
            )


def completed_bar_entry(case, original_evidence, tmp_path, monkeypatch, *, configure_policy=None, prepare_episode=None, source_commit="c" * 40):
    """Real exact entry signature, mock wire, native receipts and owned V2 basis."""
    from tests.live.test_demo_gold_delegated_issuer import native_issuer_case
    from live.execution import demo_authority, demo_issuer, demo_pre_submit, demo_prepared_execution as executor
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from live.execution.demo_issuer_store import IssuerStore
    from live.exchange import binance_demo_exact as transport
    from services.research.demo_user_stream import UserStreamJournal, normalize_message
    from live.execution.order_event_store import SqliteOrderEventStore
    from tests.research.test_binance_order_receipts import message
    from live.execution.demo_wire_protocol import outcome_for_query
    from live.execution import demo_oms_reconciliation as reconciliation, demo_position_context as position
    from services.validation.demo_submission import GOLD_BATCH_SCHEMA

    f = native_issuer_case(
        case,
        original_evidence,
        tmp_path,
        monkeypatch,
        source_commit=source_commit,
        **({"configure_policy": configure_policy} if configure_policy is not None else {}),
        **({"prepare_episode": prepare_episode} if prepare_episode is not None else {}),
    )
    at = f.now_ms
    native = dict(
        dispatch=DemoDispatchStore(f.root / "dispatch.db"),
        recovery=SqliteOrderRecoveryStore(str(f.root / "recovery.db")),
        oms=LiveLedger(str(f.root / "oms.db")),
    )
    f.stores = native
    # The initial exact signed policy remains a distinct independent approval.
    policy = deepcopy(f.base_policy)
    policy["policy_id"] = "isolated-original-exact-entry"
    policy["bindings"]["execution_constraints_sha256"] = _digest(f.proof["result"]["decision"]["execution_constraints"])
    p = demo_pre_submit.proposal(**f.proposal_values, **native)
    handoff = dict(
        schema="DEMO_EXECUTION_SESSION_PROPOSAL_V1",
        proposal=p,
        proposal_sha256=_digest(p),
        proposal_path="/isolated/original-entry.json",
        status="WAITING_INDEPENDENT_GRANT",
        submission_enabled=False,
    )
    with closing(IssuerStore(f.ledger)) as log:
        grant = demo_issuer.issue_submission(
            handoff, policy=policy, registry=f.registry, key=f.private, store=log, now_ms=at + 1
        )
    monkeypatch.setattr(demo_authority, "_read_root_owned_json", lambda path: json.dumps(f.registry).encode())
    monkeypatch.setattr(executor, "_require_state_custody", lambda *a, **kw: None)
    monkeypatch.setattr(executor, "now_ms", lambda: at + 2)
    calls = []
    monkeypatch.setattr(transport, "_credentials", lambda _: ("synthetic-key", "synthetic-secret"))
    order = f.proposal_values["orders"][0]
    monkeypatch.setattr(
        transport,
        "_http_once",
        lambda *a: (
            calls.append(1) or 200,
            json.dumps(dict(symbol="XAUUSDT", clientOrderId=order["newClientOrderId"], orderId=123)).encode(),
        ),
    )
    batch = demo_pre_submit.prepare(
        json.dumps(grant["envelope"]).encode(),
        **f.proposal_values,
        **native,
        evidence_directory=f.root / "evidence",
        verification_now_ms=at + 1,
    )
    executor.execute(
        batch,
        **{
            k: v
            for k, v in f.proposal_values.items()
            if k not in ("orders", "origin", "now_ms", "execution_constraints")
        },
        **native,
    )
    assert calls == [1]
    stream = UserStreamJournal(f.root / "gold-stream.db", account_ref_sha256=f.base_policy["account_ref_sha256"])
    events = SqliteOrderEventStore(str(f.root / "gold-events.db"), create=True)
    f.stream = stream
    f.events = events
    stream.start_session(source_sha256="a" * 64, at_ms=at)
    fill = message(at + 13, "FILLED", "0.1", "0.1", trade_id=7, fee="0.0045045", asset="USDT")
    fill["o"].update(
        s="XAUUSDT",
        c=order["newClientOrderId"],
        i=123,
        q="0.1",
        p="100.1",
        L="100.1",
        f="IOC",
        m=False,
        R=False,
        er="0",
        rp="0",
    )
    stream.record("MESSAGE", at_ms=at + 14, message=normalize_message(json.dumps(fill)))
    query = dict(
        order,
        clientOrderId=order["newClientOrderId"],
        orderId=123,
        origQty="0.1",
        status="FILLED",
        executedQty="0.1",
        time=at + 2,
        updateTime=at + 13,
    )
    for key in ("newClientOrderId", "newOrderRespType", "quantity"):
        query.pop(key)
    native["dispatch"].record_query_result(
        reservation_sha256=batch["reservation_sha256"],
        client_order_id=order["newClientOrderId"],
        requested_at_ms=at + 30,
        outcome=outcome_for_query(order, 200, json.dumps(query).encode(), schema=GOLD_BATCH_SCHEMA),
        now_ms=at + 31,
    )
    area = f.root / "receipt-reconciliation"
    plan = reconciliation.prepare_resolution(
        batch,
        dispatch=native["dispatch"],
        stream=stream,
        start_ms=at + 1,
        end_ms=at + 1000,
        source_commit=source_commit,
        output_directory=area,
        as_of_ms=at + 1001,
    )
    assert plan["status"] == "READY_FOR_OMS_PROJECTION"
    outcome = reconciliation._project_resolution(plan, prepared=batch, **native, event_store=events, now_ms=at + 1001)
    demo_pre_submit._retain(area / "outcomes", outcome)
    f.context = position.load_context(
        demo_pre_submit._state(**native),
        symbol="XAUUSDT",
        account_ref=f.base_policy["account_ref_sha256"],
        as_of_ms=at + 2000,
    )
    assert f.context["quantity"] == "0.1" and f.context["canonical_wallet"] == "4999.9954955"
    assert f.context["position_episode"]["client_order_id"] == order["newClientOrderId"]
    return f


def held_bar_origin(f, at, *, price="90"):
    from decimal import Decimal
    from tests.tools.test_gold_strategy_entry import origin_at
    from services.research.demo_account_baseline import normalize_response

    origin = origin_at(at)
    wallet = f.context["canonical_wallet"]
    quantity = Decimal(f.context["quantity"])
    pnl = quantity * (Decimal(price) - Decimal(f.context["average_entry_price"]))
    margin = str(Decimal(wallet) + pnl)
    for row in origin["reads"]:
        kind, data = row["kind"], row["data"]
        if kind == "book":
            data.update(time=at, bidPrice=price, askPrice=str(Decimal(price) + Decimal(".1")))
        elif kind == "mark":
            data.update(time=at, markPrice=price, indexPrice=price)
        elif kind.startswith("account"):
            asset = next(a for a in data["assets"] if a["asset"] == "USDT")
            asset.update(
                walletBalance=wallet,
                crossWalletBalance=wallet,
                availableBalance=wallet,
                unrealizedProfit=str(pnl),
                marginBalance=margin,
            )
            data["positions"].append(dict(symbol="XAUUSDT", positionSide="BOTH", positionAmt=str(quantity)))
            row["data"] = normalize_response(kind, data)
        elif kind == "positions":
            data.append(
                dict(
                    symbol="XAUUSDT",
                    positionSide="BOTH",
                    positionAmt=str(quantity),
                    entryPrice=f.context["average_entry_price"],
                    markPrice=price,
                    unRealizedProfit=str(pnl),
                    isolatedWallet="0",
                    marginAsset="USDT",
                    updateTime=f.context["position_episode"]["opened_venue_at_ms"],
                )
            )
            row["data"] = normalize_response(kind, data)
    return origin


def retain_exit(f, *, at, price="90"):
    from tools.gold_bar_worker_inputs import load
    from tools.gold_bar_demo_cycle import prepare
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention

    runtime = f.proof["runtime"]
    origin = held_bar_origin(f, at, price=price)
    engine, _ = load(f.root, runtime, source_commit=runtime["source_commit"], uid=os.getuid())
    decision = prepare(
        engine=engine, entry_decision_ms=runtime["entry_decision_ms"], origin=origin, **f.stores, now_ms=at
    )
    assert decision["status"] == "DECISION_REQUIRES_APPROVAL" and decision["action"] == "REDUCE_LONG"
    decision.update(
        source_commit=runtime["source_commit"],
        bundle_sha256=_digest(runtime),
        account_ref_sha256=origin["account_ref_sha256"],
        origin_sha256=_digest(origin),
    )
    original = original_context(f.root, runtime, uid=os.getuid(), account=origin["account_ref_sha256"])
    with Retention(f.root, original=original, base_runtime=runtime, uid=os.getuid(), role="executor") as store:
        claim = store.claim(bundle=runtime, now_ms=at)
        store.put(claim, "origin", origin)
        store.put(claim, "decision", decision)
        result = dict(f.proof["result"], decision=decision, decision_sha256=_digest(decision))
        store.put(claim, "result", result)
    return result


def test_native_filled_entry_stop_exit_and_restarted_latched_trigger(case, original_evidence, tmp_path, monkeypatch):
    from tools.gold_delegated_proof import export
    from live.execution.demo_gold_delegated_admission import admit

    f = completed_bar_entry(case, original_evidence, tmp_path, monkeypatch)
    try:
        for offset, price in ((2000, "90"), (4000, "100")):
            result = retain_exit(f, at=f.now_ms + offset, price=price)
            proof = export(
                root=f.root,
                runtime=f.proof["runtime"],
                result_sha256=_digest(result),
                enable=f.enable,
                registry=f.registry,
                source_commit="c" * 40,
                uid=os.getuid(),
                now_ms=f.now_ms + offset,
                stores=f.stores,
            )
            accepted = admit(
                delegation=f.delegation,
                enable=f.enable,
                base_policy=f.base_policy,
                registry=f.registry,
                proof=proof,
                now_ms=f.now_ms + offset,
            )
            assert accepted["orders"] == result["decision"]["draft"]["orders"]
            assert accepted["orders"][0]["reduceOnly"] is True
            assert result["decision"]["draft"]["reason"] == "STOP"
            assert proof["execution"]["exit_trigger"]["observed_at_ms"] == f.now_ms + 2000
            if offset == 4000:
                bad = deepcopy(proof)
                bad["execution"]["trigger_evidence"]["origin"] = held_bar_origin(f, f.now_ms + 2000, price="100")
                with pytest.raises(ValueError):
                    admit(
                        delegation=f.delegation,
                        enable=f.enable,
                        base_policy=f.base_policy,
                        registry=f.registry,
                        proof=bad,
                        now_ms=f.now_ms + offset,
                    )
    finally:
        f.events.close()
        f.stream.__exit__(None, None, None)
        for store in f.stores.values():
            store.close()


def exit_proposal_values(f, result):
    from decimal import Decimal
    from live.execution import demo_position_context as position

    decision = result["decision"]
    at = decision["observed_at_ms"]
    origin = held_bar_origin(f, at, price=decision["draft"]["orders"][0]["price"])
    risk = deepcopy(f.proposal_values["risk_inputs"])
    risk["now_ms"] = at
    risk["account"].update(position.validate_origin(origin, f.context, as_of_ms=at))
    risk["account"]["peak_equity"] = str(
        max(
            Decimal(f.context["initial_equity"]),
            Decimal(f.context["peak_equity_floor"]),
            Decimal(risk["account"]["equity"]),
        )
    )
    reads = {row["kind"]: row["data"] for row in origin["reads"]}
    risk["market"].update(
        reference_price=reads["mark"]["markPrice"], observed_at_ms=min(reads["mark"]["time"], reads["book"]["time"])
    )
    return dict(
        f.proposal_values,
        orders=decision["draft"]["orders"],
        origin=origin,
        risk_inputs=risk,
        now_ms=at,
        execution_constraints=decision["execution_constraints"],
    )


def partial_stop_receipts(f, result, monkeypatch):
    """Original exact signed IOC reduction partially fills and terminally expires."""
    from live.execution import demo_pre_submit as prep, demo_issuer, demo_prepared_execution as executor
    from live.execution import demo_position_context as position, demo_oms_reconciliation as reconciliation
    from live.execution.demo_issuer_store import IssuerStore
    from live.exchange import binance_demo_exact as transport
    from tests.research.test_binance_order_receipts import message
    from services.research.demo_user_stream import normalize_message
    from live.execution.demo_wire_protocol import outcome_for_query
    from services.validation.demo_submission import GOLD_BATCH_SCHEMA

    values = exit_proposal_values(f, result)
    at = values["now_ms"]
    order = values["orders"][0]
    proposal = prep.proposal(**values, **f.stores)
    handoff = dict(
        schema="DEMO_EXECUTION_SESSION_PROPOSAL_V1",
        proposal=proposal,
        proposal_sha256=_digest(proposal),
        proposal_path="/isolated/partial-exit.json",
        status="WAITING_INDEPENDENT_GRANT",
        submission_enabled=False,
    )
    policy = deepcopy(f.base_policy)
    policy["policy_id"] = "isolated-exact-partial-exit"
    policy["bindings"]["execution_constraints_sha256"] = _digest(values["execution_constraints"])
    with closing(IssuerStore(f.ledger)) as log:
        grant = demo_issuer.issue_submission(
            handoff, policy=policy, registry=f.registry, key=f.private, store=log, now_ms=at + 1
        )
    batch = prep.prepare(
        json.dumps(grant["envelope"]).encode(),
        **values,
        **f.stores,
        evidence_directory=f.root / "evidence",
        verification_now_ms=at + 1,
    )
    monkeypatch.setattr(executor, "now_ms", lambda: at + 2)
    sent = []
    monkeypatch.setattr(
        transport,
        "_http_once",
        lambda *a: (
            sent.append(1) or 200,
            json.dumps(dict(symbol="XAUUSDT", clientOrderId=order["newClientOrderId"], orderId=124)).encode(),
        ),
    )
    executor.execute(
        batch,
        **{k: v for k, v in values.items() if k not in ("orders", "origin", "now_ms", "execution_constraints")},
        **f.stores,
    )
    assert sent == [1]
    fill = message(at + 13, "PARTIALLY_FILLED", "0.05", "0.05", trade_id=8, fee="0.002025", asset="USDT")
    fill["o"].update(
        s="XAUUSDT",
        c=order["newClientOrderId"],
        i=124,
        q="0.1",
        p="90",
        L="90",
        S="SELL",
        f="IOC",
        m=False,
        R=True,
        er="0",
        rp="-0.505",
    )
    f.stream.record("MESSAGE", at_ms=at + 14, message=normalize_message(json.dumps(fill)))
    expired = message(at + 15, "EXPIRED", "0", "0.05", trade_id=0, asset="USDT")
    expired["o"].update(
        s="XAUUSDT",
        c=order["newClientOrderId"],
        i=124,
        q="0.1",
        p="90",
        L="0",
        S="SELL",
        f="IOC",
        m=False,
        R=True,
        er="0",
        x="EXPIRED",
        rp="0",
    )
    f.stream.record("MESSAGE", at_ms=at + 16, message=normalize_message(json.dumps(expired)))
    query = dict(
        order,
        clientOrderId=order["newClientOrderId"],
        orderId=124,
        origQty="0.1",
        status="EXPIRED",
        executedQty="0.05",
        time=at + 2,
        updateTime=at + 15,
    )
    for key in ("newClientOrderId", "newOrderRespType", "quantity"):
        query.pop(key)
    f.stores["dispatch"].record_query_result(
        reservation_sha256=batch["reservation_sha256"],
        client_order_id=order["newClientOrderId"],
        requested_at_ms=at + 30,
        outcome=outcome_for_query(order, 200, json.dumps(query).encode(), schema=GOLD_BATCH_SCHEMA),
        now_ms=at + 31,
    )
    area = f.root / "receipt-reconciliation"
    plan = reconciliation.prepare_resolution(
        batch,
        dispatch=f.stores["dispatch"],
        stream=f.stream,
        start_ms=at + 1,
        end_ms=at + 1000,
        source_commit="c" * 40,
        output_directory=area,
        as_of_ms=at + 1001,
    )
    assert plan["status"] == "READY_FOR_OMS_PROJECTION", plan
    outcome = reconciliation._project_resolution(
        plan, prepared=batch, **f.stores, event_store=f.events, now_ms=at + 1001
    )
    prep._retain(area / "outcomes", outcome)
    f.context = position.load_context(
        prep._state(**f.stores), symbol="XAUUSDT", account_ref=f.base_policy["account_ref_sha256"], as_of_ms=at + 2000
    )
    assert f.context["quantity"] == "0.05" and f.context["canonical_wallet"] == "4999.4884705"


def test_native_partial_exit_replays_old_trigger_basis_after_context_changes(
    case, original_evidence, tmp_path, monkeypatch
):
    from tools.gold_delegated_proof import export
    from live.execution.demo_gold_delegated_admission import admit
    from live.execution import demo_pre_submit as prep, demo_issuer
    from live.execution.demo_issuer_store import IssuerStore

    def reserve_two_exits(policy):
        policy["budget_group"]["max_grants"] = 4
        policy["budget_group"]["exit_reserve"]["grants"] = 2

    f = completed_bar_entry(case, original_evidence, tmp_path, monkeypatch, configure_policy=reserve_two_exits)
    try:
        old_context = deepcopy(f.context)
        first = retain_exit(f, at=f.now_ms + 2000, price="90")
        partial_stop_receipts(f, first, monkeypatch)
        assert f.context["position_episode"] == old_context["position_episode"]
        assert _digest(f.context) != _digest(old_context)
        # Reopen every original order store to discard process-local state.
        from live.execution.demo_dispatch_store import DemoDispatchStore
        from live.execution.order_recovery import SqliteOrderRecoveryStore
        from live.execution.position_ledger import LiveLedger

        for store in f.stores.values():
            store.close()
        f.stores = dict(
            dispatch=DemoDispatchStore(f.root / "dispatch.db", read_only=True),
            recovery=SqliteOrderRecoveryStore(str(f.root / "recovery.db"), read_only=True),
            oms=LiveLedger(str(f.root / "oms.db"), read_only=True),
        )
        second = retain_exit(f, at=f.now_ms + 4000, price="100")
        proof = export(
            root=f.root,
            runtime=f.proof["runtime"],
            result_sha256=_digest(second),
            enable=f.enable,
            registry=f.registry,
            source_commit="c" * 40,
            uid=os.getuid(),
            now_ms=f.now_ms + 4000,
            stores=f.stores,
        )
        assert proof["execution"]["context"] == f.context
        assert proof["execution"]["trigger_evidence"]["context"] == old_context
        checked = admit(
            delegation=f.delegation,
            enable=f.enable,
            base_policy=f.base_policy,
            registry=f.registry,
            proof=proof,
            now_ms=f.now_ms + 4000,
        )
        assert checked["orders"][0]["quantity"] == "0.05" and checked["orders"][0]["reduceOnly"] is True
        assert second["decision"]["draft"]["reason"] == "STOP"
        values = exit_proposal_values(f, second)
        proposal = prep.proposal(**values, **f.stores)
        proposal.update(schema="DEMO_GOLD_DELEGATED_PRE_SUBMISSION_PROPOSAL_V1", delegated_admission=proof)
        proposal["context"]["delegated_admission_sha256"] = _digest(proof)
        handoff = dict(
            schema="DEMO_EXECUTION_SESSION_PROPOSAL_V1",
            proposal=proposal,
            proposal_sha256=_digest(proposal),
            proposal_path="/isolated/remainder-exit.json",
            status="WAITING_INDEPENDENT_GRANT",
            submission_enabled=False,
        )
        with closing(IssuerStore(f.ledger)) as log:
            before = log.snapshot()
            grant = demo_issuer.issue_delegated_submission(
                handoff,
                policy=f.base_policy,
                registry=f.registry,
                public_material=f.enable,
                key=f.private,
                store=log,
                now_ms=f.now_ms + 4000,
            )
            from services.validation.demo_submission import verify

            assert verify(
                grant["envelope"],
                registry=f.registry,
                expected_context=proposal["context"],
                orders=checked["orders"],
                now_ms=f.now_ms + 4000,
            )["signature_verified"]
            after = log.snapshot()
            assert after[: len(before)] == before
            reserved = [row for row in after if row["kind"] == "RESERVED"]
            assert (
                len(reserved) == 4
                and len({row["payload"]["policy"]["budget_group"]["group_id"] for row in reserved}) == 1
            )
        bad = deepcopy(proof)
        bad["execution"]["trigger_evidence"]["context"] = f.context
        with pytest.raises(ValueError):
            admit(
                delegation=f.delegation,
                enable=f.enable,
                base_policy=f.base_policy,
                registry=f.registry,
                proof=bad,
                now_ms=f.now_ms + 4000,
            )
    finally:
        f.events.close()
        f.stream.__exit__(None, None, None)
        for store in f.stores.values():
            store.close()
