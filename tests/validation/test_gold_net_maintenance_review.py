"""Maintenance consumes original V2 cost-net accounts; no invented IC or healthy defaults."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D, localcontext
from functools import lru_cache
import pytest
from quantos.contracts.registered_research_run import digest
from tests.execution.test_perpetual_long_replay import DAY, NAME, T, budget, environment, market
from services.execution.perpetual_simulation import LinearPerpetualSimulationSession, OrderIntent
from tests.validation.test_gold_net_forward_review import WEEK, policy, assess, episode


@lru_cache(maxsize=12)
def maintenance_account(start=T, weekly_rates=("-0.01",) * 4):
    span = len(weekly_rates) * WEEK
    active = True
    """Actual V2 execution: fees and received funding reduce constant-price cash."""
    from tests.execution.test_perpetual_environment_epochs import configuration
    from services.validation.gold_account_risk_inputs import build_account_risk_inputs

    shift = start - T
    settings = environment(0)
    settings["contract_rules"] = tuple(
        replace(
            r,
            effective_from=start - 100,
            effective_until=start + span + 100,
            observed_time=start - 3,
            available_time=start - 2,
            ingestion_time=start - 1,
        )
        for r in settings["contract_rules"]
    )
    times = tuple(start + i * 8 * 3_600_000 for i in range(span // (8 * 3_600_000) + 1))
    settings["funding_policies"] = tuple(
        replace(
            p,
            coverage_start=start - 100,
            coverage_end=start + span + 100,
            settlement_times=times,
            observed_time=start - 3,
            available_time=start - 2,
            ingestion_time=start - 1,
        )
        for p in settings["funding_policies"]
    )
    settings["order_rule_profiles"] = tuple(
        replace(
            p,
            effective_from=start - 100,
            effective_until=start + span + 100,
            observed_time=start - 3,
            available_time=start - 2,
            ingestion_time=start - 1,
        )
        for p in settings["order_rule_profiles"]
    )

    def trade(ctx):
        if not active:
            return ()
        if ctx.timestamp == start:
            return (OrderIntent("entry", NAME, D(".1")),)
        if ctx.timestamp == start + span - 10:
            return (OrderIntent("exit", NAME, D("-.1"), reduce_only=True),)
        return ()

    session = LinearPerpetualSimulationSession(
        **dict(configuration(trade), **settings, replay_budget=budget(epochs=1, span=span + 1))
    )
    offsets = sorted({0, 10, span - 10, span, *range(8 * 3_600_000, span + 1, 8 * 3_600_000)})
    from services.execution.perpetual_ledger_v2 import FundingEvent, MarkEvent

    def rate(t):
        return D(weekly_rates[min((t - start) // WEEK, len(weekly_rates) - 1)])

    frames = []
    for i in offsets:
        f = market(shift + i, funding=False)
        events = (
            *((FundingEvent(start + i, NAME, rate(start + i), D("100")),) if start + i in times else ()),
            MarkEvent(start + i, NAME, D("100")),
        )
        frames.append(replace(f, market_events=events))
    session.advance(tuple(frames))
    simulation = session.report()
    assert simulation["ending_state"]["positions"] == {}
    assert simulation["reconciliation"]["residual"] == "0"
    settlements = [
        dict(
            settlement_at_ms=t,
            requested_at_ms=t,
            available_at_ms=t,
            instrument_id=NAME,
            funding_rate=str(rate(t)),
            mark_price="100",
        )
        for t in times
    ]
    from tests.validation.test_gold_research_risk_policy import policy as research_risk_policy

    return build_account_risk_inputs(
        simulation,
        sample=dict(start_ms=start, end_ms=start + span, frequency_ms=DAY),
        settlements=settlements,
        account_currency="USDT",
        research_risk_policy=research_risk_policy(start=start, end=start + span, frequency=DAY, declared=start - 10),
    )


def cost_episode(start, rates):
    value = episode(start, span=len(rates) * WEEK)
    return replace(value, account=maintenance_account(start, rates))


def review(rates=("-0.01",) * 4):
    return assess(
        [cost_episode(T, ("-0.01",) * 4), cost_episode(T + 4 * WEEK, rates)], snapshot=T + (4 + len(rates)) * WEEK
    )


def evaluate(forward=None, *, absent=()):
    from quantos.contracts.gold_net_maintenance_policy import declare
    from services.validation.gold_net_maintenance_review import assess_maintenance

    forward = review() if forward is None else forward
    p = policy(start=T + 4 * WEEK, span=forward["episodes"][-1]["sample"]["end_ms"] - T - 4 * WEEK)
    maintenance = declare(net_forward_policy=p)
    rows = []
    for receipt in forward["episodes"]:
        fp = policy(receipt["sample"]["start_ms"], receipt["sample"]["end_ms"] - receipt["sample"]["start_ms"])
        mp = declare(net_forward_policy=fp)
        rows.append(
            dict(
                **{k: receipt[k] for k in ("invocation_id", "native_trial_id", "config_hash")},
                available=receipt["invocation_id"] not in absent,
                sample=receipt["sample"],
                forward_policy_sha256=receipt["policy_sha256"],
                policy_sha256=digest(mp),
                series_sha256=maintenance["series_sha256"],
                declared_at_ms=mp["declared_at_ms"],
                design_sha256="7" * 64,
            )
        )
    subject = {k: forward[k] for k in ("invocation_id", "native_trial_id", "config_hash")}
    return assess_maintenance(
        net_forward_review=forward,
        net_forward_policy=p,
        policy=maintenance,
        subject=subject,
        candidate_id="channel",
        native_result_hash=forward["native_result_hash"],
        project_cut_sha256=forward["project_cut_sha256"],
        snapshot_as_of_ms=forward["snapshot_as_of_ms"],
        declarations=rows,
    )


def test_actual_net_accounts_produce_bound_maintenance_without_health_authority():
    f = review()
    result = evaluate(f)
    assert result["status"] == "PASS" and result["decay_flag"] is False
    assert result["baseline_period_indices"] == [0, 1, 2, 3]
    assert result["recent_period_indices"] == [5, 6, 7]
    assert D(result["baseline_mean_net_return"]) > 0
    assert result["net_forward_review_sha256"] == f["review_sha256"]
    assert result["independent_verification"] is result["economic_authority"] is result["submission_enabled"] is False
    assert result["lifecycle_changed"] is False


@pytest.mark.parametrize("rates", [("0.001",) * 3, ("0.001",) * 4, ("0.001",) * 3 + ("-0.01",)])
def test_three_realized_bad_weeks_confirm_decay_and_later_recovery_does_not_erase_it(rates):
    result = evaluate(review(rates))
    assert result["status"] == "FAIL" and result["decay_flag"] is True
    assert result["first_confirmed_decay_period_indices"] == [4, 5, 6]
    assert "SUSTAINED_COST_NET_PERFORMANCE_GAP" in result["reasons"]


def test_one_bad_week_is_pending_and_cannot_become_a_healthy_default():
    result = evaluate(review(("-0.01",) * 3 + ("0.001",)))
    assert result["status"] == "INSUFFICIENT" and result["decay_flag"] is None
    assert "PENDING_DECAY_CONFIRMATION" in result["reasons"]


def test_nonpositive_original_baseline_never_becomes_healthy():
    result = evaluate(assess())
    assert result["status"] == "INSUFFICIENT" and result["decay_flag"] is None
    assert "POSITIVE_ORIGINAL_NET_BASELINE_REQUIRED" in result["reasons"]


def test_original_declaration_missing_never_becomes_healthy():
    f = review()
    result = evaluate(f, absent=(f["episodes"][0]["invocation_id"],))
    assert result["status"] == "INSUFFICIENT" and result["decay_flag"] is None
    assert "PREREGISTERED_MAINTENANCE_DECLARATIONS_REQUIRED" in result["reasons"]


@pytest.mark.parametrize("precision", [6, 28, 80])
def test_cost_net_maintenance_ignores_ambient_decimal_precision(precision):
    expected = evaluate()
    with localcontext() as c:
        c.prec = precision
        assert evaluate() == expected


@pytest.mark.parametrize("damage", ["gap", "confirm", "baseline", "authority", "forward", "time"])
def test_policy_cannot_weaken_thresholds_or_change_original_forward_binding(damage):
    from quantos.contracts.gold_net_maintenance_policy import declare, validate

    fp = policy(T + 4 * WEEK)
    p = deepcopy(declare(net_forward_policy=fp))
    if damage == "gap":
        p["method"]["maximum_performance_gap"] = "0.9"
    elif damage == "confirm":
        p["method"]["confirm_periods"] = True
    elif damage == "baseline":
        p["method"]["baseline_periods"] = 1
    elif damage == "authority":
        p["economic_authority"] = True
    elif damage == "forward":
        p["net_forward_policy_sha256"] = "8" * 64
    else:
        p["declared_at_ms"] += 1
    with pytest.raises(ValueError):
        validate(p, net_forward_policy=fp)


def test_rehashed_forged_healthy_flag_is_rejected_before_model_use():
    from quantos.contracts.gold_net_maintenance_policy import declare, validate_review
    from quantos.streaming_identity import exact_stream_identity_hash

    f = review(("0.001",) * 4)
    r = evaluate(f)
    r.update(status="PASS", decay_flag=False, reasons=[])
    r["review_sha256"] = exact_stream_identity_hash({k: v for k, v in r.items() if k != "review_sha256"})
    p = policy(T + 4 * WEEK)
    with pytest.raises(ValueError):
        validate_review(
            r,
            policy=declare(net_forward_policy=p),
            net_forward_policy=p,
            net_forward_review=f,
            subject={k: f[k] for k in ("invocation_id", "native_trial_id", "config_hash")},
            candidate_id="channel",
            native_result_hash=f["native_result_hash"],
            project_cut_sha256=f["project_cut_sha256"],
            snapshot_as_of_ms=f["snapshot_as_of_ms"],
        )


def test_decay_finding_enters_original_model_even_with_other_missing_facts():
    from quantos.contracts.gold_net_maintenance_policy import declare
    from services.validation.gold_model_risk_inputs import from_verified_statistics
    from tests.validation.test_gold_model_risk_inputs import context

    f = review(("0.001",) * 4)
    r = evaluate(f)
    cut, _, statistics, _ = context()
    cut["cut_sha256"] = f["project_cut_sha256"]
    subject = {k: f[k] for k in ("invocation_id", "native_trial_id", "config_hash")}
    statistics["report"].update(
        selected_attempt=subject, project_cut_sha256=cut["cut_sha256"], as_of_ms=f["snapshot_as_of_ms"]
    )
    fp = policy(T + 4 * WEEK)
    result = from_verified_statistics(
        project_cut=cut,
        subject=subject,
        candidate_id="channel",
        verified_statistics=statistics,
        checked_at_ms=f["snapshot_as_of_ms"],
        native_result_hash=f["native_result_hash"],
        net_forward_policy=fp,
        net_forward_review=f,
        maintenance_policy=declare(net_forward_policy=fp),
        maintenance_review=r,
    )
    assert result.projection["schema"] == "QUANTOS_GOLD_MODEL_RISK_INPUTS_V4"
    assert result.projection["facts"]["decay_flag"]["value"] is True
    assert "decay_flag" in result.projection["known_failure_fields"]
    assert "leakage_flag" in result.projection["missing_fields"] and result.engine_metadata is None
    from services.risk.research_risk.gold_research_replay import review_gold_account

    inputs = cost_episode(T + 4 * WEEK, ("0.001",) * 4).account
    risk = review_gold_account(replace(inputs, model_inputs=result))
    gate = risk["engines"]["model_alpha"]
    assert gate["status"] == "FAIL" and gate["invocations"] == 0
    assert "ORIGINAL_DECAY_FINDING" in gate["reasons"] and risk["status"] == "REJECTED"


def test_maintenance_recipe_is_preregistered_in_v10_and_cannot_be_added_to_v9():
    from tests.validation.test_gold_causality_policy import net_design
    from quantos.contracts.gold_forward_evaluation_design import MAINTENANCE_REVIEW_SCHEMA, validate
    from quantos.contracts.gold_net_maintenance_policy import declare

    value = net_design()
    old = deepcopy(value)
    value["schema"] = MAINTENANCE_REVIEW_SCHEMA
    value["risk_policy"]["maintenance_policy"] = declare(net_forward_policy=value["risk_policy"]["net_forward_policy"])
    assert validate(value) == value and validate(old) == old
    value["schema"] = old["schema"]
    with pytest.raises(ValueError):
        validate(value)


def test_source_port_rejects_forward_report_not_issued_by_its_own_lease():
    from services.research.gold_native_source_reader import SourceReports

    owner = SourceReports()
    owner.source_inputs["1" * 64] = [{}]
    with pytest.raises(ValueError, match="ORIGINAL_FORWARD_NOT_ISSUED"):
        owner.review_gold_maintenance(
            subject=dict(invocation_id="1" * 64), net_forward_review=dict(review_sha256="2" * 64)
        )
    owner.close_inputs()
    with pytest.raises(ValueError, match="CONTEXT_CLOSED"):
        owner.review_gold_maintenance()


def test_exact_original_performance_gap_boundary_and_missing_week_do_not_confirm_decay():
    from quantos.contracts.gold_net_maintenance_policy import period_assessment

    # Explicit synthetic math seam, no account, independent or trading evidence.
    def period(i, r):
        return dict(index=i, net_return=r, active_position_interval=True)

    p = [period(i, "0.01") for i in range(4)] + [period(i, "0.004") for i in range(4, 8)]
    assert period_assessment(dict(status="MATURE", periods=p), declarations_ready=True)["decay_flag"] is False
    p = [period(i, "0.01") for i in range(4)] + [
        period(i, "0.003999999999999999999999999999999999999") for i in (4, 6, 7)
    ]
    r = period_assessment(dict(status="MATURE", periods=p), declarations_ready=True)
    assert r["status"] == "INSUFFICIENT" and r["decay_flag"] is None
    assert not r["first_confirmed_decay_period_indices"]


@pytest.mark.parametrize("damage", ["native", "candidate", "cut", "future", "period", "policy"])
def test_review_cannot_move_to_a_different_original_or_snapshot(damage):
    from quantos.contracts.gold_net_maintenance_policy import declare, validate_review
    from quantos.streaming_identity import exact_stream_identity_hash

    f = review()
    r = evaluate(f)
    p = policy(T + 4 * WEEK)
    if damage == "native":
        r["native_result_hash"] = "9" * 64
    elif damage == "candidate":
        r["candidate_id"] = "trend"
    elif damage == "cut":
        r["project_cut_sha256"] = "9" * 64
    elif damage == "future":
        r["snapshot_as_of_ms"] += 1
    elif damage == "period":
        r["period_assessments"][0]["original_period_sha256"] = "9" * 64
    else:
        r["declarations"][-1]["policy_sha256"] = "9" * 64
    r["review_sha256"] = exact_stream_identity_hash({k: v for k, v in r.items() if k != "review_sha256"})
    with pytest.raises(ValueError):
        validate_review(
            r,
            policy=declare(net_forward_policy=p),
            net_forward_policy=p,
            net_forward_review=f,
            subject={k: f[k] for k in ("invocation_id", "native_trial_id", "config_hash")},
            candidate_id="channel",
            native_result_hash=f["native_result_hash"],
            project_cut_sha256=f["project_cut_sha256"],
            snapshot_as_of_ms=f["snapshot_as_of_ms"],
        )


def test_sparse_far_future_week_is_insufficient_with_bounded_memory():
    import os
    import subprocess
    import sys

    code = """import resource
resource.setrlimit(resource.RLIMIT_AS, (256*1024*1024,256*1024*1024))
from quantos.contracts.gold_net_maintenance_policy import period_assessment
p=[dict(index=i,net_return="0.01",active_position_interval=True) for i in range(7)]
p.append(dict(index=100_000_000,net_return="0.01",active_position_interval=True))
r=period_assessment(dict(status="MATURE",periods=p),declarations_ready=True)
assert r["status"]=="INSUFFICIENT" and r["decay_flag"] is None
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
