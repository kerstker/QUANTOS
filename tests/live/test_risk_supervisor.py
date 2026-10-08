from __future__ import annotations

import json
from dataclasses import replace

import pytest

from live.execution.risk_supervisor import (
    RiskSupervisorSnapshot,
    RiskSupervisorVerdict,
    apply_risk_supervisor_action,
    evaluate_risk_supervisor,
)
from live.governance.mandate import Mandate
from live.governance.mandate_store import (
    MandateLoadError,
    RiskSupervisorPolicy,
    load_risk_supervisor_policy,
)
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore


NOW = 2_000_000


def _mandate(**changes) -> Mandate:
    values = {
        "mandate_id": "signed-testnet-mandate",
        "venue": "testnet",
        "exchange": "binance",
        "allowed_instruments": frozenset({"BTC", "ETH"}),
        "max_single_order_notional": 1_000.0,
        "max_gross_notional": 1_000.0,
        "max_net_notional": 1_000.0,
        "approved_by": ("human-risk",),
        "issued_at": NOW - 10_000,
        "expires_at": NOW + 10_000,
    }
    values.update(changes)
    return Mandate(**values)


def _policy(**changes) -> RiskSupervisorPolicy:
    values = {
        "schema_version": 1,
        "policy_id": "testnet-risk-policy",
        "policy_version": "2026-08-08.v1",
        "max_market_age_ms": 1_000,
        "max_scheduler_age_ms": 5_000,
        "max_drawdown_pct": 0.20,
        "min_margin_ratio": 2.0,
        "max_single_name_fraction": 0.70,
        "max_participation_rate": 0.10,
        "reconciliation_tolerance": 1e-9,
    }
    values.update(changes)
    return RiskSupervisorPolicy(**values)


def _snapshot(**changes) -> RiskSupervisorSnapshot:
    values = {
        "now_ms": NOW,
        "execution_environment": "BINANCE_FUTURES_TESTNET",
        "venue": "testnet",
        "exchange": "binance",
        "market_publishable": True,
        "market_watermark_ms": NOW - 100,
        "scheduler_last_success_ms": NOW - 100,
        "mandate": _mandate(),
        "account_equity": 1_000.0,
        "peak_equity": 1_000.0,
        "positions": {"BTC": 0.5, "ETH": -0.5},
        "prices": {"BTC": 100.0, "ETH": 100.0},
        "maint_margin_balance": 100.0,
        "margin_maint_amount": 25.0,
        "participation_rates": {"BTC": 0.01, "ETH": 0.01},
        "ledger_positions": {"BTC": 0.5, "ETH": -0.5},
        "unknown_order_count": 0,
        "kill_switch_tripped": False,
        "previous_cycle_at_ms": NOW - 1_000,
    }
    values.update(changes)
    return RiskSupervisorSnapshot(**values)


def test_all_complete_inputs_allow_only_testnet_new_risk() -> None:
    decision = evaluate_risk_supervisor(_snapshot(), _policy())

    assert decision.verdict is RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK
    assert decision.reasons == ()
    assert decision.latch_kill_switch is False
    assert decision.automatic_action == "NONE"
    assert decision.policy_hash == _policy().content_hash


@pytest.mark.parametrize(
    "field",
    (
        "market_publishable",
        "market_watermark_ms",
        "scheduler_last_success_ms",
        "mandate",
        "account_equity",
        "peak_equity",
        "positions",
        "prices",
        "maint_margin_balance",
        "margin_maint_amount",
        "participation_rates",
        "ledger_positions",
        "unknown_order_count",
        "kill_switch_tripped",
    ),
)
def test_every_missing_cycle_input_halts_new_risk(field: str) -> None:
    decision = evaluate_risk_supervisor(_snapshot(**{field: None}), _policy())

    assert decision.verdict is RiskSupervisorVerdict.HALT_NEW_RISK
    assert any(reason == f"MISSING_{field.upper()}" for reason in decision.reasons)
    assert decision.automatic_action == "HALT_NEW_RISK"


