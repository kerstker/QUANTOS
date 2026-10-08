"""Preregistered shared ADV capacity in original account units; no FX."""

from quantos.contracts.registered_research_run import canonical, digest

SCHEMA = "QUANTOS_GOLD_MARKET_CAPACITY_POLICY_V1"
POLICY = dict(
    venue_id="BINANCE_USDM",
    instrument_id="XAUUSDT",
    period_rule_id="BINANCE_USDM.UTC_DAY.v1",
    adv_observation_periods=30,
    period_duration_ms=86400000,
    period_epoch_ms=0,
    original_execution_participation="0.01",
    research_capacity_fraction="0.1",
    adv_method="ORIGINAL_PUBLIC_30_COMPLETE_UTC_DAY_QUOTE_ADV_CAPACITY_FLOOR_V1",
    capacity_clock="RETURN_INTERVAL_START_ORIGINAL_RECEIPT_AND_INGESTION_AVAILABLE",
    expiry="ORIGINAL_UTC_DAY_WINDOW_NO_FORWARD_FILL",
    allocation="ONE_INSTRUMENT_POOL_BY_ORIGINAL_ACTIVE_ABSOLUTE_ALLOCATED_WEIGHTS",
    aum="ORIGINAL_NET_ACCOUNT_RECEIPT_AVAILABLE_EQUITY",
    missing="UNAVAILABLE_NO_DEFAULTS",
)
FIELDS = frozenset(
    (
        "schema",
        "declared_at_ms",
        "sample",
        "currency",
        "method",
        "independent_verification",
        "economic_authority",
        "submission_enabled",
    )
)


def require(ok, reason):
    if not ok:
        raise ValueError("GOLD_CAPACITY_POLICY_" + reason)


def validate(value, *, sample=None, declared_at_ms=None):
    require(type(value) is dict and set(value) == FIELDS, "FIELDS")
    canonical(value, maximum=65536)
    require(
        value["schema"] == SCHEMA and value["currency"] == "USDT" and canonical(value["method"]) == canonical(POLICY),
        "METHOD_UNIT",
    )
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
    require(type(value["declared_at_ms"]) is int and 0 <= value["declared_at_ms"] < axis["start_ms"], "TIME")
    require(sample is None or canonical(axis) == canonical(sample), "SAMPLE_BINDING")
    require(declared_at_ms is None or value["declared_at_ms"] <= declared_at_ms, "TIME_BINDING")
    require(
        all(value[k] is False for k in ("independent_verification", "economic_authority", "submission_enabled")),
        "AUTHORITY",
    )
    return value


def policy_sha256(value):
    return digest(validate(value))
