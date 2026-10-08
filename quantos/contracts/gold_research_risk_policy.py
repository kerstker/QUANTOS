"""Predeclared Gold nine-engine policy; no defaults, FX, or approval.

The explicit account-unit thresholds are a future review recipe. They do not
calibrate venue capacity, authenticate declaration time or fill missing inputs.
Upper limits cannot exceed the original engine limits; confidence floors cannot
fall below them. Gold retains its stricter five-percent drawdown ceiling.
"""

import copy
from decimal import Decimal, InvalidOperation

from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from quantos.contracts.registered_research_run import canonical, digest

SCHEMA = "QUANTOS_GOLD_RESEARCH_RISK_POLICY_V1"
INTEGER_FIELDS = frozenset(("max_drawdown_duration", "max_liquidation_days", "seed"))
UPPER_LIMITS = dict(
    max_portfolio_volatility="0.25",
    max_var_95="0.05",
    max_cvar_95="0.08",
    max_drawdown="0.05",
    max_drawdown_duration=60,
    max_single_alpha_weight="0.15",
    max_cluster_weight="0.3",
    max_pairwise_correlation="0.85",
    max_average_correlation="0.5",
    max_liquidity_usage="0.2",
    max_participation_rate="0.1",
    max_liquidation_days=10,
    max_leverage="2",
    max_turnover="5",
    max_cost_drag="0.5",
    max_capacity_usage="0.8",
    max_worst_regime_loss="0.25",
    max_stress_loss="0.35",
)
LOWER_LIMITS = dict(
    min_effective_number_of_bets="5",
    min_capacity="10000000",
    min_validation_confidence="0.3",
    min_paper_confidence="0.3",
)
CONFIG_FIELDS = frozenset((*UPPER_LIMITS, *LOWER_LIMITS, "periods_per_year", "seed"))
FIELDS = frozenset(
    (
        "schema",
        "declared_at_ms",
        "sample",
        "currency",
        "config",
        "input_contract",
        "independent_verification",
        "economic_authority",
        "submission_enabled",
    )
)
INPUT_CONTRACT = dict(
    engine_set=[
        "capacity",
        "concentration",
        "correlation",
        "drawdown",
        "liquidity",
        "market",
        "model_alpha",
        "regime",
        "stress",
    ],
    returns="NET_ACCOUNT_FIXED_FREQUENCY_RECEIPT_AVAILABLE",
    drawdown="RECEIPT_AVAILABLE_ATOMIC_EQUITY_REQUIRED",
    duration_unit="DECLARED_RETURN_INTERVALS",
    alpha_allocation="BOUND_NET_ACCOUNT_ALPHA_ATTRIBUTION_REQUIRED",
    alpha_returns="SAME_NET_ACCOUNT_COST_AND_FUNDING_ATTRIBUTION_REQUIRED",
    capacity_and_aum="EXPLICIT_ACCOUNT_CURRENCY_NO_FX",
    regime_labels="POINT_IN_TIME_SAME_RETURN_AXIS_REQUIRED",
    alpha_metadata="BOUND_ORIGINAL_TRIAL_HISTORY_REQUIRED",
    missing_inputs="INSUFFICIENT_NO_DEFAULTS",
)


def require(ok, reason):
    if not ok:
        raise ValueError("GOLD_RESEARCH_RISK_POLICY_" + reason)


def _number(value):
    require(type(value) is str and 0 < len(value) <= 256, "NUMBER")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("GOLD_RESEARCH_RISK_POLICY_NUMBER") from None
    require(
        number.is_finite() and (not number or -96 <= number.adjusted() <= 96) and canonical_decimal(number) == value,
        "NUMBER",
    )
    return number


@ledger_decimal_context()
def validate(value, *, sample=None, declared_at_ms=None):
    require(type(value) is dict and set(value) == FIELDS, "FIELDS")
    canonical(value, maximum=65536)
    require(value["schema"] == SCHEMA and value["currency"] == "USDT", "KIND_UNIT")
    axis = value["sample"]
    require(
        type(axis) is dict
        and set(axis) == {"start_ms", "end_ms", "frequency_ms"}
        and all(type(v) is int and 0 <= v < 2**63 for v in axis.values())
        and axis["frequency_ms"] > 0
        and axis["end_ms"] > axis["start_ms"]
        and (axis["end_ms"] - axis["start_ms"]) % axis["frequency_ms"] == 0,
        "SAMPLE",
    )
    require(
        type(value["declared_at_ms"]) is int and 0 <= value["declared_at_ms"] < axis["start_ms"], "DECLARATION_TIME"
    )
    require(sample is None or axis == sample, "SAMPLE_BINDING")
    require(declared_at_ms is None or value["declared_at_ms"] <= declared_at_ms, "TIME_BINDING")
    require(canonical(value["input_contract"]) == canonical(INPUT_CONTRACT), "INPUT_CONTRACT")
    require(
        value["independent_verification"] == "REQUIRED_NOT_ESTABLISHED"
        and value["economic_authority"] is False
        and value["submission_enabled"] is False,
        "AUTHORITY",
    )
    config = value["config"]
    require(type(config) is dict and set(config) == CONFIG_FIELDS, "CONFIG_FIELDS")
    for name, raw in config.items():
        if name in INTEGER_FIELDS:
            require(type(raw) is int and 0 <= raw < 2**63, "INTEGER")
            number = Decimal(raw)
        else:
            number = _number(raw)
            require(number >= 0, "NEGATIVE")
        if name in UPPER_LIMITS:
            require(number <= Decimal(str(UPPER_LIMITS[name])), "WEAKENED_LIMIT")
        if name in LOWER_LIMITS:
            require(number >= Decimal(LOWER_LIMITS[name]), "WEAKENED_FLOOR")
    require(config["seed"] == 42, "SEED")
    require(_number(config["periods_per_year"]) == Decimal(365 * 86400000) / axis["frequency_ms"], "ANNUALIZATION")
    return copy.deepcopy(value)


def policy_sha256(value):
    return digest(validate(value))
