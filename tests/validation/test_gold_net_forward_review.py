"""Forward maturity uses separate original cost-net accounts, never gross marks."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D, localcontext
from functools import lru_cache

import pytest

from quantos.contracts.registered_research_run import digest, native_trial_id
from tests.execution.test_perpetual_long_replay import DAY, NAME, T, budget, environment, market
from services.execution.perpetual_simulation import LinearPerpetualSimulationSession, OrderIntent

WEEK = 7 * DAY
SERIES = dict(
    start_ms=T,
    declared_at_ms=T - 10,
    candidate_id="channel",
    strategy_definition_sha256="1" * 64,
    proposal_sha256="2" * 64,
)


def policy(start=T, span=4 * WEEK):
    from quantos.contracts.gold_net_forward_policy import declare

    return declare(
        sample=dict(start_ms=start, end_ms=start + span, frequency_ms=DAY), declared_at_ms=start - 10, series=SERIES
    )


@lru_cache(maxsize=4)
def original_account(start=T, span=4 * WEEK, active=True):
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
    session.advance(tuple(market(shift + i, funding=(start + i in times)) for i in offsets))
    simulation = session.report()
    assert simulation["ending_state"]["positions"] == {}
    assert simulation["reconciliation"]["residual"] == "0"
    settlements = [
        dict(
            settlement_at_ms=t,
            requested_at_ms=t,
            available_at_ms=t,
            instrument_id=NAME,
            funding_rate="0.001",
            mark_price="100",
        )
        for t in times
    ]
    return build_account_risk_inputs(
        simulation,
        sample=dict(start_ms=start, end_ms=start + span, frequency_ms=DAY),
        settlements=settlements,
        account_currency="USDT",
    )


def episode(start=T, span=4 * WEEK, active=True):
    from services.validation.gold_net_forward_review import NetForwardEpisode

    invocation = digest(dict(start=start, span=span, active=active))
    config = digest(dict(invocation=invocation))
    subject = dict(
        invocation_id=invocation, config_hash=config, native_trial_id=native_trial_id(invocation, "channel", config)
    )
    return NetForwardEpisode(
        subject=subject,
        policy=policy(start, span),
        account=original_account(start, span, active),
        strategy_definition_sha256=SERIES["strategy_definition_sha256"],
        proposal_sha256=SERIES["proposal_sha256"],
    )


def assess(episodes=None, *, snapshot=T + 8 * WEEK, incomplete=()):
    from services.validation.gold_net_forward_review import assess_net_forward

    values = (episode(), episode(T + 4 * WEEK)) if episodes is None else tuple(episodes)
    selected = values[-1] if values else episode()
    return assess_net_forward(
        episodes=values,
        policy=selected.policy,
        subject=selected.subject,
        native_result_hash=selected.account.projection["native_result_hash"],
        project_cut_sha256="3" * 64,
        snapshot_as_of_ms=snapshot,
        incomplete_invocations=incomplete,
    )


def test_eight_nonoverlapping_weeks_use_two_original_net_accounts_without_cash_concatenation():
    result = assess()
    assert result["status"] == "MATURE" and result["completed_active_periods"] == 8
    assert result["required_periods"] == 8 and result["paper_confidence"] == "1"
    assert len(result["episodes"]) == 2 and len(result["periods"]) == 8
    assert all(D(p["net_return"]) < 0 for p in result["periods"])
    assert result["account_mode"] == "SEPARATE_ORIGINAL_EPISODES_NO_CASH_CONCATENATION"
    assert result["independent_verification"] is result["economic_authority"] is result["submission_enabled"] is False
    assert result["maturity_is_profit_probability"] is False
    assert all(e["original_cash_identity_sha256"] for e in result["episodes"])
    from quantos.streaming_identity import exact_stream_identity_hash

    receipts = {exact_stream_identity_hash(e) for e in result["episodes"]}
    assert all(p["account_receipt_sha256"] in receipts for p in result["periods"])


def test_changed_net_vector_cannot_keep_the_original_source_group_hash():
    value = episode()
    changed = (D("999"), *value.account.equity_curve[1:])
    value = replace(value, account=replace(value.account, equity_curve=changed))
    with pytest.raises(ValueError, match="GRID_BINDING"):
        assess([value])


def test_selected_subject_cannot_reuse_a_policy_from_a_different_episode():
    from services.validation.gold_net_forward_review import assess_net_forward

    first, second = episode(), episode(T + 4 * WEEK)
    with pytest.raises(ValueError, match="SELECTED_POLICY_BINDING"):
        assess_net_forward(
            episodes=(first, second),
            policy=first.policy,
            subject=second.subject,
            native_result_hash=second.account.projection["native_result_hash"],
            project_cut_sha256="3" * 64,
            snapshot_as_of_ms=T + 8 * WEEK,
        )


def test_one_28_day_capture_does_not_claim_eight_week_maturity():
    result = assess([episode()])
    assert result["completed_active_periods"] == 4 and result["status"] == "INSUFFICIENT"
    assert result["normalized_maturity"] == "0.5" and result["paper_confidence"] is None


def test_idle_weeks_do_not_turn_zero_trading_into_forward_maturity():
    result = assess([episode(active=False)])
    assert result["completed_active_periods"] == 0 and result["paper_confidence"] is None


def test_partial_tail_does_not_count_as_a_complete_week():
    result = assess([episode(span=4 * WEEK - DAY)])
    assert result["completed_active_periods"] == 3


def test_missing_origin_declaration_cannot_establish_a_series_retroactively():
    result = assess([episode(T + 4 * WEEK)])
    assert result["status"] == "INSUFFICIENT" and result["paper_confidence"] is None
    assert "ORIGINAL_SERIES_DECLARATION_MISSING" in result["reasons"]


def test_overlapping_original_runs_cannot_double_count_the_same_weeks():
    with pytest.raises(ValueError, match="OVERLAPPING_PERIOD"):
        assess([episode(), episode(T + 2 * WEEK)])


@pytest.mark.parametrize("damage", ["candidate", "definition", "proposal", "trial", "native", "future", "authority"])
def test_wrong_candidate_or_changed_original_identity_never_becomes_confidence(damage):
    first, second = episode(), episode(T + 4 * WEEK)
    if damage == "candidate":
        p = deepcopy(second.policy)
        p["series"]["candidate_id"] = "trend"
        second = replace(second, policy=p)
    elif damage == "definition":
        second = replace(second, strategy_definition_sha256="4" * 64)
    elif damage == "proposal":
        second = replace(second, proposal_sha256="4" * 64)
    elif damage == "trial":
        second = replace(second, subject=second.subject | dict(native_trial_id="4" * 64))
    else:
        p = deepcopy(second.account.projection)
        if damage == "native":
            p["native_result_hash"] = "wrong"
        elif damage == "future":
            p["last_available_at_ms"] = T + 8 * WEEK + 1
        else:
            p["submission_enabled"] = True
        second = replace(second, account=replace(second.account, projection=p))
    with pytest.raises(ValueError):
        assess([first, second])


@pytest.mark.parametrize("damage", ["coverage", "grid", "positions", "missing_episode"])
def test_missing_net_originals_remain_insufficient(damage):
    first, second = episode(), episode(T + 4 * WEEK)
    options = {}
    if damage == "missing_episode":
        options["incomplete"] = ("5" * 64,)
    elif damage == "positions":
        second = replace(second, account=replace(second.account, position_history=None))
    elif damage == "grid":
        second = replace(second, account=replace(second.account, returns=None, equity_curve=None, timestamps=None))
    else:
        p = deepcopy(second.account.projection)
        p["full_run_covered"] = False
        second = replace(second, account=replace(second.account, projection=p))
    result = assess([first, second], **options)
    assert result["status"] == "INSUFFICIENT" and result["paper_confidence"] is None


@pytest.mark.parametrize("precision", [6, 28, 80])
def test_original_net_math_and_receipt_hash_ignore_ambient_decimal_precision(precision):
    expected = assess()
    with localcontext() as c:
        c.prec = precision
        assert assess() == expected


def test_review_is_bound_to_fixed_snapshot_not_later_read_time():
    from services.validation.gold_model_risk_inputs import from_verified_statistics
    from tests.validation.test_gold_model_risk_inputs import context

    review = assess()
    cut, _, statistics, _ = context()
    subject = episode(T + 4 * WEEK).subject
    cut["cut_sha256"] = review["project_cut_sha256"]
    statistics["report"].update(selected_attempt=subject, project_cut_sha256=cut["cut_sha256"], as_of_ms=T + 8 * WEEK)
    arguments = dict(
        project_cut=cut,
        subject=subject,
        candidate_id="channel",
        verified_statistics=statistics,
        native_result_hash=episode(T + 4 * WEEK).account.projection["native_result_hash"],
        net_forward_policy=episode(T + 4 * WEEK).policy,
        net_forward_review=review,
    )
    a = from_verified_statistics(**arguments, checked_at_ms=T + 8 * WEEK)
    b = from_verified_statistics(**arguments, checked_at_ms=T + 9 * WEEK)
    assert a.projection == b.projection
    assert a.projection["schema"] == "QUANTOS_GOLD_MODEL_RISK_INPUTS_V3"
    assert a.projection["facts"]["paper_confidence"]["value"] == "1"
    assert a.projection["missing_fields"] == ["decay_flag", "leakage_flag"]
    assert a.engine_metadata is None


@pytest.mark.parametrize("damage", ["method", "late", "candidate", "threshold", "authority", "bool"])
def test_net_forward_policy_cannot_shorten_or_relabel_the_original_requirement(damage):
    from quantos.contracts.gold_net_forward_policy import validate

    p = deepcopy(policy())
    if damage == "method":
        p["method"]["account_mode"] = "GROSS_PLUS_FUNDING"
    elif damage == "late":
        p["series"]["declared_at_ms"] = p["series"]["start_ms"]
    elif damage == "candidate":
        p["series"]["candidate_id"] = "invented"
    elif damage == "threshold":
        p["method"]["required_periods"] = 4
    elif damage == "bool":
        p["series"]["start_ms"] = True
    else:
        p["independent_verification"] = True
    with pytest.raises(ValueError):
        validate(p)