def test_exact_policy_and_mandate_boundaries_pass() -> None:
    snapshot = _snapshot(
        market_watermark_ms=NOW - 1_000,
        scheduler_last_success_ms=NOW - 5_000,
        mandate=_mandate(expires_at=NOW),
        positions={"BTC": 7.0, "ETH": -3.0},
        prices={"BTC": 100.0, "ETH": 100.0},
        ledger_positions={"BTC": 7.0, "ETH": -3.0},
        maint_margin_balance=50.0,
        margin_maint_amount=25.0,
        participation_rates={"BTC": 0.10},
    )

    decision = evaluate_risk_supervisor(snapshot, _policy())

    assert decision.verdict is RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK
    assert decision.risk_breach.gross_exposure == pytest.approx(1_000.0)
    assert decision.constraint_breach.max_single_name_fraction == pytest.approx(0.70)


@pytest.mark.parametrize(
    ("changes", "verdict", "reason"),
    (
        (
            {"market_watermark_ms": NOW - 1_001},
            RiskSupervisorVerdict.HALT_NEW_RISK,
            "MARKET_WATERMARK_STALE",
        ),
        (
            {"scheduler_last_success_ms": NOW - 5_001},
            RiskSupervisorVerdict.HALT_NEW_RISK,
            "SCHEDULER_STALE",
        ),
        (
            {"mandate": _mandate(expires_at=NOW - 1)},
            RiskSupervisorVerdict.HALT_NEW_RISK,
            "MANDATE_EXPIRED",
        ),
        (
            {
                "mandate": _mandate(
                    max_single_order_notional=0.0,
                    max_gross_notional=0.0,
                ),
                "positions": {},
                "prices": {},
                "ledger_positions": {},
            },
            RiskSupervisorVerdict.HALT_NEW_RISK,
            "MANDATE_NO_RISK_CAPACITY",
        ),
        (
            {
                "positions": {"BTC": 7.01, "ETH": -3.0},
                "ledger_positions": {"BTC": 7.01, "ETH": -3.0},
            },
            RiskSupervisorVerdict.REDUCE_ONLY_REQUIRED,
            "GROSS_EXPOSURE_EXCEEDED",
        ),
        (
            {
                "positions": {"BTC": 7.01, "ETH": -2.99},
                "ledger_positions": {"BTC": 7.01, "ETH": -2.99},
            },
            RiskSupervisorVerdict.REDUCE_ONLY_REQUIRED,
            "CONCENTRATION_EXCEEDED",
        ),
        (
            {"participation_rates": {"BTC": 0.100001}},
            RiskSupervisorVerdict.HALT_NEW_RISK,
            "PARTICIPATION_EXCEEDED",
        ),
    ),
)
def test_one_step_beyond_each_limit_fails_closed(changes, verdict, reason) -> None:
    decision = evaluate_risk_supervisor(_snapshot(**changes), _policy())

    assert decision.verdict is verdict
    assert any(item.startswith(reason) for item in decision.reasons)


@pytest.mark.parametrize(
    "changes",
    (
        {"market_watermark_ms": NOW + 1},
        {"scheduler_last_success_ms": NOW + 1},
        {"previous_cycle_at_ms": NOW + 1},
    ),
)
def test_time_regression_never_becomes_fresh(changes) -> None:
    decision = evaluate_risk_supervisor(_snapshot(**changes), _policy())

    assert decision.verdict is RiskSupervisorVerdict.HALT_NEW_RISK
    assert any("TIME_REGRESSION" in reason for reason in decision.reasons)


