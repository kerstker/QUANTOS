"""Review original net forward episodes without replaying or joining their cash."""

from dataclasses import dataclass
from decimal import Decimal

from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from quantos.contracts.gold_net_forward_policy import (
    METHOD,
    REVIEW_SCHEMA,
    SCOPE,
    WEEK_MS,
    policy_sha256,
    require,
    series_sha256,
    validate,
)
from quantos.contracts.registered_research_run import _hash, native_trial_id
from quantos.streaming_identity import exact_stream_identity_hash as digest
from services.validation.gold_account_risk_inputs import GoldAccountRiskInputs

IDENTITY = ("invocation_id", "native_trial_id", "config_hash")


@dataclass(frozen=True)
class NetForwardEpisode:
    """Internal owning-source seam; not a serialized claim or alternate ledger."""

    subject: dict
    policy: dict
    account: GoldAccountRiskInputs
    strategy_definition_sha256: str
    proposal_sha256: str


def _time(value):
    require(type(value) is int and 0 <= value < 2**63, "TIME")
    return value


def _identity(subject, candidate):
    require(type(subject) is dict and all(_hash(subject.get(k)) for k in IDENTITY), "SUBJECT")
    result = {k: subject[k] for k in IDENTITY}
    require(
        result["native_trial_id"] == native_trial_id(result["invocation_id"], candidate, result["config_hash"]),
        "TRIAL_BINDING",
    )
    return result


