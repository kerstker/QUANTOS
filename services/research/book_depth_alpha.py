"""Pure diagnostic evaluator for the pre-registered book-depth curve study.

No IO, network, wall clock, RNG, registry mutation, promotion, execution or
order capability lives here.  A PASS is diagnostic only and cannot authorize
Shadow, Testnet or trading.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any


class BookDepthAlphaError(ValueError):
    """The fixed diagnostic cannot be evaluated without weakening its contract."""


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise BookDepthAlphaError(f"{field} is invalid")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BookDepthAlphaError(f"{field} is invalid") from exc
    if not math.isfinite(result):
        raise BookDepthAlphaError(f"{field} is not finite")
    return result


def _positive(value: object, field: str) -> float:
    result = _finite(value, field)
    if result <= 0:
        raise BookDepthAlphaError(f"{field} must be positive")
    return result


def _imbalance(level: Mapping[str, object]) -> float:
    bid = _positive(level.get("bid_notional_p10"), "bid_notional_p10")
    ask = _positive(level.get("ask_notional_p10"), "ask_notional_p10")
    return (bid - ask) / (bid + ask)


def candidate_signals(observation: Mapping[str, object]) -> dict[str, float]:
    """Compute exactly the six frozen curve definitions."""
    levels = observation.get("levels")
    if not isinstance(levels, Mapping):
        raise BookDepthAlphaError("levels are missing")
    required = ("0.2", "1", "2", "3", "4", "5")
    if any(not isinstance(levels.get(radius), Mapping) for radius in required):
        raise BookDepthAlphaError("required depth curve level is missing")
    imbalances = {
        radius: _imbalance(levels[radius])  # type: ignore[arg-type]
        for radius in required
    }
    near = levels["0.2"]
    far = levels["5"]
    assert isinstance(near, Mapping) and isinstance(far, Mapping)
    bid_concentration = _positive(
        near.get("bid_notional_p10"), "near bid notional"
    ) / _positive(far.get("bid_notional_p10"), "far bid notional")
    ask_concentration = _positive(
        near.get("ask_notional_p10"), "near ask notional"
    ) / _positive(far.get("ask_notional_p10"), "far ask notional")
    return {
        "depth_imbalance_0p2_continuation_4h_v1": imbalances["0.2"],
        "depth_imbalance_1p0_continuation_4h_v1": imbalances["1"],
        "depth_imbalance_5p0_continuation_4h_v1": imbalances["5"],
        "depth_near_minus_far_continuation_4h_v1": (
            imbalances["0.2"] - imbalances["5"]
        ),
        "depth_near_concentration_skew_4h_v1": math.log(
            bid_concentration / ask_concentration
        ),
        "depth_curve_mean_imbalance_4h_v1": statistics.fmean(
            imbalances.values()
        ),
    }


def _average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        average = (start + end - 1) / 2.0
        for offset in range(start, end):
            ranks[indexed[offset][0]] = average
        start = end
    return ranks


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right, strict=True)
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def _weights(
    signals: Mapping[str, float], *, long_count: int, short_count: int
) -> dict[str, float]:
    ordered = sorted(signals.items(), key=lambda item: (item[1], item[0]))
    if len(ordered) < long_count + short_count:
        raise BookDepthAlphaError("cross-section is too narrow for the fixed book")
    result = {instrument: 0.0 for instrument in signals}
    for instrument, _ in ordered[:short_count]:
        result[instrument] = -0.5 / short_count
    for instrument, _ in ordered[-long_count:]:
        result[instrument] = 0.5 / long_count
    return result


def _summary(
    returns: Sequence[float], annualization: float
) -> dict[str, float | int | None]:
    count = len(returns)
    if count < 2:
        return {
            "period_count": count,
            "mean_return": None,
            "net_sharpe": None,
            "total_return": None,
            "max_drawdown": None,
        }
    mean_return = statistics.fmean(returns)
    volatility = statistics.pstdev(returns)
    sharpe = mean_return / volatility * math.sqrt(annualization) if volatility else None
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1.0 - equity / peak)
    return {
        "period_count": count,
        "mean_return": mean_return,
        "net_sharpe": sharpe,
        "total_return": equity - 1.0,
        "max_drawdown": max_drawdown,
    }


def _ic_summary(values: Sequence[float]) -> dict[str, float | int | None]:
    count = len(values)
    if count < 2:
        return {"period_count": count, "mean_ic": None, "t_stat": None}
    mean_ic = statistics.fmean(values)
    sample_std = statistics.stdev(values)
    t_stat = mean_ic / (sample_std / math.sqrt(count)) if sample_std else None
    return {"period_count": count, "mean_ic": mean_ic, "t_stat": t_stat}


def evaluate_registered_candidates(
    observations: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the fixed candidate population on the exact common 4h panel."""
    universe = tuple(str(value) for value in contract["universe"])
    interval_hours = int(contract["decision_interval_hours"])
    horizon_hours = int(contract["forward_horizon_hours"])
    if interval_hours != horizon_hours or interval_hours < 1:
        raise BookDepthAlphaError("only fixed non-overlapping horizons are supported")
    interval_ms = interval_hours * 3_600_000
    panel: dict[int, dict[str, Mapping[str, Any]]] = {}
    for row in observations:
        if row.get("dataset") != contract["dataset_id"]:
            raise BookDepthAlphaError("unexpected dataset in diagnostic input")
        if row.get("external_price_alignment") != "PASS":
            raise BookDepthAlphaError("unaligned price input is not admissible")
        event_time = int(row["event_time_ms"])
        instrument = str(row["instrument_id"])
        if instrument not in universe:
            raise BookDepthAlphaError("unexpected instrument in diagnostic input")
        if instrument in panel.setdefault(event_time, {}):
            raise BookDepthAlphaError("duplicate panel observation")
        panel[event_time][instrument] = row
    times = tuple(
        time_value
        for time_value in sorted(panel)
        if time_value % interval_ms == 0
        and set(panel[time_value]) == set(universe)
        and set(panel.get(time_value + interval_ms, {})) == set(universe)
    )
    if not times:
        raise BookDepthAlphaError("no exact common non-overlapping decisions")
    candidate_ids = tuple(
        str(item["candidate_id"]) for item in contract["candidates"]
    )
    if len(candidate_ids) != 6 or len(set(candidate_ids)) != 6:
        raise BookDepthAlphaError("candidate population is not the fixed six")
    long_count = int(contract["long_count"])
    short_count = int(contract["short_count"])
    annualization = int(contract["annualization_periods"])
    cost_rate = float(contract["one_way_cost_bps_per_unit_turnover"]) / 10_000.0
    stress_multiplier = float(contract["cost_stress_multiplier"])
    results: dict[str, Any] = {}
    for candidate_id in candidate_ids:
        previous_weights = {instrument: 0.0 for instrument in universe}
        periods: list[dict[str, Any]] = []
        for event_time in times:
            signals = {
                instrument: candidate_signals(panel[event_time][instrument])[
                    candidate_id
                ]
                for instrument in universe
            }
            next_time = event_time + interval_ms
            prices = {
                instrument: _positive(
                    panel[event_time][instrument]["external_price_reference"][
                        "reference_price"
                    ],
                    "decision mark price",
                )
                for instrument in universe
            }
            next_prices = {
                instrument: _positive(
                    panel[next_time][instrument]["external_price_reference"][
                        "reference_price"
                    ],
                    "realization mark price",
                )
                for instrument in universe
            }
            forward_returns = {
                instrument: next_prices[instrument] / prices[instrument] - 1.0
                for instrument in universe
            }
            weights = _weights(
                signals, long_count=long_count, short_count=short_count
            )
            turnover = 0.5 * sum(
                abs(weights[instrument] - previous_weights[instrument])
                for instrument in universe
            )
            contributions = {
                instrument: weights[instrument] * forward_returns[instrument]
                for instrument in universe
            }
            gross_return = sum(contributions.values())
            rank_ic = _correlation(
                _average_ranks([signals[instrument] for instrument in universe]),
                _average_ranks(
                    [forward_returns[instrument] for instrument in universe]
                ),
            )
            if rank_ic is None:
                raise BookDepthAlphaError("rank IC is undefined")
            periods.append(
                {
                    "event_time_ms": event_time,
                    "gross_return": gross_return,
                    "turnover": turnover,
                    "net_return": gross_return - turnover * cost_rate,
                    "double_cost_net_return": (
                        gross_return - turnover * cost_rate * stress_multiplier
                    ),
                    "rank_ic": rank_ic,
                    "instrument_contributions": contributions,
                }
            )
            previous_weights = weights
        locked_start = math.floor(
            len(periods) * (1.0 - float(contract["locked_oos_fraction"]))
        )
        locked = periods[locked_start:]
        if len(locked) < int(contract["minimum_locked_decisions"]):
            raise BookDepthAlphaError("locked OOS decision count is insufficient")
        third_size = max(1, len(locked) // 3)
        thirds = (
            locked[:third_size],
            locked[third_size : 2 * third_size],
            locked[2 * third_size :],
        )
        positive_thirds = sum(
            1
            for section in thirds
            if section and math.prod(1.0 + row["net_return"] for row in section) > 1.0
        )
        locked_absolute_contribution = {
            instrument: sum(
                abs(row["instrument_contributions"][instrument]) for row in locked
            )
            for instrument in universe
        }
        gross_absolute = sum(locked_absolute_contribution.values())
        maximum_contribution_fraction = (
            max(locked_absolute_contribution.values())
            / gross_absolute
            if gross_absolute
            else None
        )
        results[candidate_id] = {
            "all_periods": _summary(
                [row["net_return"] for row in periods], annualization
            ),
            "locked_oos": _summary(
                [row["net_return"] for row in locked], annualization
            ),
            "locked_oos_double_cost": _summary(
                [row["double_cost_net_return"] for row in locked], annualization
            ),
            "locked_rank_ic": _ic_summary(
                [row["rank_ic"] for row in locked]
            ),
            "positive_chronological_thirds": positive_thirds,
            "maximum_single_instrument_gross_pnl_fraction": (
                maximum_contribution_fraction
            ),
            "mean_turnover": statistics.fmean(
                row["turnover"] for row in locked
            ),
            "decision_count": len(periods),
            "locked_start_event_time_ms": locked[0]["event_time_ms"],
            "last_decision_event_time_ms": locked[-1]["event_time_ms"],
        }
    primary_id = str(contract["primary_candidate_id"])
    primary = results[primary_id]
    gates = contract["primary_required_gates"]
    checks = {
        "locked_net_sharpe": (
            primary["locked_oos"]["net_sharpe"] is not None
            and primary["locked_oos"]["net_sharpe"]
            >= float(gates["locked_net_sharpe_minimum"])
        ),
        "locked_rank_ic_mean": (
            primary["locked_rank_ic"]["mean_ic"] is not None
            and primary["locked_rank_ic"]["mean_ic"]
            > float(gates["locked_rank_ic_mean_minimum"])
        ),
        "locked_rank_ic_t_stat": (
            primary["locked_rank_ic"]["t_stat"] is not None
            and primary["locked_rank_ic"]["t_stat"]
            >= float(gates["locked_rank_ic_t_stat_minimum"])
        ),
        "chronological_thirds": (
            primary["positive_chronological_thirds"]
            >= int(gates["positive_chronological_thirds_minimum"])
        ),
        "double_cost": (
            primary["locked_oos_double_cost"]["net_sharpe"] is not None
            and primary["locked_oos_double_cost"]["net_sharpe"]
            > float(gates["double_cost_net_sharpe_minimum"])
        ),
        "instrument_concentration": (
            primary["maximum_single_instrument_gross_pnl_fraction"] is not None
            and primary["maximum_single_instrument_gross_pnl_fraction"]
            <= float(gates["maximum_single_instrument_gross_pnl_fraction"])
        ),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "RETROSPECTIVE_PRE_REGISTERED_DIAGNOSTIC",
        "primary_candidate_id": primary_id,
        "candidate_count": len(results),
        "trial_count": len(results),
        "common_decision_count": len(times),
        "first_decision_event_time_ms": times[0],
        "last_decision_event_time_ms": times[-1],
        "primary_gate_checks": checks,
        "candidates": results,
        "formal_admission": False,
        "forward_credit": False,
        "testnet_authorized": False,
        "order_submission_authorized": False,
    }


def evaluate_exploration(
    observations: Sequence[Mapping[str, Any]],
    preregistration: Mapping[str, Any],
    exploration: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the frozen direction/horizon grid without granting admission.

    Every trial uses non-overlapping decisions for its own forward horizon.  The
    complete observed history is exploratory; a selected lead must therefore be
    confirmed on the separately frozen history required by the exploration
    contract before it can enter any formal validation path.
    """
    universe = tuple(str(value) for value in preregistration["universe"])
    expected_candidates = tuple(
        str(item["candidate_id"]) for item in preregistration["candidates"]
    )
    candidate_ids = tuple(str(value) for value in exploration["candidate_ids"])
    if candidate_ids != expected_candidates or len(set(candidate_ids)) != 6:
        raise BookDepthAlphaError("exploration candidate population drifted")
    directions = tuple(int(value) for value in exploration["directions"])
    horizons = tuple(int(value) for value in exploration["horizon_hours"])
    if directions != (1, -1) or any(value < 1 for value in horizons):
        raise BookDepthAlphaError("exploration direction or horizon grid is invalid")
    expected_trial_count = len(candidate_ids) * len(directions) * len(horizons)
    if int(exploration["trial_count"]) != expected_trial_count:
        raise BookDepthAlphaError("exploration trial count is inconsistent")
    if exploration.get("dataset_id") != preregistration.get("dataset_id"):
        raise BookDepthAlphaError("exploration dataset differs from pre-registration")

    panel: dict[int, dict[str, Mapping[str, Any]]] = {}
    for row in observations:
        if row.get("dataset") != exploration["dataset_id"]:
            raise BookDepthAlphaError("unexpected dataset in exploration input")
        if row.get("external_price_alignment") != "PASS":
            raise BookDepthAlphaError("unaligned price input is not admissible")
        event_time = int(row["event_time_ms"])
        instrument = str(row["instrument_id"])
        if instrument not in universe:
            raise BookDepthAlphaError("unexpected instrument in exploration input")
        if instrument in panel.setdefault(event_time, {}):
            raise BookDepthAlphaError("duplicate panel observation")
        panel[event_time][instrument] = row

    long_count = int(exploration["long_count"])
    short_count = int(exploration["short_count"])
    cost_rate = (
        float(exploration["one_way_cost_bps_per_unit_turnover"]) / 10_000.0
    )
    stress_multiplier = float(exploration["cost_stress_multiplier"])
    minimum_decisions = int(exploration["minimum_decisions"])
    policy = exploration["selection_policy"]
    trials: dict[str, Any] = {}
    for candidate_id in candidate_ids:
        for direction in directions:
            for horizon_hours in horizons:
                horizon_ms = horizon_hours * 3_600_000
                times = tuple(
                    event_time
                    for event_time in sorted(panel)
                    if event_time % horizon_ms == 0
                    and set(panel[event_time]) == set(universe)
                    and set(panel.get(event_time + horizon_ms, {})) == set(universe)
                )
                if len(times) < minimum_decisions:
                    raise BookDepthAlphaError(
                        f"insufficient decisions for {horizon_hours}h exploration"
                    )
                previous_weights = {instrument: 0.0 for instrument in universe}
                periods: list[dict[str, Any]] = []
                for event_time in times:
                    directed_signals = {
                        instrument: direction
                        * candidate_signals(panel[event_time][instrument])[candidate_id]
                        for instrument in universe
                    }
                    next_time = event_time + horizon_ms
                    prices = {
                        instrument: _positive(
                            panel[event_time][instrument][
                                "external_price_reference"
                            ]["reference_price"],
                            "decision mark price",
                        )
                        for instrument in universe
                    }
                    next_prices = {
                        instrument: _positive(
                            panel[next_time][instrument][
                                "external_price_reference"
                            ]["reference_price"],
                            "realization mark price",
                        )
                        for instrument in universe
                    }
                    forward_returns = {
                        instrument: next_prices[instrument] / prices[instrument] - 1.0
                        for instrument in universe
                    }
                    weights = _weights(
                        directed_signals,
                        long_count=long_count,
                        short_count=short_count,
                    )
                    turnover = 0.5 * sum(
                        abs(weights[instrument] - previous_weights[instrument])
                        for instrument in universe
                    )
                    contributions = {
                        instrument: weights[instrument] * forward_returns[instrument]
                        for instrument in universe
                    }
                    gross_return = sum(contributions.values())
                    rank_ic = _correlation(
                        _average_ranks(
                            [directed_signals[instrument] for instrument in universe]
                        ),
                        _average_ranks(
                            [forward_returns[instrument] for instrument in universe]
                        ),
                    )
                    if rank_ic is None:
                        raise BookDepthAlphaError("rank IC is undefined")
                    periods.append(
                        {
                            "event_time_ms": event_time,
                            "gross_return": gross_return,
                            "turnover": turnover,
                            "net_return": gross_return - turnover * cost_rate,
                            "double_cost_net_return": (
                                gross_return
                                - turnover * cost_rate * stress_multiplier
                            ),
                            "rank_ic": rank_ic,
                            "instrument_contributions": contributions,
                        }
                    )
                    previous_weights = weights

                third_size = max(1, len(periods) // 3)
                thirds = (
                    periods[:third_size],
                    periods[third_size : 2 * third_size],
                    periods[2 * third_size :],
                )
                positive_thirds = sum(
                    1
                    for section in thirds
                    if section
                    and math.prod(1.0 + row["net_return"] for row in section) > 1.0
                )
                absolute_contribution = {
                    instrument: sum(
                        abs(row["instrument_contributions"][instrument])
                        for row in periods
                    )
                    for instrument in universe
                }
                total_absolute_contribution = sum(absolute_contribution.values())
                maximum_contribution_fraction = (
                    max(absolute_contribution.values()) / total_absolute_contribution
                    if total_absolute_contribution
                    else None
                )
                annualization = 8_760 / horizon_hours
                net_summary = _summary(
                    [row["net_return"] for row in periods], annualization
                )
                double_cost_summary = _summary(
                    [row["double_cost_net_return"] for row in periods],
                    annualization,
                )
                ic_summary = _ic_summary([row["rank_ic"] for row in periods])
                checks = {
                    "net_sharpe": (
                        net_summary["net_sharpe"] is not None
                        and net_summary["net_sharpe"]
                        >= float(policy["net_sharpe_minimum"])
                    ),
                    "double_cost": (
                        double_cost_summary["net_sharpe"] is not None
                        and double_cost_summary["net_sharpe"]
                        > float(policy["double_cost_net_sharpe_minimum"])
                    ),
                    "rank_ic_absolute_t_stat": (
                        ic_summary["t_stat"] is not None
                        and abs(ic_summary["t_stat"])
                        >= float(policy["rank_ic_absolute_t_stat_minimum"])
                    ),
                    "chronological_thirds": (
                        positive_thirds
                        >= int(policy["positive_chronological_thirds_minimum"])
                    ),
                    "instrument_concentration": (
                        maximum_contribution_fraction is not None
                        and maximum_contribution_fraction
                        <= float(
                            policy[
                                "maximum_single_instrument_gross_pnl_fraction"
                            ]
                        )
                    ),
                }
                trial_id = (
                    f"{candidate_id}|direction={direction:+d}|horizon={horizon_hours}h"
                )
                trials[trial_id] = {
                    "candidate_id": candidate_id,
                    "direction": direction,
                    "horizon_hours": horizon_hours,
                    "decision_count": len(periods),
                    "first_decision_event_time_ms": periods[0]["event_time_ms"],
                    "last_decision_event_time_ms": periods[-1]["event_time_ms"],
                    "net": net_summary,
                    "double_cost_net": double_cost_summary,
                    "rank_ic": ic_summary,
                    "positive_chronological_thirds": positive_thirds,
                    "maximum_single_instrument_gross_pnl_fraction": (
                        maximum_contribution_fraction
                    ),
                    "mean_turnover": statistics.fmean(
                        row["turnover"] for row in periods
                    ),
                    "selection_checks": checks,
                    "selected_for_confirmation": all(checks.values()),
                }

    selected_trial_ids = sorted(
        (
            trial_id
            for trial_id, trial in trials.items()
            if trial["selected_for_confirmation"]
        ),
        key=lambda trial_id: (
            -float(trials[trial_id]["net"]["net_sharpe"]),
            trial_id,
        ),
    )
    return {
        "status": "LEAD_SELECTED" if selected_trial_ids else "NO_LEAD",
        "mode": "RETROSPECTIVE_FROZEN_GRID_EXPLORATION",
        "trial_count": len(trials),
        "multiple_testing_trial_increment": len(trials),
        "selected_trial_count": len(selected_trial_ids),
        "selected_trial_ids": selected_trial_ids,
        "trials": trials,
        "confirmation_required": True,
        "formal_admission": False,
        "forward_credit": False,
        "testnet_authorized": False,
        "order_submission_authorized": False,
    }


def evaluate_confirmation(
    observations: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the single frozen trial on its untouched confirmation window."""
    universe = tuple(str(value) for value in contract["universe"])
    candidate_id = str(contract["candidate_id"])
    direction = int(contract["direction"])
    horizon_hours = int(contract["horizon_hours"])
    if direction not in {-1, 1} or horizon_hours < 1:
        raise BookDepthAlphaError("confirmation trial identity is invalid")
    horizon_ms = horizon_hours * 3_600_000
    panel: dict[int, dict[str, Mapping[str, Any]]] = {}
    for row in observations:
        if row.get("dataset") != contract["dataset_id"]:
            raise BookDepthAlphaError("unexpected dataset in confirmation input")
        if row.get("external_price_alignment") != "PASS":
            raise BookDepthAlphaError("unaligned price input is not admissible")
        event_time = int(row["event_time_ms"])
        instrument = str(row["instrument_id"])
        if instrument not in universe:
            raise BookDepthAlphaError("unexpected instrument in confirmation input")
        if instrument in panel.setdefault(event_time, {}):
            raise BookDepthAlphaError("duplicate panel observation")
        panel[event_time][instrument] = row
    times = tuple(
        event_time
        for event_time in sorted(panel)
        if event_time % horizon_ms == 0
        and set(panel[event_time]) == set(universe)
        and set(panel.get(event_time + horizon_ms, {})) == set(universe)
    )
    if len(times) < int(contract["minimum_decisions"]):
        raise BookDepthAlphaError("confirmation decision count is insufficient")

    long_count = int(contract["long_count"])
    short_count = int(contract["short_count"])
    cost_rate = float(contract["one_way_cost_bps_per_unit_turnover"]) / 10_000.0
    stress_multiplier = float(contract["cost_stress_multiplier"])
    previous_weights = {instrument: 0.0 for instrument in universe}
    periods: list[dict[str, Any]] = []
    for event_time in times:
        signals = {
            instrument: direction
            * candidate_signals(panel[event_time][instrument])[candidate_id]
            for instrument in universe
        }
        next_time = event_time + horizon_ms
        prices = {
            instrument: _positive(
                panel[event_time][instrument]["external_price_reference"][
                    "reference_price"
                ],
                "decision mark price",
            )
            for instrument in universe
        }
        next_prices = {
            instrument: _positive(
                panel[next_time][instrument]["external_price_reference"][
                    "reference_price"
                ],
                "realization mark price",
            )
            for instrument in universe
        }
        forward_returns = {
            instrument: next_prices[instrument] / prices[instrument] - 1.0
            for instrument in universe
        }
        weights = _weights(
            signals, long_count=long_count, short_count=short_count
        )
        turnover = 0.5 * sum(
            abs(weights[instrument] - previous_weights[instrument])
            for instrument in universe
        )
        contributions = {
            instrument: weights[instrument] * forward_returns[instrument]
            for instrument in universe
        }
        gross_return = sum(contributions.values())
        rank_ic = _correlation(
            _average_ranks([signals[instrument] for instrument in universe]),
            _average_ranks(
                [forward_returns[instrument] for instrument in universe]
            ),
        )
        if rank_ic is None:
            raise BookDepthAlphaError("rank IC is undefined")
        periods.append(
            {
                "event_time_ms": event_time,
                "gross_return": gross_return,
                "turnover": turnover,
                "net_return": gross_return - turnover * cost_rate,
                "double_cost_net_return": (
                    gross_return - turnover * cost_rate * stress_multiplier
                ),
                "rank_ic": rank_ic,
                "instrument_contributions": contributions,
            }
        )
        previous_weights = weights

    third_size = max(1, len(periods) // 3)
    thirds = (
        periods[:third_size],
        periods[third_size : 2 * third_size],
        periods[2 * third_size :],
    )
    positive_thirds = sum(
        1
        for section in thirds
        if section
        and math.prod(1.0 + row["net_return"] for row in section) > 1.0
    )
    absolute_contribution = {
        instrument: sum(
            abs(row["instrument_contributions"][instrument]) for row in periods
        )
        for instrument in universe
    }
    total_absolute_contribution = sum(absolute_contribution.values())
    maximum_contribution_fraction = (
        max(absolute_contribution.values()) / total_absolute_contribution
        if total_absolute_contribution
        else None
    )
    annualization = float(contract["annualization_periods"])
    net_summary = _summary(
        [row["net_return"] for row in periods], annualization
    )
    double_cost_summary = _summary(
        [row["double_cost_net_return"] for row in periods], annualization
    )
    ic_summary = _ic_summary([row["rank_ic"] for row in periods])
    gates = contract["required_gates"]
    checks = {
        "net_sharpe": (
            net_summary["net_sharpe"] is not None
            and net_summary["net_sharpe"] >= float(gates["net_sharpe_minimum"])
        ),
        "net_mean_return": (
            net_summary["mean_return"] is not None
            and net_summary["mean_return"] > float(gates["net_mean_return_minimum"])
        ),
        "double_cost": (
            double_cost_summary["net_sharpe"] is not None
            and double_cost_summary["net_sharpe"]
            > float(gates["double_cost_net_sharpe_minimum"])
        ),
        "rank_ic_mean": (
            ic_summary["mean_ic"] is not None
            and ic_summary["mean_ic"] > float(gates["rank_ic_mean_minimum"])
        ),
        "rank_ic_t_stat": (
            ic_summary["t_stat"] is not None
            and ic_summary["t_stat"] >= float(gates["rank_ic_t_stat_minimum"])
        ),
        "chronological_thirds": (
            positive_thirds
            >= int(gates["positive_chronological_thirds_minimum"])
        ),
        "instrument_concentration": (
            maximum_contribution_fraction is not None
            and maximum_contribution_fraction
            <= float(gates["maximum_single_instrument_gross_pnl_fraction"])
        ),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "STRICTLY_UNSEEN_PRE_REGISTERED_CONFIRMATION",
        "candidate_id": candidate_id,
        "direction": direction,
        "horizon_hours": horizon_hours,
        "decision_count": len(periods),
        "first_decision_event_time_ms": periods[0]["event_time_ms"],
        "last_decision_event_time_ms": periods[-1]["event_time_ms"],
        "net": net_summary,
        "double_cost_net": double_cost_summary,
        "rank_ic": ic_summary,
        "positive_chronological_thirds": positive_thirds,
        "maximum_single_instrument_gross_pnl_fraction": (
            maximum_contribution_fraction
        ),
        "mean_turnover": statistics.fmean(row["turnover"] for row in periods),
        "gate_checks": checks,
        "formal_validation_dag_required": True,
        "formal_admission": False,
        "forward_credit": False,
        "testnet_authorized": False,
        "order_submission_authorized": False,
    }


__all__ = [
    "BookDepthAlphaError",
    "candidate_signals",
    "evaluate_confirmation",
    "evaluate_exploration",
    "evaluate_registered_candidates",
]