def test_simultaneous_failures_preserve_every_reason_and_choose_manual_review() -> None:
    decision = evaluate_risk_supervisor(
        _snapshot(
            market_publishable=False,
            positions={"BTC": 1.0},
            prices={"BTC": 100.0},
            ledger_positions={"BTC": 0.0},
            unknown_order_count=2,
        ),
        _policy(),
    )

    assert decision.verdict is RiskSupervisorVerdict.MANUAL_REVIEW
    assert decision.latch_kill_switch is True
    assert "MARKET_NOT_PUBLISHABLE" in decision.reasons
    assert "POSITION_LEDGER_RECONCILIATION_FAILED" in decision.reasons
    assert "UNKNOWN_ORDERS_PRESENT:2" in decision.reasons


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        (
            {"maint_margin_balance": 49.9, "margin_maint_amount": 25.0},
            "MARGIN_RATIO_BELOW_FLOOR",
        ),
        (
            {"ledger_positions": {"BTC": 0.0, "ETH": -0.5}},
            "POSITION_LEDGER_RECONCILIATION_FAILED",
        ),
        ({"unknown_order_count": 1}, "UNKNOWN_ORDERS_PRESENT"),
    ),
)
def test_severe_failures_latch_persistent_kill_switch(tmp_path, changes, reason) -> None:
    store = KillSwitchStateStore(str(tmp_path / "kill.db"), create=True)
    decision = evaluate_risk_supervisor(_snapshot(**changes), _policy())

    action = apply_risk_supervisor_action(decision, store=store)

    assert any(item.startswith(reason) for item in decision.reasons)
    assert decision.latch_kill_switch is True
    assert action["action"] == "kill_switch_armed"
    assert store.load_state().tripped is True
    store.close()


def test_healthy_followup_cannot_reset_or_weaken_latched_kill_switch(tmp_path) -> None:
    store = KillSwitchStateStore(str(tmp_path / "kill.db"), create=True)
    severe = evaluate_risk_supervisor(
        _snapshot(unknown_order_count=1), _policy()
    )
    apply_risk_supervisor_action(severe, store=store)
    incident_id = store.load_state().incident_id

    healthy = evaluate_risk_supervisor(
        _snapshot(kill_switch_tripped=True), _policy()
    )
    action = apply_risk_supervisor_action(healthy, store=store)

    assert healthy.verdict is RiskSupervisorVerdict.HALT_NEW_RISK
    assert action["action"] == "none"
    assert store.load_state().tripped is True
    assert store.load_state().incident_id == incident_id
    store.close()


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"positions": {"BTC": float("nan")}}, "POSITION_QUANTITY_INVALID"),
        ({"maint_margin_balance": float("nan")}, "MAINT_MARGIN_BALANCE_INVALID"),
        ({"margin_maint_amount": -1.0}, "MARGIN_MAINT_AMOUNT_INVALID"),
    ),
)
def test_malformed_values_return_halt_instead_of_raising(changes, reason) -> None:
    malformed = replace(_snapshot(), **changes)

    decision = evaluate_risk_supervisor(malformed, _policy())

    assert decision.verdict is RiskSupervisorVerdict.HALT_NEW_RISK
    assert any(reason in item for item in decision.reasons)


def test_versioned_policy_loader_has_no_business_number_defaults(tmp_path) -> None:
    path = tmp_path / "mandate.json"
    policy = {
        "schema_version": 1,
        "policy_id": "signed-policy",
        "policy_version": "v7",
        "max_market_age_ms": 2_000,
        "max_scheduler_age_ms": 10_000,
        "max_drawdown_pct": 0.15,
        "min_margin_ratio": 2.5,
        "max_single_name_fraction": 0.40,
        "max_participation_rate": 0.05,
        "reconciliation_tolerance": 1e-8,
    }
    path.write_text(
        json.dumps({"risk_supervisor_policy": policy}), encoding="utf-8"
    )

    loaded = load_risk_supervisor_policy(str(path))

    assert loaded.policy_id == "signed-policy"
    assert loaded.policy_version == "v7"
    assert len(loaded.content_hash) == 64

    del policy["max_participation_rate"]
    path.write_text(
        json.dumps({"risk_supervisor_policy": policy}), encoding="utf-8"
    )
    with pytest.raises(MandateLoadError, match="max_participation_rate"):
        load_risk_supervisor_policy(str(path))


@pytest.mark.parametrize(
    "policy_change",
    (
        {"policy_version": ""},
        {"max_market_age_ms": 0},
        {"max_drawdown_pct": float("nan")},
        {"max_single_name_fraction": 1.1},
        {"reconciliation_tolerance": -1.0},
    ),
)
def test_invalid_direct_policy_is_rejected(policy_change) -> None:
    with pytest.raises((TypeError, ValueError)):
        _policy(**policy_change)
