"""Preregistered raw-market states are known at return-interval START."""

import copy
from decimal import localcontext

import pytest

from tests.contracts.test_gold_forward_evaluation_design import START, INTERVAL

SAMPLE = dict(start_ms=START, end_ms=START + 40000, frequency_ms=10000)


def policy(sample=None, *, declared=None):
    from quantos.contracts.gold_market_state_policy import POLICY, SCHEMA

    return dict(
        schema=SCHEMA,
        declared_at_ms=(sample or SAMPLE)["start_ms"] - INTERVAL - 180000 if declared is None else declared,
        sample=dict(sample or SAMPLE),
        method=copy.deepcopy(POLICY),
        independent_verification=False,
        economic_authority=False,
        submission_enabled=False,
    )


def bars(count=160, *, start=START):
    result = []
    for i in range(count):
        close = start - (count - i - 1) * INTERVAL
        result.append(
            dict(
                open_ms=close - INTERVAL,
                close_ms=close,
                available_ms=max(close, start - 5000),
                open="100",
                high="101",
                low="99",
                close="100",
            )
        )
    return result


def inputs(values, *, sample=None, available=True):
    from services.validation.gold_market_states import build_market_state_inputs

    return build_market_state_inputs(
        policy(sample),
        market_bars=values,
        market_history_policy="GOLD_ORIGINAL_RECEIPT_AVAILABLE_BARS_V1",
        sample=dict(sample or SAMPLE),
        account_returns_available=available,
        maximum_bars=100000,
        maximum_intervals=100000,
    )


def test_labels_use_received_market_prices_at_interval_start_and_original_method():
    b = bars()
    b[-1].update(available_ms=START + 5000, close="200", high="201")
    value = inputs(b)
    assert value.labels == ("VOL_RELATIVE_0", "VOL_RELATIVE_1", "VOL_RELATIVE_1", "VOL_RELATIVE_1")
    observed = list(value.observations.items())
    assert observed[0]["return_end_ms"] == START + 10000 and observed[0]["decision_at_ms"] == START
    assert observed[0]["latest_bar"]["available_ms"] <= START
    assert observed[1]["latest_bar"]["available_ms"] == START + 5000
    assert value.projection["label_clock"] == "RETURN_INTERVAL_START_RECEIPT_AVAILABLE"
    assert value.projection["independent_verification"] is False
    assert value.projection["economic_authority"] is value.projection["submission_enabled"] is False


def test_future_prices_cannot_change_earlier_market_labels_or_observation_identity():
    before = bars()
    before[-1]["available_ms"] = START + 5000
    after = copy.deepcopy(before)
    after[-1].update(close="300", high="301")
    a, b = inputs(before), inputs(after)
    assert list(a.observations.items())[0] == list(b.observations.items())[0]
    assert a.labels[0] == b.labels[0]
    assert a.labels[1] != b.labels[1]


@pytest.mark.parametrize("reason", ["warmup", "gap", "stale", "empty", "account"])
def test_missing_evidence_never_defaults_cleans_or_shortens_the_return_axis(reason):
    b = bars()
    sample = dict(SAMPLE)
    available = True
    if reason == "warmup":
        b = bars(10)
    elif reason == "gap":
        b = b[:-25] + b[-1:]
    elif reason == "stale":
        sample.update(end_ms=START + 3 * INTERVAL, frequency_ms=INTERVAL)
    elif reason == "empty":
        b = []
    else:
        available = False
    value = inputs(b, sample=sample, available=available)
    assert value.labels is None
    assert value.projection["status"].startswith("UNAVAILABLE_")
    assert value.projection["label_values_sha256"] is None
    if available:
        if reason == "stale":
            assert value.projection["missing_intervals"] > 0
        else:
            assert (
                value.projection["missing_intervals"]
                == (sample["end_ms"] - sample["start_ms"]) // sample["frequency_ms"]
            )


@pytest.mark.parametrize("damage", ["future_close", "duplicate", "clock", "nan", "ohlc", "policy", "sample", "late"])
def test_malformed_originals_or_posthoc_policy_reject(damage):
    b = bars()
    p = policy()
    sample = dict(SAMPLE)
    if damage == "future_close":
        b[-1]["available_ms"] = b[-1]["close_ms"] - 1
    elif damage == "duplicate":
        b.append(copy.deepcopy(b[-1]))
    elif damage == "clock":
        b[-1]["available_ms"] = True
    elif damage == "nan":
        b[-1]["close"] = "NaN"
    elif damage == "ohlc":
        b[-1]["close"] = "999"
    elif damage == "policy":
        p["method"]["label_clock"] = "RETURN_INTERVAL_END"
    elif damage == "sample":
        sample["frequency_ms"] = 20000
    else:
        p["declared_at_ms"] = START
    from services.validation.gold_market_states import build_market_state_inputs

    with pytest.raises(ValueError):
        build_market_state_inputs(
            p,
            market_bars=b,
            market_history_policy="GOLD_ORIGINAL_RECEIPT_AVAILABLE_BARS_V1",
            sample=sample,
            account_returns_available=True,
            maximum_bars=100000,
            maximum_intervals=100000,
        )


@pytest.mark.parametrize("precision", [6, 28, 80])
def test_lazy_cold_views_ignore_decimal_context_and_replay_same_state(precision):
    b = bars()
    b[-1].update(available_ms=START + 5000, close="150", high="151")
    expected = inputs(b)
    with localcontext() as context:
        context.prec = precision
        actual = inputs(copy.deepcopy(b))
        lazy = list(actual.observations.items())
    assert actual.projection == expected.projection
    assert actual.labels == expected.labels
    assert lazy == list(expected.observations.items())


