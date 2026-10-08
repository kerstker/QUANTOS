"""Prospective bounded raw-market classifier; never portfolio-P&L labels."""

from quantos.contracts.registered_research_run import canonical, digest

SCHEMA = "QUANTOS_GOLD_MARKET_STATE_POLICY_V1"
POLICY = dict(
    method="ORIGINAL_VOL_CLASSIFIER_BOUNDED_REFERENCE_V1",
    label_clock="RETURN_INTERVAL_START_RECEIPT_AVAILABLE",
    bar_interval_ms=900_000,
    vol_window=20,
    reference_windows=128,
    minimum_reference_windows=20,
    n_regimes=2,
    maximum_bar_age_ms=930_000,
    gaps="RESET_AND_REWARM",
    ties="STRICT_RANK_LOW_BUCKET",
    missing_inputs="UNAVAILABLE_NO_DEFAULT_LABELS",
)
FIELDS = frozenset(
    (
        "schema",
        "declared_at_ms",
        "sample",
        "method",
        "independent_verification",
        "economic_authority",
        "submission_enabled",
    )
)


def require(ok, reason):
    if not ok:
        raise ValueError("GOLD_MARKET_STATE_POLICY_" + reason)


def validate(value, *, sample=None, declared_at_ms=None):
    require(type(value) is dict and set(value) == FIELDS, "FIELDS")
    canonical(value, maximum=65_536)
    require(value["schema"] == SCHEMA and canonical(value["method"]) == canonical(POLICY), "METHOD")
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
