"""Freeze a cross-venue borrow-pressure mechanism before outcomes.

The append-once recorder reads no market rows, returns, trial registry,
lifecycle authority, credentials, broker, or order surface.  Public OKX borrow
rates are a cost observation, never evidence of lendable quantity.  A future
evaluator must verify this exact artifact before inspecting forward labels.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import time

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.research.borrow_pressure import (
    BASIS_BPS_MAX,
    BORROW_ACCELERATION_MIN,
    BORROW_PERCENTILE_MIN,
    CHEAP_BORROW_PERCENTILE_MAX,
    ENTRY_SPREAD_BPS_MAX,
    FUNDING_RATE_MAX,
    TRAILING_RETURN_MAX,
)
from tools.cache_io import atomic_write_json


SOURCE = "tools.borrow_pressure_preregistration"
SEARCH_KIND = "CROSS_VENUE_BORROW_SHORT_PRESSURE_FORWARD_V1"
FAMILY = "cross_venue_borrow_short_pressure_forward_v1"
PRIMARY_ALIAS = "borrow_short_pressure_reversion_forward_v1"
SIGN_CONTROL_ALIAS = "borrow_short_pressure_continuation_control_v1"
BORROW_ONLY_ALIAS = "borrow_only_reversion_control_v1"
FUNDING_ONLY_ALIAS = "funding_only_reversion_control_v1"
CHEAP_BORROW_ALIAS = "cheap_borrow_reversion_control_v1"
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
MIN_COLLECTION_MS = 120 * 86_400_000
MIN_ELIGIBLE_DAYS = 75
MIN_INDEPENDENT_EPISODES = 100
MIN_EPISODES_PER_INSTRUMENT = 4
_COMMIT = re.compile(r"[0-9a-f]{40}")


ECONOMIC_HYPOTHESIS = (
    "When the cost of borrowing a cryptoasset is cross-sectionally high and "
    "rising on OKX while Binance perpetual funding and mark/index basis are "
    "negative after a price decline, short demand is paying on two distinct "
    "financing surfaces.  A crowded short inventory unwind may then produce a "
    "24-hour rebound large enough to survive public simulated execution costs."
)


EVALUATION_CONTRACT = {
    "schema": "cross_venue_borrow_short_pressure_forward_contract.v1",
    "purpose": "MECHANISM_FORWARD_PREREGISTRATION_NOT_ALPHA_ADMISSION",
    "economic_payer": {
        "primary": "LEVERAGED_CROWD",
        "secondary": "CAPITAL_CONSTRAINED_CROSS_VENUE",
        "who_pays": (
            "crowded short holders paying asset-borrow or negative perpetual "
            "funding costs, whose constrained unwind crosses finite liquidity"
        ),
        "persistence_assumption": (
            "leveraged directional demand and venue-fragmented financing limits "
            "recur across independent leverage cycles"
        ),
        "independent_bet_unit": (
            "one currency decision followed by a seven-day currency lockout"
        ),
    },
    "provenance": {
        "costly_short_selling": {
            "title": "Dynamic Equilibrium with Costly Short-Selling and Lending Market",
            "doi": "10.1093/rfs/hhad060",
            "tier": "PEER_REVIEWED_MECHANISM_NOT_CRYPTO_STRATEGY_VALIDATION",
        },
        "lending_fee_information": {
            "title": "Information Value of Stock Lending Fees: Are Lenders Price Takers?",
            "doi": "10.1093/rof/rfw075",
            "tier": "PEER_REVIEWED_CROSS_ASSET_PRIOR_NOT_DIRECTION_TRANSFER",
        },
    },
    "data": {
        "okx_snapshot_authority": "data/cache/okx_public_borrow_rates.json",
        "okx_vintage_authority": "data/cache/okx_borrow_vintages.db",
        "okx_channel_id": "okx_public_borrow_rates",
        "binance_authority": "data/cache/binance_realtime_stream.db",
        "binance_channel_id": "binance_usdm_websocket_stream",
        "universe": UNIVERSE,
        "formal_forward_credit": (
            "OKX knowledge_time_ms and Binance received_at_ms/processed_at_ms "
            "must be strictly after preregistered_at_ms"
        ),
        "historical_okx_rows_pit_before_fetch": False,
        "historical_backfill_credit": False,
        "borrow_availability": "UNAVAILABLE_PUBLIC_AMOUNT_FIELDS_DEPRECATED",
        "borrow_availability_may_infer_capacity": False,
        "revision_policy": "append_only_earliest_retained_observation_only",
        "missing_or_stale_surface": "decision_ineligible_not_forward_filled",
    },
    "episode_definition": {
        "decision_time": "02:15 UTC once per eligible calendar day",
        "minimum_forward_daily_vintages_before_first_decision": 30,
        "borrow_measure": "OKX annual_average_borrow_rate_24h",
        "borrow_cross_section_percentile_min": BORROW_PERCENTILE_MIN,
        "estimated_minus_average_borrow_rate_min": BORROW_ACCELERATION_MIN,
        "binance_funding_rate_max": FUNDING_RATE_MAX,
        "binance_mark_index_basis_bps_max": BASIS_BPS_MAX,
        "trailing_24h_mark_return_max": TRAILING_RETURN_MAX,
        "maximum_entry_spread_bps": ENTRY_SPREAD_BPS_MAX,
        "currency_lockout_ms": 7 * 86_400_000,
        "all_filters_use_knowledge_time_at_or_before_decision": True,
        "future_event_access": False,
        "outcome_in_episode_selection": False,
        "parameter_scan": False,
    },
    "decision_and_label": {
        "primary_direction": "LONG_BINANCE_USDM_PERPETUAL",
        "entry": (
            "cross first complete Binance public quote at_or_after decision plus 250ms"
        ),
        "holding_ms": 86_400_000,
        "exit": "cross first complete public quote at_or_after entry plus 24h",
        "maximum_entry_or_exit_quote_lateness_ms": 1_000,
        "overlapping_same_currency_labels": False,
        "funding_cash_flows": "all settled funding during the holding interval",
        "daily_portfolio": "equal-risk across eligible currencies with frozen caps",
    },
    "portfolio_and_execution": {
        "reference_aum_usd": 1_000_000.0,
        "gross_exposure_cap": 1.0,
        "leverage_cap": 1.0,
        "single_currency_weight_cap": 0.10,
        "maximum_participation": 0.02,
        "fees": "versioned Binance fee schedule required; absence blocks PASS",
        "spread": "cross contemporaneous executable quote at entry and exit",
        "impact": "walk reconstructed depth or fail closed",
        "partial_fill": "retain only executable quantity and record rejected remainder",
        "latency_slippage_bps_per_side": 2.0,
        "borrow_capacity": (
            "never inferred because public lendable quantity is unavailable; "
            "capacity is bounded only by the traded perpetual book"
        ),
        "double_cost": "double fees, latency slippage, and impact coefficient",
        "incremental_baseline": "best eligible existing L2 paper portfolio",
    },
    "candidate_aliases": (
        PRIMARY_ALIAS,
        SIGN_CONTROL_ALIAS,
        BORROW_ONLY_ALIAS,
        FUNDING_ONLY_ALIAS,
        CHEAP_BORROW_ALIAS,
    ),
    "controls": {
        "sign": "same primary episodes and costs with SHORT direction",
        "borrow_only": "retain borrow and price filters; remove funding and basis filters",
        "funding_only": "retain funding, basis and price filters; remove borrow filters",
        "cheap_borrow": (
            f"replace top-20-percent borrow rank with bottom-{CHEAP_BORROW_PERCENTILE_MAX:.0%} while "
            "retaining funding, basis and price filters"
        ),
        "post_hoc_control_choice": False,
    },
    "unblinding": {
        "minimum_collection_ms": MIN_COLLECTION_MS,
        "minimum_eligible_days": MIN_ELIGIBLE_DAYS,
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
        "min_net_sharpe": 0.80,
        "min_dsr": 0.95,
        "max_pbo": 0.25,
        "max_drawdown": 0.12,
        "locked_final_third_min_sharpe": 0.50,
        "walk_forward_positive_fraction": 0.70,
        "double_cost_min_sharpe": 0.50,
        "double_cost_positive_return": True,
        "primary_must_beat_all_four_controls": True,
        "primary_minus_best_control_min_sharpe": 0.15,
        "maximum_abs_correlation_to_existing_book": 0.60,
        "minimum_execution_confidence": 0.80,
        "minimum_capacity_usd": 1_000_000.0,
        "minimum_independent_prediction_count": MIN_INDEPENDENT_EPISODES,
        "maximum_single_instrument_event_fraction": 0.40,
        "maximum_single_day_pnl_fraction": 0.10,
        "capacity_crowding_regime_tail_evidence": True,
    },
    "outcome_access_before_unblind": False,
    "pass_authority": "L2_PAPER_ADMISSION_REVIEW_ONLY",
    "testnet_or_live_authority": False,
}
CONTRACT_HASH = stable_hash(EVALUATION_CONTRACT)


_CANDIDATE_SPECS = (
    (PRIMARY_ALIAS, "borrow_short_pressure_reversion", "PRIMARY"),
    (SIGN_CONTROL_ALIAS, "borrow_short_pressure_continuation", "SIGN_CONTROL"),
    (BORROW_ONLY_ALIAS, "borrow_only_reversion", "BORROW_ABLATION_CONTROL"),
    (FUNDING_ONLY_ALIAS, "funding_only_reversion", "FUNDING_ABLATION_CONTROL"),
    (CHEAP_BORROW_ALIAS, "cheap_borrow_reversion", "SCARCITY_CONTROL"),
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
                "okx_borrow_cross_section_rank",
                "okx_estimated_borrow_acceleration",
                "binance_negative_funding",
                "binance_negative_basis",
                "binance_trailing_return",
            ),
            universe="forward_okx_binance_common_14",
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
        "schema_version": "borrow_pressure_preregistration.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "APPEND_ONCE_FORWARD_PREREGISTRATION",
        "source": SOURCE,
        "search_kind": SEARCH_KIND,
        "source_commit": source_commit,
        "preregistered_at_ms": preregistered_at_ms,
        "earliest_unblind_at_ms": earliest_unblind,
        "admissible_knowledge_time_rule": (
            "all source knowledge/receive/process times > preregistered_at_ms"
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
        "borrow-pressure preregistration: "
        f"id={document['preregistration_id']} "
        f"contract={document['contract_hash']} "
        f"earliest_unblind_at_ms={document['earliest_unblind_at_ms']} "
        "outcomes_accessed=false"
    )
    print(f"evidence={args.output}")


if __name__ == "__main__":
    main()


__all__ = [
    "BORROW_ONLY_ALIAS",
    "CHEAP_BORROW_ALIAS",
    "CONTRACT_HASH",
    "ECONOMIC_HYPOTHESIS",
    "EVALUATION_CONTRACT",
    "FUNDING_ONLY_ALIAS",
    "MIN_COLLECTION_MS",
    "MIN_ELIGIBLE_DAYS",
    "MIN_EPISODES_PER_INSTRUMENT",
    "MIN_INDEPENDENT_EPISODES",
    "PRIMARY_ALIAS",
    "SIGN_CONTROL_ALIAS",
    "UNIVERSE",
    "build_preregistration",
    "candidate_definitions",
    "write_preregistration",
]