def _grid(account):
    p = account.projection
    if (
        account.returns is None
        or account.equity_curve is None
        or account.timestamps is None
        or account.initial_equity is None
        or account.position_history is None
        or not p["full_run_covered"]
    ):
        return None
    groups = p["source_groups"]
    values = dict(portfolio_returns=account.returns, equity_curve=account.equity_curve, timestamps=account.timestamps)
    size = len(account.timestamps)
    require(size > 0 and all(len(v) == size for v in values.values()), "GRID_SHAPE")
    for k, v in values.items():
        encoded = tuple(canonical_decimal(x) if isinstance(x, Decimal) else x for x in v)
        require(
            groups[k]["status"] == "AVAILABLE"
            and groups[k]["count"] == size
            and groups[k]["value_sha256"] == digest(encoded),
            "GRID_BINDING",
        )
    initial = groups["initial_equity"]
    require(
        initial["status"] == "AVAILABLE"
        and initial["value"] == canonical_decimal(account.initial_equity)
        and initial["value_sha256"] == digest(initial["value"]),
        "GRID_ANCHOR",
    )
    start, end, frequency = (p["sample"][k] for k in ("start_ms", "end_ms", "frequency_ms"))
    require(account.timestamps == tuple(range(start + frequency, end + 1, frequency)), "GRID_AXIS")
    require(account.initial_equity > 0 and all(e.is_finite() and e > 0 for e in account.equity_curve), "GRID_EQUITY")
    previous = account.initial_equity
    for value, ret in zip(account.equity_curve, account.returns, strict=True):
        require(ret.is_finite() and ret == value / previous - 1, "NET_RETURN_BINDING")
        previous = value
    require(
        groups["account_positions"]["status"] == "AVAILABLE"
        and groups["account_positions"]["value_sha256"] == digest(account.position_history),
        "POSITION_BINDING",
    )

    def equity_at(timestamp):
        require(start <= timestamp <= end and (timestamp - start) % frequency == 0, "PERIOD_GRID_BOUNDARY")
        return (
            account.initial_equity if timestamp == start else account.equity_curve[(timestamp - start) // frequency - 1]
        )

    return equity_at


@ledger_decimal_context()
def assess_net_forward(
    *, episodes, policy, subject, native_result_hash, project_cut_sha256, snapshot_as_of_ms, incomplete_invocations=()
):
    """Consume each account once; retain only bounded weekly receipts, not vectors."""
    validate(policy)
    series = policy["series"]
    candidate = series["candidate_id"]
    selected = _identity(subject, candidate)
    require(_hash(native_result_hash) and _hash(project_cut_sha256), "RESULT_CUT")
    snapshot = _time(snapshot_as_of_ms)
    series_hash = series_sha256(policy)
    receipts = []
    periods = {}
    reasons = set()
    origin = False
    found = False
    count = 0
    for episode in episodes:
        count += 1
        require(count <= policy["budget"]["maximum_episodes"], "EPISODE_BUDGET")
        require(isinstance(episode, NetForwardEpisode), "ORIGINAL_EPISODE_REQUIRED")
        validate(episode.policy)
        identity = _identity(episode.subject, candidate)
        require(series_sha256(episode.policy) == series_hash, "SERIES_BINDING")
        require(
            episode.strategy_definition_sha256 == series["strategy_definition_sha256"]
            and episode.proposal_sha256 == series["proposal_sha256"],
            "FROZEN_SELECTION_BINDING",
        )
        a = episode.account
        require(isinstance(a, GoldAccountRiskInputs), "ORIGINAL_ACCOUNT_REQUIRED")
        p = a.projection
        sample = episode.policy["sample"]
        require(
            p["sample"] == sample
            and p["currency"] == a.currency == "USDT"
            and type(p["full_run_covered"]) is bool
            and _hash(p["native_result_hash"])
            and all(p[k] is False for k in ("economic_authority", "submission_enabled")),
            "ACCOUNT_BINDING",
        )
        require(_time(p["last_available_at_ms"]) <= snapshot and sample["end_ms"] <= snapshot, "FUTURE_ACCOUNT")
        for k in ("available_history_sha256", "funding_bindings_sha256", "original_cash_identity_sha256"):
            require(_hash(p[k]), "ACCOUNT_ORIGINALS")
        if identity == selected:
            require(p["native_result_hash"] == native_result_hash, "SELECTED_RESULT_BINDING")
            require(policy_sha256(episode.policy) == policy_sha256(policy), "SELECTED_POLICY_BINDING")
            found = True
        origin |= (
            sample["start_ms"] == series["start_ms"] and episode.policy["declared_at_ms"] == series["declared_at_ms"]
        )
        grid = _grid(a)
        receipt = dict(
            **identity,
            sample=dict(sample),
            policy_sha256=policy_sha256(episode.policy),
            native_result_hash=p["native_result_hash"],
            **{
                k: p[k]
                for k in ("available_history_sha256", "funding_bindings_sha256", "original_cash_identity_sha256")
            },
            source_groups_sha256=digest(
                {
                    k: p["source_groups"][k]
                    for k in ("portfolio_returns", "equity_curve", "timestamps", "initial_equity", "account_positions")
                }
            ),
            full_run_covered=p["full_run_covered"],
            period_indices=[],
        )
        receipts.append(receipt)
        if grid is None:
            reasons.add("ORIGINAL_NET_GRID_POSITION_OR_FULL_RUN_COVERAGE_MISSING")
            continue
        first = max(0, (sample["start_ms"] - series["start_ms"] + WEEK_MS - 1) // WEEK_MS)
        last = (sample["end_ms"] - series["start_ms"]) // WEEK_MS
        indices = list(range(first, last))
        require(all(i not in periods for i in indices), "OVERLAPPING_PERIOD")
        receipt["period_indices"] = indices
        active = set()
        previous = None
        for row in a.position_history.items():
            _time(row["timestamp"])
            _time(row["available_at_ms"])
            require(row["available_at_ms"] <= snapshot, "FUTURE_POSITION")
            if previous is not None and previous["positions"] and row["timestamp"] > previous["timestamp"]:
                for i in indices:
                    left = series["start_ms"] + i * WEEK_MS
                    right = left + WEEK_MS
                    if max(left, previous["timestamp"]) < min(right, row["timestamp"]):
                        active.add(i)
            previous = row
        for i in indices:
            left = series["start_ms"] + i * WEEK_MS
            right = left + WEEK_MS
            opening, ending = grid(left), grid(right)
            periods[i] = dict(
                index=i,
                start_ms=left,
                end_ms=right,
                **identity,
                opening_net_equity=canonical_decimal(opening),
                ending_net_equity=canonical_decimal(ending),
                net_return=canonical_decimal(ending / opening - 1),
                active_position_interval=i in active,
                account_receipt_sha256=digest(receipt),
            )
    require(all(_hash(k) for k in incomplete_invocations), "INCOMPLETE_IDENTITY")
    if incomplete_invocations:
        reasons.add("ORIGINAL_SERIES_EPISODES_INCOMPLETE")
    if not origin:
        reasons.add("ORIGINAL_SERIES_DECLARATION_MISSING")
    if not found:
        reasons.add("SELECTED_ORIGINAL_NET_EPISODE_MISSING")
    active_count = sum(row["active_position_interval"] for row in periods.values())
    maturity = min(Decimal(1), Decimal(active_count) / METHOD["required_periods"])
    if active_count < METHOD["required_periods"]:
        reasons.add("EIGHT_COMPLETE_ACTIVE_WEEKS_NOT_ESTABLISHED")
    mature = not reasons
    value = dict(
        schema=REVIEW_SCHEMA,
        scope=SCOPE,
        **selected,
        candidate_id=candidate,
        native_result_hash=native_result_hash,
        project_cut_sha256=project_cut_sha256,
        snapshot_as_of_ms=snapshot,
        policy_sha256=policy_sha256(policy),
        series_sha256=series_hash,
        status="MATURE" if mature else "INSUFFICIENT",
        completed_active_periods=active_count,
        required_periods=METHOD["required_periods"],
        normalized_maturity=canonical_decimal(maturity),
        paper_confidence=canonical_decimal(maturity) if mature else None,
        episodes=sorted(receipts, key=lambda r: (r["sample"]["start_ms"], r["invocation_id"])),
        incomplete_invocations=sorted(set(incomplete_invocations)),
        periods=[periods[i] for i in sorted(periods)],
        account_mode=METHOD["account_mode"],
        maturity_is_profit_probability=False,
        reasons=sorted(reasons),
        limitations=[
            "NO_INDEPENDENT_SOURCE_POLICY_OR_TIME_AUTHORITY",
            "NET_FORWARD_MATURITY_IS_NOT_PROFITABILITY_OR_FULL_MODEL_RISK",
            "EPISODE_ACCOUNTS_ARE_SEPARATE_NOT_CONTINUOUS_DEPLOYMENT_CASH",
        ],
        independent_verification=False,
        economic_authority=False,
        submission_enabled=False,
    )
    value["review_sha256"] = digest(value)
    return value
