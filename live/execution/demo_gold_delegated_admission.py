"""Independently replay bounded original bar evidence; never install authority."""

import base64
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.research.gold_bar_candidates import evaluate
from services.research.gold_bar_dataset import load_bars
from services.research.gold_bar_stream import GoldBarStream

SCHEMA = "DEMO_GOLD_DELEGATED_DECISION_PROOF_V1"
MAX_BYTES = 12000000
FILES = (
    "registration.json",
    "result.json",
    "selected-decisions.jsonl",
    "live-signals.jsonl",
    "journal/plan.json",
    "journal/summary.json",
    "journal/warmup.json",
    "journal/warmup-receipt.json",
    "journal/messages.jsonl",
    "journal/decisions.jsonl",
)


def bounded(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    require(len(raw) <= MAX_BYTES, "GOLD_DELEGATED_PROOF_BUDGET")
    return value


def replay_prefix(prefix, *, runtime, definition, observed_at_ms):
    """Replay every recorded message and the exact immutable tail, without I/O.

    Definition authority comes from the separately installed public ENABLE. This
    routine checks full forward outputs against that definition, not a supplied
    signal hash or a controller assertion that replay succeeded.
    """
    from services.execution.backtest_authority import exact_identity_hash
    from quantos.contracts.gold_observation_window import EXTENDED_MAX_SECONDS, EXTENDED_STREAM_SCHEMA

    bounded(prefix)
    _fields(prefix, "schema files entry_decision_ms tail")
    require(
        prefix["schema"] == "GOLD_DELEGATED_FROZEN_PREFIX_V1"
        and type(prefix["files"]) is dict
        and set(prefix["files"]) == set(FILES),
        "GOLD_DELEGATED_PREFIX_FILES",
    )
    require(
        definition["definition_hash"]
        == exact_identity_hash(
            {k: v for k, v in definition.items() if k != "definition_hash"}, name="gold_bar_research_definition"
        ),
        "GOLD_DELEGATED_DEFINITION_HASH",
    )
    require(
        definition["archive_files"]["manifest.json"] == runtime["trial_manifest_sha256"]
        and definition["source"]["commit"] == runtime["archive_source_commit"],
        "GOLD_DELEGATED_ARCHIVE",
    )
    raw = {name: base64.b64decode(value, validate=True) for name, value in prefix["files"].items()}
    reg, result, plan, summary, receipt = (
        decode(raw[name])
        for name in (
            "registration.json",
            "result.json",
            "journal/plan.json",
            "journal/summary.json",
            "journal/warmup-receipt.json",
        )
    )
    require(
        reg["schema"] == "GOLD_BAR_BOUND_FORWARD_REGISTRATION_V1"
        and reg["definition"] == definition
        and reg["submission_enabled"] is False
        and reg["local_registration_only"] is True
        and type(reg["registered_at_ms"]) is int
        and reg["registered_at_ms"] >= 0,
        "GOLD_DELEGATED_REGISTRATION",
    )
    extended = summary["schema"] == EXTENDED_STREAM_SCHEMA
    require(
        not extended
        or (
            plan.get("extended") is True
            and type(plan["seconds"]) is int
            and 1 <= plan["seconds"] <= EXTENDED_MAX_SECONDS
        ),
        "GOLD_DELEGATED_EXTENDED",
    )
    require(
        summary["schema"] in ("GOLD_BAR_STREAM_SUMMARY_V1", EXTENDED_STREAM_SCHEMA)
        and summary["status"] == "OBSERVED"
        and summary["reason"] in ("DURATION_COMPLETE", "FIRST_NEW_CLOSE_COMPLETE")
        and summary["source_unchanged"] is True
        and summary["submission_enabled"] is False
        and plan["submission_enabled"] is False
        and plan["url"] == "wss://fstream.binance.com/market/stream?streams=xauusdt@kline_15m"
        and plan["source"] == reg["observation_source"],
        "GOLD_DELEGATED_JOURNAL",
    )
    hashes = {
        name.removeprefix("journal/"): hashlib.sha256(body).hexdigest()
        for name, body in raw.items()
        if name.startswith("journal/")
    }
    require(
        hashes["warmup.json"] == receipt["response_sha256"] == plan["warmup_source_sha256"]
        and hashes["messages.jsonl"] == summary["messages_sha256"]
        and hashes["decisions.jsonl"] == summary["decisions_sha256"],
        "GOLD_DELEGATED_JOURNAL_HASHES",
    )
    require(
        receipt["received_at_ms"] <= reg["registered_at_ms"]
        and plan["seconds"] == reg["seconds"]
        and type(reg.get("first_close", False)) is bool
        and plan.get("stop_after_close_ms") == (reg["registered_at_ms"] if reg.get("first_close") else None)
        and reg.get("network_mode") in (None, "PROTECTED_DEMO_NETWORK_V1")
        and plan.get("network_mode") == reg.get("network_mode"),
        "GOLD_DELEGATED_PLAN",
    )
    cursor = GoldBarStream(
        load_bars(
            raw["journal/warmup.json"],
            requested_at_ms=receipt["requested_at_ms"],
            received_at_ms=receipt["received_at_ms"],
        )
    )
    messages = raw["journal/messages.jsonl"].splitlines()
    require(
        type(summary["messages"]) is int and 1 <= len(messages) <= 20000 and len(messages) == summary["messages"],
        "GOLD_DELEGATED_MESSAGE_COUNT",
    )
    decisions = []
    for line in messages:
        item = decode(line)
        _fields(item, "received_at_ms raw")
        require(
            type(item["received_at_ms"]) is int and item["received_at_ms"] >= reg["registered_at_ms"],
            "GOLD_DELEGATED_RECEIPT_BEFORE_REGISTRATION",
        )
        observed = cursor.ingest(item["raw"], received_at_ms=item["received_at_ms"])
        if observed["status"] == "CLOSED_BAR":
            decisions.append(observed)
    require(
        decisions == [decode(line) for line in raw["journal/decisions.jsonl"].splitlines()]
        and type(summary["closed_bars"]) is int
        and len(decisions) == summary["closed_bars"],
        "GOLD_DELEGATED_DECISIONS",
    )
    threshold = plan.get("stop_after_close_ms")
    require(
        threshold is None or (type(threshold) is int and 0 <= threshold <= 2**63 - 1 and not extended),
        "GOLD_DELEGATED_THRESHOLD",
    )
    eligible = [row for row in decisions if threshold is not None and row["close_ms"] > threshold]
    require(
        (summary["reason"] == "FIRST_NEW_CLOSE_COMPLETE" and len(eligible) == 1 and observed == eligible[0])
        or (summary["reason"] == "DURATION_COMPLETE" and not eligible),
        "GOLD_DELEGATED_FIRST_CLOSE",
    )
    selected = []
    for row in decisions:
        if row["close_ms"] <= reg["registered_at_ms"]:
            continue
        require(row["received_at_ms"] >= row["close_ms"], "GOLD_DELEGATED_KNOWLEDGE_TIME")
        (signal,) = [v for v in row["decisions"] if v["candidate"] == definition["candidate"]]
        selected.append(
            dict(
                definition_hash=definition["definition_hash"],
                close_ms=row["close_ms"],
                received_at_ms=row["received_at_ms"],
                signal=signal,
                submission_enabled=False,
            )
        )
    encoded = b"".join(json.dumps(v, sort_keys=True, allow_nan=False).encode() + b"\n" for v in selected)
    require(raw["selected-decisions.jsonl"] == encoded, "GOLD_DELEGATED_SELECTED")
    live = [decode(line) for line in raw["live-signals.jsonl"].splitlines()]
    require(len(live) == len(selected), "GOLD_DELEGATED_LIVE_COUNT")
    for retained, projected in zip(live, selected):
        prepared = retained["prepared_at_ms"]
        require(type(prepared) is int and prepared >= projected["received_at_ms"], "GOLD_DELEGATED_PREPARED")
        deadline = projected["signal"]["decision_ms"] + definition["size_and_timing"]["entry_timeout_ms"]
        require(
            retained
            == dict(
                projected,
                prepared_at_ms=prepared,
                signal_valid_until_ms=deadline,
                entry_window_status="TIMELY" if prepared < deadline else "EXPIRED",
                scope="SIGNAL_ONLY_RECHECK_CURRENT_TIME_AND_ACCOUNT_BEFORE_ANY_EXECUTION",
            ),
            "GOLD_DELEGATED_LIVE",
        )
    expected = dict(
        schema="GOLD_BAR_BOUND_FORWARD_RESULT_V1",
        status="FORWARD_SIGNALS_RECORDED" if selected else "NO_NEW_CLOSED_BAR",
        definition_hash=definition["definition_hash"],
        registration_sha256=hashlib.sha256(raw["registration.json"]).hexdigest(),
        selected_decisions_sha256=hashlib.sha256(encoded).hexdigest(),
        journal_hashes=hashes,
        new_closed_bars=len(selected),
        live_signals_sha256=hashlib.sha256(raw["live-signals.jsonl"]).hexdigest(),
        authoritative=False,
        economic_eligible=False,
        submission_enabled=False,
        net_pnl=None,
        execution_adapter_status="BAR_DEMO_ADAPTER_NOT_IMPLEMENTED",
    )
    require(result == expected, "GOLD_DELEGATED_FORWARD_RESULT")
    entry = prefix["entry_decision_ms"]
    require(type(entry) is int and type(observed_at_ms) is int and entry <= observed_at_ms, "GOLD_DELEGATED_ENTRY_TIME")
    require(any(v["received_at_ms"] == entry for v in selected), "GOLD_DELEGATED_ENTRY_MISSING")
    signal = evaluate(cursor.bars, candidate=definition["candidate"], as_of_ms=entry)
    if runtime["schema"] == "GOLD_BAR_RETAINED_EPISODE_INPUT_V1":
        require(
            runtime["entry_decision_ms"] == entry
            and prefix["tail"] is None
            and runtime["observation_sha256"] == hashlib.sha256(raw["result.json"]).hexdigest(),
            "GOLD_DELEGATED_RETAINED",
        )
    else:
        require(
            runtime["schema"] in ("GOLD_BAR_FRESH_EPISODE_INPUT_V1", "GOLD_BAR_AUTO_EPISODE_INPUT_V1")
            and reg.get("first_close") is True
            and reg.get("network_mode") == "PROTECTED_DEMO_NETWORK_V1"
            and len(selected) == 1
            and runtime["candidate"] == definition["candidate"]
            and runtime["seconds"] == reg["seconds"],
            "GOLD_DELEGATED_FRESH",
        )
        if runtime["schema"] == "GOLD_BAR_FRESH_EPISODE_INPUT_V1":
            require(hashes["warmup-receipt.json"] == runtime["history_sha256"], "GOLD_DELEGATED_HISTORY_BINDING")
        tail = prefix["tail"]
        _fields(tail, "binding snapshot events")
        binding = dict(
            bundle_sha256=_digest(runtime),
            definition_hash=definition["definition_hash"],
            entry_decision_ms=entry,
            signal_sha256=_digest(signal),
            bars_sha256=_digest([asdict(b) for b in cursor.bars]),
            submission_enabled=False,
        )
        require(tail["binding"] == binding, "GOLD_DELEGATED_TAIL_BINDING")
        _fields(tail["snapshot"], "binding prefix")
        require(
            tail["snapshot"]["binding"] == binding
            and type(tail["snapshot"]["prefix"]) is list
            and len(tail["snapshot"]["prefix"]) <= 128
            and type(tail["events"]) is list
            and len(tail["events"]) == len(tail["snapshot"]["prefix"]),
            "GOLD_DELEGATED_TAIL_PREFIX",
        )
        previous = None
        for ordinal, (digest, row) in enumerate(zip(tail["snapshot"]["prefix"], tail["events"]), 1):
            _fields(row, "binding_sha256 ordinal previous_sha256 kind received_at_ms raw reason")
            require(
                _digest(row) == digest
                and row["binding_sha256"] == _digest(binding)
                and type(row["ordinal"]) is int
                and row["ordinal"] == ordinal
                and row["previous_sha256"] == previous
                and not cursor.blocked
                and type(row["received_at_ms"]) is int
                and row["received_at_ms"] >= cursor.last_received,
                "GOLD_DELEGATED_TAIL_CHAIN",
            )
            if row["kind"] == "CLOSED_BAR":
                require(
                    row["reason"] is None
                    and cursor.ingest(row["raw"], received_at_ms=row["received_at_ms"])["status"] == "CLOSED_BAR",
                    "GOLD_DELEGATED_TAIL_FINALITY",
                )
            else:
                require(
                    row["kind"] == "GAP"
                    and row["raw"] is None
                    and type(row["reason"]) is str
                    and 0 < len(row["reason"]) <= 100,
                    "GOLD_DELEGATED_TAIL_GAP",
                )
                cursor.last_received = row["received_at_ms"]
                cursor.blocked = True
            previous = digest
    require(cursor.last_received <= observed_at_ms, "GOLD_DELEGATED_FUTURE_PREFIX")
    return dict(
        signal=signal,
        entry_decision_ms=entry,
        last_received_ms=cursor.last_received,
        cursor=cursor,
        registration=reg,
        observation_sha256=hashlib.sha256(raw["result.json"]).hexdigest(),
    )


def _pilot(records, *, terms, origin, account, observed_at_ms):
    from decimal import Decimal
    from live.execution.demo_gold_pilot import config_check
    from services.validation.demo_submission import _digest as digest_check
    from live.execution.demo_gold_pilot_enrollment import completed_configuration_records

    _fields(records, "request configuration receipt intent stop")
    config = completed_configuration_records(
        **{k: records[k] for k in ("configuration", "receipt", "intent", "request")}
    )
    initial = config_check(config)
    require(
        config == terms["pilot_configuration"]
        and _digest(records["request"]) == terms["pilot_request_sha256"]
        and config["account_ref_sha256"] == origin["account_ref_sha256"]
        and config["started_at_ms"] <= observed_at_ms,
        "GOLD_DELEGATED_PILOT",
    )
    stop = records["stop"]
    if stop is not None:
        _fields(
            stop,
            "schema status reason configuration_sha256 account_ref_sha256 observed_at_ms observed_equity origin_sha256 submission_enabled",
        )
        require(
            stop["schema"] == "GOLD_DEMO_PILOT_STOP_V1"
            and stop["status"] == "STOPPED"
            and stop["reason"] in ("LOSS_LIMIT", "DURATION_COMPLETE")
            and stop["submission_enabled"] is False
            and stop["configuration_sha256"] == _digest(config)
            and stop["account_ref_sha256"] == config["account_ref_sha256"]
            and type(stop["observed_at_ms"]) is int
            and config["started_at_ms"] <= stop["observed_at_ms"] <= observed_at_ms,
            "GOLD_DELEGATED_STOP",
        )
        require(
            type(stop["observed_equity"]) is str and len(stop["observed_equity"]) <= 100, "GOLD_DELEGATED_STOP_EQUITY"
        )
        digest_check(stop["origin_sha256"])
        equity = Decimal(stop["observed_equity"])
        require(
            equity.is_finite()
            and (
                equity <= initial * Decimal("0.95")
                if stop["reason"] == "LOSS_LIMIT"
                else stop["observed_at_ms"] >= config["ends_at_ms"]
            ),
            "GOLD_DELEGATED_STOP_EVIDENCE",
        )
        return stop
    require(
        Decimal(account["equity"]) > initial * Decimal("0.95") and observed_at_ms < config["ends_at_ms"],
        "GOLD_DELEGATED_STOP_MISSING",
    )
    return dict(status="ACTIVE", configuration_sha256=_digest(config), submission_enabled=False)


def _quote(origin, *, now_ms, lower_ms, maximum_age):
    reads = {v["kind"]: v for v in origin["reads"]}
    clocks = [reads[k] for k in ("clock_before", "clock_after")]
    lower = min(v["data"]["serverTime"] - v["received_at_ms"] for v in clocks)
    upper = max(v["data"]["serverTime"] - v["requested_at_ms"] for v in clocks)
    row = reads["book"]
    book = row["data"]
    lo, hi = book["time"] - upper, book["time"] - lower
    require(
        lower_ms <= lo <= hi <= row["received_at_ms"] <= now_ms and now_ms - lo < maximum_age,
        "GOLD_DELEGATED_QUOTE_CLOCK",
    )
    return book, lo


def _entry(*, definition, replay, origin, pilot, now_ms):
    from decimal import Decimal
    from services.execution.perpetual_ledger_v2 import canonical_decimal
    from live.execution.demo_pre_submit import _venue_rules, _venue_account_cap
    from services.validation.demo_submission import validate_orders, GOLD_BATCH_SCHEMA

    signal = replay["signal"]
    plan = definition["size_and_timing"]
    require(
        not replay["cursor"].blocked
        and signal["status"] == "READY"
        and signal["entry_side"] in ("BUY", "SELL")
        and replay["cursor"].bars[-1].available_ms == replay["entry_decision_ms"],
        "GOLD_DELEGATED_ENTRY_SIGNAL",
    )
    deadline = signal["decision_ms"] + plan["entry_timeout_ms"]
    require(signal["decision_ms"] < now_ms < deadline and pilot["status"] == "ACTIVE", "GOLD_DELEGATED_ENTRY_WINDOW")
    book, lower = _quote(origin, now_ms=now_ms, lower_ms=signal["decision_ms"], maximum_age=plan["max_quote_age_ms"])
    side = signal["entry_side"]
    price = Decimal(book["askPrice" if side == "BUY" else "bidPrice"])
    distance = Decimal(signal["stop_distance"])
    require(distance.is_finite() and 0 < distance < price, "GOLD_DELEGATED_STOP_DISTANCE")
    client = (
        "QD"
        + _digest(
            dict(
                account_ref_sha256=origin["account_ref_sha256"],
                definition_hash=definition["definition_hash"],
                decision_id=signal["decision_id"],
                action="OPEN_" + side,
            )
        )[:32]
    )
    order = dict(
        symbol="XAUUSDT",
        side=side,
        type="LIMIT",
        timeInForce="IOC",
        quantity=canonical_decimal(Decimal(plan["quantity"])),
        price=canonical_decimal(price),
        reduceOnly=False,
        positionSide="BOTH",
        newClientOrderId=client,
        newOrderRespType="ACK",
    )
    validate_orders([order], schema=GOLD_BATCH_SCHEMA)
    _venue_rules([order], origin)
    _venue_account_cap([order], origin)
    draft = dict(
        schema="GOLD_BAR_DEMO_ENTRY_DRAFT_V1",
        environment="BINANCE_USDM_DEMO",
        definition_hash=definition["definition_hash"],
        trial_id=definition["trial_id"],
        observed_at_ms=now_ms,
        orders=[order],
        submission_enabled=False,
        economic_authority=False,
        risk_status="NOT_RUN",
        authorization_status="BAR_STRATEGY_ENROLLMENT_REQUIRED",
        pilot_control=pilot,
        status="BAR_ENTRY_DRAFT",
        decision=signal,
        signal_valid_until_ms=deadline,
        valid_until_ms=min(deadline, lower + plan["max_quote_age_ms"]),
        origin_sha256=_digest(origin),
        account_ref_sha256=origin["account_ref_sha256"],
        exit_plan=dict(
            stop_distance=canonical_decimal(distance),
            stop_basis="ACTUAL_FILLED_AVERAGE_PRICE",
            max_holding_ms=signal["max_holding_bars"] * 900000,
            signal_exit_side="exit_long" if side == "BUY" else "exit_short",
        ),
        required_before_execution=[
            "BAR_STRATEGY_ENROLLMENT",
            "INDEPENDENT_ADMISSION_AND_RISK",
            "RECHECK_PERSISTENT_5_PERCENT_STOP_AT_SUBMISSION",
            "SIGNED_ENTRY_DEADLINE",
            "ORIGINAL_DISPATCH_DEDUPLICATION",
            "OMS_BOUND_BIDIRECTIONAL_EXIT_CONTROLLER",
        ],
    )
    return dict(draft, result_hash=_digest(draft))


def _execution(execution, *, decision, origin, observed_at_ms):
    from decimal import Decimal
    from live.execution import demo_position_context as position
    from services.research.demo_execution_origin import assess

    _fields(execution, "state context exit_trigger trigger_evidence")
    state = execution["state"]
    _fields(state, "reservations recovery oms wire")
    require(
        all(type(state[k]) is list and len(state[k]) <= 10000 for k in state)
        and _digest(state) == decision["execution_state_sha256"],
        "GOLD_DELEGATED_EXECUTION_STATE",
    )
    require(
        all(r["state"] == "RESOLVED" for r in state["recovery"])
        and all(o["status"] in ("FILLED", "CANCELED", "REJECTED", "EXPIRED") for o in state["oms"]),
        "GOLD_DELEGATED_RECOVERY",
    )
    recovered = {r["client_order_id"] for r in state["recovery"]}
    require(
        all(o["newClientOrderId"] in recovered for row in state["reservations"] for o in row["payload"]["orders"]),
        "GOLD_DELEGATED_UNRECOVERED_RESERVATION",
    )
    context = execution["context"]
    if context is None:
        report = assess(origin, as_of_ms=observed_at_ms)
        require(
            report["status"] == "OBSERVED_FLAT_QUOTE" and "position_context_sha256" not in decision,
            "GOLD_DELEGATED_FLAT_ORIGIN",
        )
        account = dict(equity=report["wallet"])
    else:
        require(_digest(context) == decision["position_context_sha256"], "GOLD_DELEGATED_POSITION_BINDING")
        account = position.validate_origin(origin, context, as_of_ms=observed_at_ms)
    flat = context is None or Decimal(context["quantity"]) == 0
    require(
        decision["position_flat"] is flat and decision["execution_history_empty"] is (not state["reservations"]),
        "GOLD_DELEGATED_POSITION_STATUS",
    )
    return state, context, account, flat


def admit(*, delegation, enable, base_policy, registry, proof, now_ms):
    """Derive exact constraints under independently loaded ENABLE and original policy.

    Boundary callers must load ENABLE from the fixed root-owned public reader on
    each invocation. A supplied ENABLE object alone establishes no file custody.
    This pure check neither issues nor reserves a grant.
    """
    from live.execution import demo_gold_delegation_enable as enabling
    from live.execution.demo_gold_execution_delegation import delegation_base, SCHEMA_V2
    from live.execution.demo_gold_future_template import current_template_key, template_group_binding, derive_exact_policy
    from live.execution.demo_issuer_policy import validate_policy, budget_group_binding, current_key
    from services.validation.demo_strategy_constraints import validate as validate_constraints
    from services.execution.perpetual_ledger_v2 import ledger_decimal_context

    with ledger_decimal_context():
        bounded(proof)
        _fields(
            proof,
            "schema source_commit enable_sha256 delegation_sha256 runtime result origin claim capacity pilot execution prefix submission_enabled economic_authority",
        )
        require(
            proof["schema"] == SCHEMA
            and proof["submission_enabled"] is False
            and proof["economic_authority"] is False
            and type(now_ms) is int
            and 0 <= now_ms <= 2**63 - 1,
            "GOLD_DELEGATED_PROOF",
        )
        source = proof["source_commit"]
        account = base_policy["account_ref_sha256"]
        enabling.validate(enable, registry=registry, source_commit=source, account_ref_sha256=account, now_ms=now_ms)
        require(
            proof["enable_sha256"] == _digest(enable)
            and proof["delegation_sha256"] == _digest(delegation)
            and delegation == enable["delegation"]
            and delegation_base(delegation) == base_policy,
            "GOLD_DELEGATED_AUTHORITY_BINDING",
        )
        future = delegation['schema'] == SCHEMA_V2
        if future:
            key = current_template_key(base_policy, registry, now_ms)
            group_binding = template_group_binding(base_policy)
        else:
            validate_policy(base_policy)
            key = current_key(base_policy, registry, now_ms)
            group_binding = budget_group_binding(base_policy)
        terms = delegation["terms"]
        runtime = proof["runtime"]
        base = enable["runtime"]
        omit = ("episode_id",) if base["schema"] == "GOLD_BAR_AUTO_EPISODE_INPUT_V1" else ()
        require(
            set(runtime) == set(base)
            and {k: v for k, v in runtime.items() if k not in omit} == {k: v for k, v in base.items() if k not in omit},
            "GOLD_DELEGATED_RUNTIME",
        )
        if omit:
            from services.validation.demo_submission import _digest as digest_check

            digest_check(runtime["episode_id"])
        result = proof["result"]
        decision = result["decision"]
        _fields(
            result,
            "schema status source_commit bundle_sha256 decision_sha256 decision submission_enabled economic_authority original_pilot_window"
            + (" recovery_observation" if "recovery_observation" in result else ""),
        )
        if "recovery_observation" in result:
            require(
                type(result["recovery_observation"]) is dict
                and result["recovery_observation"].get("new_order_submission_enabled") is False,
                "GOLD_DELEGATED_RECOVERY_OBSERVATION",
            )
        require(
            result["schema"] == "GOLD_STRATEGY_WORKER_RESULT_V1"
            and result["status"] == "DECISION_RETAINED"
            and result["source_commit"] == source
            and result["bundle_sha256"] == _digest(runtime)
            and result["decision_sha256"] == _digest(decision)
            and result["submission_enabled"] is False
            and result["economic_authority"] is False,
            "GOLD_DELEGATED_RESULT",
        )
        require(
            decision["schema"] == "GOLD_STRATEGY_CYCLE_DECISION_V1"
            and decision["status"] == "DECISION_REQUIRES_APPROVAL"
            and decision["source_commit"] == source
            and decision["bundle_sha256"] == _digest(runtime)
            and decision["account_ref_sha256"] == account
            and decision["origin_sha256"] == _digest(proof["origin"])
            and decision["strategy_definition_sha256"] == enable["definition"]["definition_hash"]
            and decision["strategy_family"] == "GOLD_BAR_CANDIDATES_V1"
            and decision["cash_reconciled"] is False
            and decision["submission_enabled"] is False
            and decision["economic_authority"] is False,
            "GOLD_DELEGATED_DECISION",
        )
        at = decision["observed_at_ms"]
        require(
            type(at) is int
            and terms["confirmed_at_ms"] <= enable["operator_confirmation"]["approved_at_ms"] <= at <= now_ms,
            "GOLD_DELEGATED_DECISION_CLOCK",
        )
        config = proof["pilot"]["configuration"]
        capacity = proof["capacity"]
        claim = proof["claim"]
        window = {k: capacity[k] for k in ("request_sha256", "configuration_sha256", "started_at_ms", "ends_at_ms")}
        require(
            dict(
                window,
                schema="GOLD_PILOT_ORIGINAL_WINDOW_V1",
                source_commit=source,
                account_ref_sha256=account,
                submission_enabled=False,
            )
            == result["original_pilot_window"]
            and capacity["source_commit"] == source
            and capacity["account_ref_sha256"] == account
            and capacity["base_runtime"] == base
            and capacity["submission_enabled"] is False
            and capacity["economic_authority"] is False
            and capacity["closing_ms"] == 900000
            and capacity["minimum_interval_ms"] == 1000
            and capacity["max_frames"] == 87302
            and window
            == dict(
                request_sha256=terms["pilot_request_sha256"],
                configuration_sha256=_digest(config),
                started_at_ms=config["started_at_ms"],
                ends_at_ms=config["ends_at_ms"],
            ),
            "GOLD_DELEGATED_ORIGINAL_WINDOW",
        )
        _fields(claim, "schema ordinal admitted_at_ms bundle_sha256 capacity_sha256 submission_enabled")
        require(
            claim["schema"] == "GOLD_PILOT_OBSERVATION_CLAIM_V1"
            and claim["capacity_sha256"] == _digest(capacity)
            and claim["bundle_sha256"] == _digest(runtime)
            and claim["submission_enabled"] is False
            and type(claim["ordinal"]) is int
            and 1 <= claim["ordinal"] <= capacity["max_frames"]
            and type(claim["admitted_at_ms"]) is int
            and config["started_at_ms"] <= claim["admitted_at_ms"] <= at
            and claim["admitted_at_ms"] < config["ends_at_ms"] + capacity["closing_ms"],
            "GOLD_DELEGATED_CLAIM",
        )
        replay = replay_prefix(proof["prefix"], runtime=runtime, definition=enable["definition"], observed_at_ms=at)
        require(decision["bar_signal_sha256"] == _digest(replay["signal"]), "GOLD_DELEGATED_SIGNAL")
        if proof["prefix"]["tail"] is not None:
            require(
                decision["bar_continuation_sha256"] == _digest(proof["prefix"]["tail"]["snapshot"]),
                "GOLD_DELEGATED_FROZEN_PREFIX",
            )
        state, context, account_state, flat = _execution(
            proof["execution"], decision=decision, origin=proof["origin"], observed_at_ms=at
        )
        pilot = _pilot(proof["pilot"], terms=terms, origin=proof["origin"], account=account_state, observed_at_ms=at)
        require(decision["pilot_control"] == pilot, "GOLD_DELEGATED_PILOT_CONTROL")
        opening = decision["action"] in ("OPEN_LONG", "OPEN_SHORT")
        if opening:
            require(
                flat and proof["execution"]["exit_trigger"] is None and now_ms < terms["opening_until_ms"],
                "GOLD_DELEGATED_OPENING",
            )
            draft = _entry(
                definition=enable["definition"], replay=replay, origin=proof["origin"], pilot=pilot, now_ms=at
            )
            require(
                draft["exit_plan"]["max_holding_ms"] <= terms["max_holding_ms"]
                and proof["execution"]["trigger_evidence"] is None,
                "GOLD_DELEGATED_HOLDING_LIMIT",
            )
            expected_action = "OPEN_LONG" if replay["signal"]["entry_side"] == "BUY" else "OPEN_SHORT"
            require(
                not any(
                    o["newClientOrderId"] == draft["orders"][0]["newClientOrderId"]
                    for r in state["reservations"]
                    for o in r["payload"]["orders"]
                ),
                "GOLD_DELEGATED_ENTRY_SPENT",
            )
        else:
            require(not flat and decision["action"] in ("REDUCE_LONG", "REDUCE_SHORT"), "GOLD_DELEGATED_REDUCTION")
            draft = _exit(
                definition=enable["definition"],
                replay=replay,
                origin=proof["origin"],
                pilot=pilot,
                state=state,
                context=context,
                trigger=proof["execution"]["exit_trigger"],
                trigger_evidence=proof["execution"]["trigger_evidence"],
                runtime=runtime,
                terms=terms,
                now_ms=at,
            )
            expected_action = "REDUCE_LONG" if replay["signal"]["entry_side"] == "BUY" else "REDUCE_SHORT"
        require(decision["action"] == expected_action and decision["draft"] == draft, "GOLD_DELEGATED_EXACT_DRAFT")
        constraints = dict(
            schema="DEMO_GOLD_STRATEGY_CONSTRAINTS_V1",
            strategy_definition_sha256=draft["definition_hash"],
            decision_sha256=draft["result_hash"],
            orders_sha256=_digest(draft["orders"]),
            not_before_ms=at,
            valid_until_ms=draft["valid_until_ms"],
        )
        validate_constraints(constraints, orders=draft["orders"], now_ms=now_ms)
        require(decision["execution_constraints"] == constraints, "GOLD_DELEGATED_EXACT_CONSTRAINTS")
        deadline = min(
            constraints["valid_until_ms"],
            terms["opening_until_ms"] if opening else terms["exit_until_ms"],
            base_policy["valid_until_ms"],
            key["valid_until_ms"],
        )
        require(now_ms < deadline, "GOLD_DELEGATED_EXPIRED")
        digest = _digest(proof)
        if future:
            effective = derive_exact_policy(base_policy, proof_sha256=digest, constraints=constraints,
                orders=draft['orders'], now_ms=now_ms)
        else:
            effective = deepcopy(base_policy)
            effective["policy_id"] = "gold-delegated-" + digest
            effective["bindings"]["execution_constraints_sha256"] = _digest(constraints)
            validate_policy(effective)
        require(budget_group_binding(effective) == group_binding, "GOLD_DELEGATED_BUDGET_REBIND")
        return dict(
            effective_policy=effective,
            constraints=constraints,
            orders=draft["orders"],
            proof_sha256=digest,
            constraints_sha256=_digest(constraints),
            result_sha256=_digest(result),
            valid_until_ms=deadline,
            history_anchor=deepcopy(enable["issuer_anchor"]),
            group_binding_sha256=group_binding,
        )


def verify_admission(proof, *, public_material, installed_policy, registry, now_ms):
    return admit(
        delegation=public_material["delegation"],
        enable=public_material,
        base_policy=installed_policy,
        registry=registry,
        proof=proof,
        now_ms=now_ms,
    )


def _exit_reason(*, definition, replay, origin, pilot, context, now_ms):
    from decimal import Decimal

    side = replay["signal"]["entry_side"]
    quantity = Decimal(context["quantity"])
    book, lower = _quote(
        origin, now_ms=now_ms, lower_ms=0, maximum_age=definition["size_and_timing"]["max_quote_age_ms"]
    )
    price = Decimal(book["bidPrice" if quantity > 0 else "askPrice"])
    average = Decimal(context["average_entry_price"])
    distance = Decimal(replay["signal"]["stop_distance"])
    require(average.is_finite() and distance.is_finite() and 0 < distance < average, "GOLD_DELEGATED_EXIT_BASIS")
    stop_price = average - distance if quantity > 0 else average + distance
    stop = price <= stop_price if quantity > 0 else price >= stop_price
    opened = context["position_episode"]["opened_venue_at_ms"]
    require(type(opened) is int and 0 <= opened <= book["time"], "GOLD_DELEGATED_OPEN_TIME")
    timed = book["time"] >= opened + replay["signal"]["max_holding_bars"] * 900000
    current = (
        None
        if replay["cursor"].blocked
        else evaluate(replay["cursor"].bars, candidate=definition["candidate"], as_of_ms=now_ms)
    )
    reverse = (
        current is not None
        and current["status"] == "READY"
        and any(replay["entry_decision_ms"] < bar.available_ms <= now_ms for bar in replay["cursor"].bars)
        and current["exit_long" if side == "BUY" else "exit_short"]
    )
    reason = (
        "PILOT_" + pilot["reason"]
        if pilot["status"] == "STOPPED"
        else "STOP"
        if stop
        else "TIMEOUT"
        if timed
        else "SIGNAL_EXIT"
        if reverse
        else None
    )
    return reason, price, average, stop_price, lower


def _exit(*, definition, replay, origin, pilot, state, context, trigger, trigger_evidence, runtime, terms, now_ms):
    from decimal import Decimal
    from live.execution import demo_bar_exit_trigger, demo_position_context as position
    from live.execution.demo_pre_submit import _venue_rules, _venue_account_cap
    from services.execution.perpetual_ledger_v2 import canonical_decimal
    from services.validation.demo_submission import validate_orders, GOLD_BATCH_SCHEMA

    signal = replay["signal"]
    side = signal["entry_side"]
    quantity = Decimal(context["quantity"])
    require(
        signal["status"] == "READY"
        and side in ("BUY", "SELL")
        and replay["entry_decision_ms"] < now_ms
        and quantity.is_finite()
        and quantity != 0
        and (quantity > 0) == (side == "BUY")
        and abs(quantity) <= Decimal(definition["size_and_timing"]["quantity"]),
        "GOLD_DELEGATED_EXIT_QUANTITY",
    )
    client = (
        "QD"
        + _digest(
            dict(
                account_ref_sha256=origin["account_ref_sha256"],
                definition_hash=definition["definition_hash"],
                decision_id=signal["decision_id"],
                action="OPEN_" + side,
            )
        )[:32]
    )
    episode = context["position_episode"]
    require(
        type(episode) is dict and episode["status"] == "REPORTED_FILL_START" and episode["client_order_id"] == client,
        "GOLD_DELEGATED_EXIT_OWNERSHIP",
    )
    reservations = [row for row in state["reservations"] if row["record_hash"] == episode["reservation_sha256"]]
    require(
        len(reservations) == 1 and len(reservations[0]["payload"]["orders"]) == 1, "GOLD_DELEGATED_EXIT_RESERVATION"
    )
    entry = reservations[0]["payload"]["orders"][0]
    require(
        entry["newClientOrderId"] == client
        and entry["symbol"] == "XAUUSDT"
        and entry["side"] == side
        and entry["reduceOnly"] is False
        and Decimal(entry["quantity"]) == Decimal(definition["size_and_timing"]["quantity"]),
        "GOLD_DELEGATED_EXIT_ENTRY",
    )
    binding = dict(
        account_ref_sha256=origin["account_ref_sha256"],
        definition_hash=definition["definition_hash"],
        entry_client_order_id=client,
        reservation_sha256=episode["reservation_sha256"],
        opened_venue_at_ms=episode["opened_venue_at_ms"],
    )
    demo_bar_exit_trigger.validate(trigger, binding, now_ms)
    _fields(trigger_evidence, "origin context prefix pilot")
    old_at = trigger["observed_at_ms"]
    old_origin = trigger_evidence["origin"]
    old_context = trigger_evidence["context"]
    require(
        trigger["origin_sha256"] == _digest(old_origin)
        and trigger["position_context_sha256"] == _digest(old_context)
        and old_context["position_episode"] == episode,
        "GOLD_DELEGATED_TRIGGER_ORIGINALS",
    )
    account = position.validate_origin(old_origin, old_context, as_of_ms=old_at)
    old_pilot = _pilot(
        trigger_evidence["pilot"], terms=terms, origin=old_origin, account=account, observed_at_ms=old_at
    )
    old_replay = replay_prefix(
        trigger_evidence["prefix"], runtime=runtime, definition=definition, observed_at_ms=old_at
    )
    reason, *_ = _exit_reason(
        definition=definition, replay=old_replay, origin=old_origin, pilot=old_pilot, context=old_context, now_ms=old_at
    )
    require(reason == trigger["reason"] and reason is not None, "GOLD_DELEGATED_TRIGGER_REASON")
    _, price, average, stop_price, lower = _exit_reason(
        definition=definition, replay=replay, origin=origin, pilot=pilot, context=context, now_ms=now_ms
    )
    order = dict(
        symbol="XAUUSDT",
        side="SELL" if quantity > 0 else "BUY",
        type="LIMIT",
        timeInForce="IOC",
        quantity=canonical_decimal(abs(quantity)),
        price=canonical_decimal(price),
        reduceOnly=True,
        positionSide="BOTH",
        newClientOrderId="QD"
        + _digest(
            dict(entry_client_order_id=client, ledger_state_hash=context["ledger_state_hash"], action="REDUCE_" + side)
        )[:32],
        newOrderRespType="ACK",
    )
    validate_orders([order], schema=GOLD_BATCH_SCHEMA)
    _venue_rules([order], origin)
    _venue_account_cap([order], origin, context)
    draft = dict(
        schema="GOLD_BAR_DEMO_EXIT_DRAFT_V1",
        environment="BINANCE_USDM_DEMO",
        definition_hash=definition["definition_hash"],
        trial_id=definition["trial_id"],
        origin_sha256=_digest(origin),
        position_context_sha256=_digest(context),
        observed_at_ms=now_ms,
        orders=[order],
        submission_enabled=False,
        economic_authority=False,
        risk_status="NOT_RUN",
        authorization_status="BAR_STRATEGY_ENROLLMENT_REQUIRED",
        exit_trigger_sha256=trigger["record_hash"],
        pilot_control=pilot,
        average_entry_price=canonical_decimal(average),
        stop_price=canonical_decimal(stop_price),
        position_episode=episode,
        valid_until_ms=lower + definition["size_and_timing"]["max_quote_age_ms"],
        status="BAR_EXIT_DRAFT",
        reason=reason,
        required_before_execution=[
            "BAR_STRATEGY_ENROLLMENT",
            "INDEPENDENT_RISK_AND_SIGNATURE",
            "FRESH_ORIGINAL_POSITION_RECHECK",
            "ORIGINAL_DISPATCH_DEDUPLICATION",
            "CONTROLLER_RESTORE_OF_ORIGINAL_BAR_AND_EXECUTION_HISTORY",
        ],
    )
    return dict(draft, result_hash=_digest(draft))
