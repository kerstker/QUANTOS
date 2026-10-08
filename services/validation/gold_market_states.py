"""Receipt-available raw-market labels on the complete account-return axis.

The original classifier runs on a bounded trailing prefix, explicitly declared
before observation. A repeatable stream binds causal observations without a
second price/return ledger. Available labels are not full regime robustness.
"""

from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib

from quantos.contracts.gold_market_state_policy import POLICY, policy_sha256, validate
from quantos.streaming_identity import StreamArray, exact_stream_identity_hash as digest
from services.validation.regime import label_regimes_by_vol

HISTORY_POLICY = "GOLD_ORIGINAL_RECEIPT_AVAILABLE_BARS_V1"
SCHEMA = "QUANTOS_GOLD_MARKET_STATE_INPUTS_V1"
BAR_FIELDS = frozenset(("open_ms", "close_ms", "available_ms", "open", "high", "low", "close"))


def require(ok, reason):
    if not ok:
        raise ValueError("GOLD_MARKET_STATES_" + reason)


def _time(value):
    require(type(value) is int and 0 <= value < 2**63, "TIME")
    return value


def _number(value):
    require(type(value) is str and 0 < len(value) <= 256, "NUMBER")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("GOLD_MARKET_STATES_NUMBER") from None
    require(number.is_finite() and number > 0 and -96 <= number.adjusted() <= 96, "NUMBER")
    return number


def _rows(values):
    previous_close = previous_available = -1
    for row in values:
        require(isinstance(row, Mapping) and set(row) == BAR_FIELDS, "BAR_FIELDS")
        opening, closing, available = (_time(row[k]) for k in ("open_ms", "close_ms", "available_ms"))
        require(
            opening % POLICY["bar_interval_ms"] == 0
            and closing == opening + POLICY["bar_interval_ms"]
            and available >= closing,
            "BAR_TIME",
        )
        require(closing > previous_close and available >= previous_available, "BAR_ORDER")
        op, high, low, close = (_number(row[k]) for k in ("open", "high", "low", "close"))
        require(low <= min(op, close) <= max(op, close) <= high, "OHLC")
        previous_close, previous_available = closing, available
        yield dict(row)


@dataclass(frozen=True)
class MarketStateInputs:
    labels: tuple[str, ...] | None
    observations: StreamArray
    projection: dict


def build_market_state_inputs(
    policy, *, market_bars, market_history_policy, sample, account_returns_available, maximum_bars, maximum_intervals
):
    bound = validate(policy, sample=sample)
    require(market_history_policy == HISTORY_POLICY, "HISTORY_POLICY")
    require(
        type(account_returns_available) is bool
        and all(type(v) is int and 0 < v <= 100_000_000 for v in (maximum_bars, maximum_intervals)),
        "BUDGET",
    )
    require(
        isinstance(market_bars, Sequence)
        and not isinstance(market_bars, (str, bytes))
        and len(market_bars) <= maximum_bars,
        "BAR_BUDGET",
    )
    start, end, frequency = (sample[k] for k in ("start_ms", "end_ms", "frequency_ms"))
    interval_count = (end - start) // frequency
    require(interval_count <= maximum_intervals, "INTERVAL_BUDGET")
    # Validate ALL originals, including bars unavailable at the decision time.
    source_sha256 = digest(StreamArray(lambda: _rows(market_bars)))

    def observations():
        if not account_returns_available:
            return
        iterator = iter(_rows(market_bars))
        pending = next(iterator, None)
        latest = None
        window = deque(maxlen=POLICY["vol_window"] + POLICY["reference_windows"])
        prefix = hashlib.sha256()
        consumed = 0
        label = None
        for decision in range(start, end, frequency):
            changed = False
            while pending is not None and pending["available_ms"] <= decision:
                if latest is not None and pending["close_ms"] != latest["close_ms"] + POLICY["bar_interval_ms"]:
                    window.clear()
                latest = pending
                window.append(float(_number(latest["close"])))
                prefix.update(bytes.fromhex(digest(latest)))
                consumed += 1
                changed = True
                pending = next(iterator, None)
            status = "UNAVAILABLE_EMPTY_MARKET_HISTORY"
            if latest is not None:
                if changed:
                    label = None
                    if len(window) >= POLICY["vol_window"] + POLICY["minimum_reference_windows"]:
                        try:
                            result = label_regimes_by_vol(
                                list(window), vol_window=POLICY["vol_window"], n_regimes=POLICY["n_regimes"]
                            )[-1]
                            if result is not None:
                                label = "VOL_RELATIVE_" + str(result)
                        except (OverflowError, ValueError, ZeroDivisionError):
                            label = None
                status = (
                    "UNAVAILABLE_STALE_MARKET_HISTORY"
                    if decision - latest["close_ms"] > POLICY["maximum_bar_age_ms"]
                    else "UNAVAILABLE_WARMUP_OR_CLASSIFIER"
                    if label is None
                    else "AVAILABLE"
                )
            yield dict(
                decision_at_ms=decision,
                return_end_ms=decision + frequency,
                status=status,
                label=label if status == "AVAILABLE" else None,
                latest_bar=None if latest is None else dict(latest),
                received_bar_count=consumed,
                contiguous_closes=len(window),
                received_prefix_sha256=prefix.hexdigest(),
            )

    stream = StreamArray(observations)
    labels = []
    statuses = Counter()
    for row in stream.items():
        labels.append(row["label"])
        statuses[row["status"]] += 1
    missing = (
        interval_count
        if not account_returns_available
        else sum(count for status, count in statuses.items() if status != "AVAILABLE")
    )
    ready = account_returns_available and missing == 0
    values = tuple(labels) if ready else None
    projection = dict(
        schema=SCHEMA,
        status="AVAILABLE"
        if ready
        else "UNAVAILABLE_MARKET_COVERAGE"
        if account_returns_available
        else "UNAVAILABLE_ACCOUNT_SERIES",
        sample=dict(sample),
        label_clock=POLICY["label_clock"],
        policy_sha256=policy_sha256(bound),
        original_bars_sha256=source_sha256,
        market_history_policy=HISTORY_POLICY,
        original_bar_count=len(market_bars),
        interval_count=interval_count,
        observation_count=len(labels),
        missing_intervals=missing,
        status_counts=dict(sorted(statuses.items())),
        observation_sha256=digest(stream),
        label_values_sha256=None if values is None else digest(values),
        observed_labels=sorted(set(values or ())),
        prefix_hash_method="SHA256_ORDERED_ORIGINAL_BAR_IDENTITY_DIGESTS_V1",
        limitations=[
            "SAME_ORIGINAL_MARKET_CLASSIFIER_NOT_INDEPENDENT_MATHEMATICS",
            "BOUNDED_RELATIVE_VOLATILITY_IS_NOT_FULL_MARKET_REGIME_ROBUSTNESS",
            "NO_SOURCE_OR_DECLARATION_TIME_AUTHORITY",
        ],
        independent_verification=False,
        economic_authority=False,
        submission_enabled=False,
    )
    return MarketStateInputs(values, stream, projection)
