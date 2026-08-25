"""Freeze a cross-venue forced-liquidation mechanism before outcomes.

The recorder is append-once and deliberately outcome-blind.  It reads no
market rows, returns, trial registry, lifecycle authority, credentials, broker,
or order surface.  A future evaluator must verify this exact artifact before
it may inspect post-freeze labels.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import time

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from tools.cache_io import atomic_write_json


SOURCE = "tools.cross_venue_liquidation_preregistration"
SEARCH_KIND = "CROSS_VENUE_LIQUIDATION_RECOVERY_FORWARD_V1"
FAMILY = "cross_venue_liquidation_recovery_forward_v1"
PRIMARY_ALIAS = "cross_venue_liquidation_reversion_forward_v1"
CONTINUATION_ALIAS = "cross_venue_liquidation_continuation_control_v1"
SINGLE_VENUE_ALIAS = "single_venue_liquidation_reversion_control_v1"
URGENT_FLOW_ALIAS = "urgent_flow_without_liquidation_control_v1"
CLOCK_PLACEBO_ALIAS = "clock_placebo_liquidation_reversion_v1"
UNIVERSE = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
MIN_COLLECTION_MS = 90 * 86_400_000
MIN_ELIGIBLE_EVENT_DAYS = 60
MIN_INDEPENDENT_EPISODES = 500
MIN_EPISODES_PER_INSTRUMENT = 75
_COMMIT = re.compile(r"[0-9a-f]{40}")


ECONOMIC_HYPOTHESIS = (
    "A venue-specific forced-liquidation burst can consume finite displayed "
    "liquidity faster than capital-constrained cross-venue arbitrageurs can "
    "replenish it.  Once the forced flow becomes quiet and the book recovers, "
    "the Bybit overshoot relative to Binance should mean-revert net of "
    "executable spread, fees, latency, and impact."
)


EVALUATION_CONTRACT = {
    "schema": "cross_venue_liquidation_recovery_forward_contract.v1",
    "purpose": "MECHANISM_FORWARD_PREREGISTRATION_NOT_ALPHA_ADMISSION",
    "economic_payer": {
        "primary": "FORCED_LIQUIDATOR",
        "secondary": "CAPITAL_CONSTRAINED_CROSS_VENUE",
        "who_pays": (
            "leveraged accounts whose positions are forcibly closed into a "
            "temporarily depleted book, while fragmented venue capital delays "
            "immediate convergence"
        ),
        "persistence_assumption": (
            "maintenance-margin liquidation and venue-fragmented inventory "
            "constraints recur independently across leverage cycles"
        ),
        "independent_bet_unit": (
            "one instrument-side liquidation burst followed by a 30-minute "
            "instrument lockout"
        ),
    },
    "provenance": {
        "order_book_mechanism": {
            "title": "The Price Impact of Order Book Events",
            "doi": "10.1093/jjfinec/nbt003",
            "tier": "PEER_REVIEWED_MECHANISM_NOT_STRATEGY_VALIDATION",
        },
        "cross_venue_mechanism": {
            "title": "Price Discovery in Cryptocurrency Markets",
            "doi": "10.1257/pandp.20191020",
            "tier": "PEER_REVIEWED_VENUE_MECHANISM_NOT_EXECUTABILITY_PROOF",
        },
        "translation_boundary": (
            "the papers motivate finite-depth impact and venue segmentation; "
            "they do not validate this liquidation-reversion strategy"
        ),
    },
    "data": {
        "bybit_authority": "data/cache/bybit_linear_events.db",
        "bybit_channel_id": "bybit_linear_websocket_events",
        "binance_authority": "data/cache/binance_realtime_stream.db",
        "binance_channel_id": "binance_usdm_websocket_stream",
        "universe": UNIVERSE,
        "forward_credit": (
            "event_time_ms, received_at_ms, and processed_at_ms must all be "
            "strictly after preregistered_at_ms"
        ),
        "clock_policy": (
            "received_at_ms is knowledge time; every feature and fill must "
            "have processed_at_ms no later than its decision time"
        ),
        "bybit_required_channels": (
            "orderbook_l2",
            "public_trade",
            "all_liquidation",
            "ticker",
        ),
        "binance_required_surfaces": (
            "book",
            "depth20",
            "trade_window",
            "liquidation_window",
        ),
        "missing_or_broken_sequence": "episode_ineligible_not_forward_filled",
        "revision_policy": "append_only_earliest_retained_observation_only",
        "historical_backfill_credit": False,
    },
    "episode_definition": {
        "liquidation_side_semantics": (
            "Bybit liquidated_position_side Buy maps to forced Sell pressure; "
            "Sell maps to forced Buy pressure"
        ),
        "burst_grouping": (
            "same instrument and liquidated-position side with no more than "
            "5000ms between received liquidation prints"
        ),
        "quiet_period_ms": 2_000,
        "instrument_lockout_ms": 30 * 60 * 1_000,
        "pre_event_book_max_age_ms": 1_000,
        "cross_venue_quote_max_skew_ms": 250,
        "forced_notional_to_20bps_depth_min": 0.25,
        "same_side_taker_confirmation_window_ms": 2_000,
        "same_side_taker_share_min": 0.60,
        "bybit_minus_binance_pressure_move_min_bps": 2.0,
        "book_recovery_fraction_min": 0.50,
        "future_event_access": False,
        "outcome_in_episode_selection": False,
    },
    "decision_and_label": {
        "decision": (
            "first causal Bybit book after the quiet period that satisfies "
            "the frozen depth, taker-flow, recovery, and cross-venue-resilience "
            "tests, plus 250ms execution latency"
        ),
        "entry": "cross the first complete Bybit best quote at_or_after decision",
        "holding_ms": 60_000,
        "exit": "cross the first complete Bybit best quote at_or_after entry_plus_60s",
        "maximum_entry_or_exit_quote_lateness_ms": 500,
        "overlapping_same_instrument_labels": False,
        "event_returns_aggregated": (
            "equal-risk within UTC day, then one UTC daily portfolio return "
            "per candidate"
        ),
    },
    "portfolio_and_execution": {
        "reference_aum_usd": 1_000_000.0,
        "gross_exposure_cap": 1.0,
        "leverage_cap": 1.0,
        "maximum_participation": 0.05,
        "fees": (
            "versioned venue fee schedule required; absent schedule blocks "
            "PASS and is never treated as zero"
        ),
        "latency_slippage_bps_per_side": 2.0,
        "spread": "cross contemporaneous executable quote at entry and exit",
        "impact": "walk reconstructed depth or fail closed",
        "partial_fill": "size only the executable portion and retain unfilled quantity",
        "double_cost": "double fees, latency slippage, and impact coefficient",
        "incremental_baseline": "best eligible existing L2 paper portfolio",
    },
    "candidate_aliases": (
        PRIMARY_ALIAS,
        CONTINUATION_ALIAS,
        SINGLE_VENUE_ALIAS,
        URGENT_FLOW_ALIAS,
        CLOCK_PLACEBO_ALIAS,
    ),
    "controls": {
        "continuation": "same episodes and costs with the opposite trade sign",
        "single_venue": "remove only the Binance-resilience requirement",
        "urgent_flow": (
            "match symbol, side, UTC hour, depth ratio, and taker notional but "
            "require no liquidation print in the preceding/following 30s"
        ),
        "clock_placebo": (
            "same symbol and UTC minute shifted exactly six hours on the next "
            "eligible day, with no liquidation event"
        ),
        "post_hoc_control_choice": False,
    },
    "unblinding": {
        "minimum_collection_ms": MIN_COLLECTION_MS,
        "minimum_eligible_event_days": MIN_ELIGIBLE_EVENT_DAYS,
        "minimum_independent_episodes": MIN_INDEPENDENT_EPISODES,
        "minimum_episodes_per_instrument": MIN_EPISODES_PER_INSTRUMENT,
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
        "executable_value_policy": "executable_value.v1",
        "executable_value_status": "PASS",
        "incremental_net_return_positive": True,
        "min_net_sharpe": 1.0,
        "min_dsr": 0.95,
        "max_pbo": 0.25,
        "max_drawdown": 0.10,
        "locked_final_third_min_sharpe": 0.80,
        "walk_forward_positive_fraction": 0.75,
        "double_cost_min_sharpe": 0.75,
        "double_cost_positive_return": True,
        "primary_must_beat_all_four_controls": True,
        "primary_minus_best_control_min_sharpe": 0.20,
        "maximum_abs_correlation_to_existing_book": 0.60,
        "minimum_execution_confidence": 0.80,
        "minimum_capacity_usd": 1_000_000.0,
        "minimum_independent_prediction_count": MIN_INDEPENDENT_EPISODES,
        "maximum_single_instrument_event_fraction": 0.60,
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
    (PRIMARY_ALIAS, "cross_venue_liquidation_reversion", "PRIMARY"),
    (CONTINUATION_ALIAS, "cross_venue_liquidation_continuation", "SIGN_CONTROL"),
    (SINGLE_VENUE_ALIAS, "single_venue_liquidation_reversion", "VENUE_CONTROL"),
    (URGENT_FLOW_ALIAS, "urgent_flow_without_liquidation", "PAYER_CONTROL"),
    (CLOCK_PLACEBO_ALIAS, "clock_placebo_liquidation_reversion", "CLOCK_CONTROL"),
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
                "forced_liquidation_burst",
                "same_side_taker_pressure",
                "reconstructed_depth_recovery",
                "cross_venue_resilience_gap",
            ),
            universe="forward_bybit_binance_linear_3",
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
        "schema_version": "cross_venue_liquidation_preregistration.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "APPEND_ONCE_FORWARD_PREREGISTRATION",
        "source": SOURCE,
        "search_kind": SEARCH_KIND,
        "source_commit": source_commit,
        "preregistered_at_ms": preregistered_at_ms,
        "earliest_unblind_at_ms": earliest_unblind,
        "admissible_knowledge_time_rule": (
            "event_time_ms, received_at_ms, processed_at_ms > preregistered_at_ms"
        ),
        "contract_hash": CONTRACT_HASH,
        "preregistration_id": stable_hash(identity),
        "economic_hypothesis": ECONOMIC_HYPOTHESIS,
        "evaluation_contract": EVALUATION_CONTRACT,
        "candidates": candidates,
        "outcomes_accessed": False,
        "return_statistics_computed": False,
        "trial_registry_mutated": False,
        "alpha_eligible_count": 0,
        "safety": {
            "credentials": False,
            "private_stream": False,
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
        "cross-venue liquidation preregistration: "
        f"id={document['preregistration_id']} "
        f"contract={document['contract_hash']} "
        f"earliest_unblind_at_ms={document['earliest_unblind_at_ms']} "
        "outcomes_accessed=false"
    )
    print(f"evidence={args.output}")


if __name__ == "__main__":
    main()


__all__ = [
    "CLOCK_PLACEBO_ALIAS",
    "CONTINUATION_ALIAS",
    "CONTRACT_HASH",
    "ECONOMIC_HYPOTHESIS",
    "EVALUATION_CONTRACT",
    "MIN_COLLECTION_MS",
    "MIN_ELIGIBLE_EVENT_DAYS",
    "MIN_EPISODES_PER_INSTRUMENT",
    "MIN_INDEPENDENT_EPISODES",
    "PRIMARY_ALIAS",
    "SINGLE_VENUE_ALIAS",
    "UNIVERSE",
    "URGENT_FLOW_ALIAS",
    "build_preregistration",
    "candidate_definitions",
    "write_preregistration",
]
