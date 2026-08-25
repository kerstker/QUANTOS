"""Freeze the forward ICT liquidity-sweep experiment before outcomes.

This append-once recorder reads no realtime values, prices, labels, returns,
trial registry, lifecycle state, credentials, broker, or order surface.  A
future evaluator must verify this artifact before it may inspect post-freeze
outcomes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import re
import time

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.research.ict_liquidity_sweep import IctLiquiditySweepConfig
from tools.cache_io import atomic_write_json


SOURCE = "tools.ict_liquidity_sweep_preregistration"
SEARCH_KIND = "ICT_LIQUIDITY_SWEEP_FORWARD_V1"
FAMILY = "ict_liquidity_sweep_forward_v1"
CONFIG = IctLiquiditySweepConfig()
PRIMARY_ALIAS = "ict_session_absorption_reversal_forward_v1"
ALL_DAY_ALIAS = "ict_all_day_absorption_reversal_control_v1"
SWEEP_ONLY_ALIAS = "ict_session_sweep_only_reversal_control_v1"
CONTINUATION_ALIAS = "ict_session_absorption_continuation_control_v1"
SESSION_PLACEBO_ALIAS = "ict_placebo_session_absorption_reversal_v1"
MIN_COLLECTION_MS = 90 * 86_400_000
MIN_ELIGIBLE_EVENT_DAYS = 60
MIN_ELIGIBLE_EVENTS = 500
_COMMIT = re.compile(r"[0-9a-f]{40}")


UNIVERSE = (
    "ADAUSDT",
    "ATOMUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "BNBUSDT",
    "BTCUSDT",
    "DOGEUSDT",
    "DOTUSDT",
    "ETHUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "SOLUSDT",
    "UNIUSDT",
    "XRPUSDT",
)


ECONOMIC_HYPOTHESIS = (
    "A fixed prior-hour price-level breach caused by aggressive taker flow "
    "reverses after a causal reclaim and opposing displayed-depth "
    "replenishment, with stronger net evidence in preregistered ICT session "
    "windows than in all-day, sweep-only, continuation, or clock-placebo "
    "controls."
)


EVALUATION_CONTRACT = {
    "schema": "ict_liquidity_sweep_forward_contract.v1",
    "purpose": "FORWARD_PREREGISTRATION_NOT_ALPHA_ADMISSION",
    "provenance": {
        "practitioner_source": {
            "title": "The Inner Circle Trader primary education library",
            "author": "Michael J. Huddleston",
            "tier": "PRACTITIONER_PRIMARY_NOT_PROFITABILITY_EVIDENCE",
            "url": "https://www.theinnercircletrader.com/",
        },
        "academic_mechanism": {
            "title": "The Price Impact of Order Book Events",
            "doi": "10.1093/jjfinec/nbt003",
            "tier": "PEER_REVIEWED_MECHANISM_NOT_STRATEGY_VALIDATION",
        },
        "translation_boundary": (
            "liquidity sweep and session labels are practitioner claims; "
            "aggressive-flow and displayed-depth tests are observable proxies, "
            "not evidence of institutional intent"
        ),
    },
    "data": {
        "source": "binance_realtime_features.db",
        "admissible_rows": (
            "complete PIT 30-second frames with decision_time strictly after "
            "preregistered_at_ms"
        ),
        "required_features": (
            "rt_mid_price",
            "rt_spread_bps",
            "rt_taker_imbalance_5s",
            "rt_depth_imbalance_5",
            "rt_executable_depth_5_usd",
        ),
        "universe": UNIVERSE,
        "missing_frame": "affected formation_or_label_is_ineligible_not_forward_filled",
        "revision_policy": "earliest retained PIT observation only",
    },
    "event_definition": {
        "cadence_ms": CONFIG.cadence_ms,
        "prior_level": "max_or_min_of_120_strictly_prior_contiguous_frames",
        "prior_level_minutes": 60,
        "breach": "mid_crosses_prior_level_by_at_least_one_current_full_spread",
        "aggressive_flow": "same_side_abs_taker_imbalance_5s_at_least_0_50",
        "reclaim": "first_mid_back_inside_prior_level_within_4_frames",
        "reclaim_minutes": 2,
        "displayed_absorption": (
            "confirmation_depth_imbalance_opposes_breach_by_at_least_0_25 "
            "and executable_depth_is_at_least_breach_depth"
        ),
        "instrument_lockout_ms": CONFIG.holding_ms,
        "future_bar_access": False,
    },
    "sessions": {
        "timezone": CONFIG.session_timezone,
        "dst_policy": "IANA_TIMEZONE_CONVERSION_AT_EVENT_TIME",
        "primary_local_hours": CONFIG.primary_session_hours,
        "primary_label": "ICT_LONDON_02_05_AND_NEW_YORK_AM_07_10",
        "placebo_local_hours": CONFIG.placebo_session_hours,
        "placebo_label": "SAME_DURATION_12_HOUR_ROTATED_CLOCK_CONTROL",
        "post_hoc_session_choice": False,
    },
    "decision_and_label": {
        "decision": "first causal reclaim frame plus 250ms execution latency",
        "holding_ms": CONFIG.holding_ms,
        "exit": "first complete frame_at_or_after_decision_plus_15_minutes",
        "maximum_label_lateness_ms": CONFIG.cadence_ms,
        "overlapping_same_instrument_labels": False,
        "event_returns_aggregated": (
            "equal-risk-within-day then one UTC daily portfolio return per candidate"
        ),
    },
    "portfolio_and_cost": {
        "diagnostic_target_aum_usd": 100_000.0,
        "gross_exposure_cap": 1.0,
        "leverage_cap": 1.0,
        "maximum_participation": 0.05,
        "base_fee_bps_per_side": 4.0,
        "base_latency_slippage_bps_per_side": 2.0,
        "spread": "cross_contemporaneous_half_spread_at_entry_and_exit",
        "impact": "calibrate_from_PIT_executable_depth_or_fail_closed",
        "double_cost": "double_fee_latency_slippage_and_impact_coefficient",
    },
    "candidate_aliases": (
        PRIMARY_ALIAS,
        ALL_DAY_ALIAS,
        SWEEP_ONLY_ALIAS,
        CONTINUATION_ALIAS,
        SESSION_PLACEBO_ALIAS,
    ),
    "unblinding": {
        "minimum_collection_ms": MIN_COLLECTION_MS,
        "minimum_eligible_event_days": MIN_ELIGIBLE_EVENT_DAYS,
        "minimum_eligible_events": MIN_ELIGIBLE_EVENTS,
        "single_terminal_unblind": True,
        "interim_return_statistics_allowed": False,
        "interim_data_health_only": True,
        "extension_if_incomplete": "availability_only_never_outcome_based",
    },
    "decision_bars": {
        "all_five_candidates_registered_before_outcome_access": True,
        "global_trial_ledger_delta": 5,
        "full_evaluator_evidence": True,
        "evaluator_verdict": "PASS",
        "min_net_sharpe": 1.0,
        "min_net_annual_return": 0.0,
        "min_dsr": 0.95,
        "max_pbo": 0.25,
        "max_drawdown": 0.10,
        "locked_final_third_min_sharpe": 0.80,
        "walk_forward_positive_fraction": 0.75,
        "double_cost_min_sharpe": 0.75,
        "double_cost_positive_return": True,
        "primary_minus_all_day_min_sharpe": 0.20,
        "primary_minus_sweep_only_min_sharpe": 0.20,
        "must_beat_continuation_sign": True,
        "must_beat_placebo_session": True,
        "leave_one_instrument_out_positive_fraction": 0.80,
        "maximum_single_instrument_event_fraction": 0.20,
        "maximum_single_day_pnl_fraction": 0.10,
        "max_participation": 0.05,
        "capacity_crowding_regime_tail_evidence": True,
    },
    "parameter_scan": False,
    "outcome_access_before_unblind": False,
    "pass_authority": "L2_PAPER_ADMISSION_REVIEW_ONLY",
    "testnet_or_live_authority": False,
}
CONTRACT_HASH = stable_hash(EVALUATION_CONTRACT)


_CANDIDATE_SPECS = (
    (PRIMARY_ALIAS, "session_absorption_reversal", "PRIMARY"),
    (ALL_DAY_ALIAS, "all_day_absorption_reversal", "ALL_DAY_CONTROL"),
    (SWEEP_ONLY_ALIAS, "session_sweep_only_reversal", "SWEEP_ONLY_CONTROL"),
    (CONTINUATION_ALIAS, "session_absorption_continuation", "SIGN_CONTROL"),
    (
        SESSION_PLACEBO_ALIAS,
        "placebo_session_absorption_reversal",
        "CLOCK_PLACEBO_CONTROL",
    ),
)


def candidate_definitions() -> tuple[tuple[AlphaDefinition, str], ...]:
    definitions = []
    for alias, mode, role in _CANDIDATE_SPECS:
        params = {
            "contract_hash": CONTRACT_HASH,
            "family": FAMILY,
            "mode": mode,
            "role": role,
        }
        seed = AlphaDefinition(
            alpha_id=alias,
            name=alias,
            factor_ids=(
                "prior_60m_level_sweep",
                "aggressive_taker_absorption",
                "displayed_depth_replenishment",
                "dst_safe_session_clock",
            ),
            universe="fixed_binance_usdm_realtime_14",
            params=params,
        )
        definition = AlphaDefinition(
            alpha_id=f"{alias}_{seed.definition_hash[:16]}",
            name=alias,
            factor_ids=seed.factor_ids,
            universe=seed.universe,
            params=params,
        )
        definitions.append((definition, role))
    return tuple(definitions)


def build_preregistration(
    *, source_commit: str, preregistered_at_ms: int
) -> dict[str, object]:
    if _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character Git commit")
    if isinstance(preregistered_at_ms, bool) or not isinstance(preregistered_at_ms, int):
        raise TypeError("preregistered_at_ms must be an integer")
    if preregistered_at_ms < 0:
        raise ValueError("preregistered_at_ms must be non-negative")
    candidates = [
        {
            "alias": definition.name,
            "alpha_id": definition.alpha_id,
            "definition_hash": definition.definition_hash,
            "role": role,
            "params": dict(definition.params),
        }
        for definition, role in candidate_definitions()
    ]
    earliest_unblind = preregistered_at_ms + MIN_COLLECTION_MS
    identity = {
        "contract_hash": CONTRACT_HASH,
        "source_commit": source_commit,
        "preregistered_at_ms": preregistered_at_ms,
        "earliest_unblind_at_ms": earliest_unblind,
        "candidate_definition_hashes": [
            item["definition_hash"] for item in candidates
        ],
    }
    return {
        "schema_version": "ict_liquidity_sweep_preregistration.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "APPEND_ONCE_FORWARD_PREREGISTRATION",
        "source": SOURCE,
        "search_kind": SEARCH_KIND,
        "source_commit": source_commit,
        "preregistered_at_ms": preregistered_at_ms,
        "earliest_unblind_at_ms": earliest_unblind,
        "admissible_decision_time_rule": "decision_time_ms > preregistered_at_ms",
        "contract_hash": CONTRACT_HASH,
        "preregistration_id": stable_hash(identity),
        "economic_hypothesis": ECONOMIC_HYPOTHESIS,
        "config": asdict(CONFIG),
        "evaluation_contract": EVALUATION_CONTRACT,
        "candidates": candidates,
        "outcomes_accessed": False,
        "return_statistics_computed": False,
        "trial_registry_mutated": False,
        "alpha_eligible_count": 0,
        "safety": {
            "credentials": False,
            "broker_connectivity": False,
            "order_submission": False,
            "testnet_or_live_authority": False,
            "human_review_required_for_l2_paper_admission": True,
        },
    }


def write_preregistration(
    *,
    output: str | Path,
    source_commit: str,
    preregistered_at_ms: int | None = None,
) -> dict[str, object]:
    path = Path(output)
    if path.exists():
        raise FileExistsError("preregistration artifact is append-once and already exists")
    recorded_at = (
        time.time_ns() // 1_000_000
        if preregistered_at_ms is None
        else preregistered_at_ms
    )
    document = build_preregistration(
        source_commit=source_commit,
        preregistered_at_ms=recorded_at,
    )
    atomic_write_json(str(path), document, indent=2)
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    document = write_preregistration(
        output=args.output,
        source_commit=args.source_commit,
    )
    print(
        "ICT liquidity-sweep preregistration: "
        f"id={document['preregistration_id']} "
        f"contract={document['contract_hash']} "
        f"earliest_unblind_at_ms={document['earliest_unblind_at_ms']} "
        "outcomes_accessed=false"
    )
    print(f"evidence={args.output}")


if __name__ == "__main__":
    main()


__all__ = [
    "ALL_DAY_ALIAS",
    "CONFIG",
    "CONTINUATION_ALIAS",
    "CONTRACT_HASH",
    "EVALUATION_CONTRACT",
    "MIN_COLLECTION_MS",
    "MIN_ELIGIBLE_EVENT_DAYS",
    "MIN_ELIGIBLE_EVENTS",
    "PRIMARY_ALIAS",
    "SESSION_PLACEBO_ALIAS",
    "SWEEP_ONLY_ALIAS",
    "UNIVERSE",
    "build_preregistration",
    "candidate_definitions",
    "write_preregistration",
]