def test_account_risk_receives_full_aligned_labels_from_original_market_not_strategy_pnl(monkeypatch):
    from tests.validation.test_gold_net_attribution import portfolio
    from tests.research.test_gold_v2_strategy import T
    from services.validation.gold_account_risk_inputs import build_account_risk_inputs

    p = portfolio(monkeypatch)
    sample = dict(start_ms=T, end_ms=T + 40, frequency_ms=10)
    value = build_account_risk_inputs(
        p["simulation"],
        portfolio=p,
        sample=sample,
        settlements=[],
        account_currency="USDT",
        market_state_policy=policy(sample),
        market_bars=bars(start=T),
        market_history_policy="GOLD_ORIGINAL_RECEIPT_AVAILABLE_BARS_V1",
    )
    assert value.market_states.labels == ("VOL_RELATIVE_0",) * len(value.returns)
    assert value.projection["source_groups"]["regime_labels"]["status"] == "AVAILABLE"
    assert value.projection["schema"] == "QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V5"
    assert value.projection["full_research_risk_ready"] is False


@pytest.mark.parametrize("labels", [[], ["only_one"], ["ok", None], ["ok", True], ["ok", ""], ["ok", "other", "extra"]])
def test_original_regime_engine_rejects_missing_or_misaligned_labels(labels):
    from services.risk.research_risk.domain.risk_modules import RegimeRiskEngine
    from services.risk.research_risk.domain.models import RiskConfig

    report = RegimeRiskEngine().evaluate([0.01, -0.01], labels, RiskConfig())
    assert report.passed is False


def test_market_classifier_recipe_is_bound_before_observation_and_old_design_cannot_inherit_it():
    from quantos.contracts.gold_forward_evaluation_design import declare, validate
    from tests.contracts.test_gold_forward_evaluation_design import SOURCE, CONFIG
    from tests.validation.test_gold_research_risk_policy import policy as risk_policy

    sample = dict(start_ms=START, end_ms=START + 10000, frequency_ms=10000)
    value = declare(
        program_id=1,
        attempt=1,
        source_identity=SOURCE,
        configuration=dict(CONFIG),
        first_close_ms=START,
        window_bars=1,
        declared_at_ms=START - INTERVAL - 180000,
        long_window=True,
        selected_candidate="channel",
        proposal_sha256="a" * 64,
        research_risk_policy=risk_policy(),
        market_state_policy=policy(sample),
    )
    assert value["schema"] == "QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V6"
    assert validate(value) == value
    value["schema"] = "QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V5"
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize(
    "returns", [[None, -0.01], [float("nan"), -0.01], [float("inf"), -0.01], [True, -0.01], ["0.01", -0.01]]
)
def test_original_regime_engine_does_not_clean_return_axis(returns):
    from services.risk.research_risk.domain.risk_modules import RegimeRiskEngine
    from services.risk.research_risk.domain.models import RiskConfig

    assert RegimeRiskEngine().evaluate(returns, ["calm", "high"], RiskConfig()).passed is False


def test_fixed_market_recipe_is_saved_before_public_capture_or_job(tmp_path, monkeypatch):
    from tests.tools.test_gold_forward_collection import options, prepare
    from tests.validation.test_gold_research_risk_policy import policy as risk_policy

    settings = options()
    window = settings["window_bars"]
    sample = dict(start_ms=START, end_ms=START + (window - 1) * INTERVAL + 10000, frequency_ms=10000)
    coordinator, value = prepare(
        tmp_path / "prospective-market",
        monkeypatch,
        research_risk_policy=risk_policy(start=sample["start_ms"], end=sample["end_ms"], frequency=10000),
        market_state_policy=policy(sample),
    )
    assert value["forward_design"]["schema"] == "QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V6"
    assert coordinator.read_plan(tmp_path / "prospective-market") == value
    assert {p.name for p in (tmp_path / "prospective-market").iterdir()} == {"plan.json"}


def test_latest_price_arriving_at_sample_end_does_not_relabel_any_return():
    b = bars()
    b[-1].update(available_ms=SAMPLE["end_ms"], close="250", high="251")
    value = inputs(b)
    assert value.labels == ("VOL_RELATIVE_0",) * 4
    assert all(row["latest_bar"]["available_ms"] <= row["decision_at_ms"] for row in value.observations.items())


def test_gap_rewarms_after_only_contiguous_received_market_closes():
    values = bars(160)
    del values[-41]
    value = inputs(values)
    assert value.labels == ("VOL_RELATIVE_0",) * 4
    assert list(value.observations.items())[0]["contiguous_closes"] == 40


@pytest.mark.parametrize("damage", ["receipt_order", "bar_budget", "interval_budget", "history_policy"])
def test_source_order_or_resource_budget_rejects_before_labels(damage):
    from services.validation.gold_market_states import build_market_state_inputs

    b = bars()
    bar_budget = 1000
    intervals = 1000
    history = "GOLD_ORIGINAL_RECEIPT_AVAILABLE_BARS_V1"
    if damage == "receipt_order":
        b[-2]["available_ms"] = START + 1
    elif damage == "bar_budget":
        bar_budget = 159
    elif damage == "interval_budget":
        intervals = 3
    else:
        history = "INVENTED_PRICE_HISTORY"
    with pytest.raises(ValueError):
        build_market_state_inputs(
            policy(),
            market_bars=b,
            market_history_policy=history,
            sample=SAMPLE,
            account_returns_available=True,
            maximum_bars=bar_budget,
            maximum_intervals=intervals,
        )


def test_original_regime_engine_rejects_overflowed_regime_total():
    from services.risk.research_risk.domain.risk_modules import RegimeRiskEngine
    from services.risk.research_risk.domain.models import RiskConfig

    assert RegimeRiskEngine().evaluate([1e308, 1e308], ["calm", "calm"], RiskConfig()).passed is False
