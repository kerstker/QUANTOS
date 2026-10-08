"""Freeze the forward attention-conditioned momentum experiment before returns.

This recorder reads no market prices, attention values, labels, returns, trial
registry, lifecycle state, credentials, broker, or order surface.  It creates a
single append-once contract artifact that a future evaluator must verify before
it is allowed to inspect outcomes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import re
import time

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.research.attention_momentum import AttentionMomentumConfig
from tools.cache_io import atomic_write_json


SOURCE = "tools.attention_momentum_preregistration"
SEARCH_KIND = "ATTENTION_CONDITIONED_MOMENTUM_FORWARD_V1"
FAMILY = "wikimedia_attention_conditioned_momentum_forward_v1"
CONFIG = AttentionMomentumConfig()
PRIMARY_ALIAS = "attention_conditioned_momentum_forward_v1"
PRICE_ONLY_ALIAS = "attention_price_only_momentum_control_v1"
TIMESTAMP_PLACEBO_ALIAS = "attention_timestamp_placebo_control_v1"
ATTENTION_ONLY_ALIAS = "attention_only_control_v1"
EARLIEST_UNBLIND_AT_MS = 1_819_670_400_000  # 2027-08-31T00:00:00Z
MIN_ELIGIBLE_OUTCOME_DAYS = 252
_COMMIT = re.compile(r"[0-9a-f]{40}")


ECONOMIC_HYPOTHESIS = (
    "A source-timestamped crypto attention innovation amplifies a fixed "
    "seven-day price-momentum signal enough to improve the next-day, "
    "cost-adjusted cross-sectional return over price momentum alone."
)

EVALUATION_CONTRACT = {
    "schema": "attention_momentum_forward_contract.v1",
    "purpose": "FORWARD_PREREGISTRATION_NOT_ALPHA_ADMISSION",
    "source_paper": {
        "title": "Risks and Returns of Cryptocurrency",
        "doi": "10.1093/rfs/hhaa113",
        "working_paper": "10.3386/w24877",
        "portability": "DIRECT_CRYPTO_ECONOMIC_PRIOR",
    },
    "attention_proxy": {
        "source": "Wikimedia Analytics API daily per-article pageviews",
        "project": "en.wikipedia.org",
        "access": "all-access",
        "agent": "user",
        "feature_id": "wikimedia_crypto_attention_pageviews_forward_vintage",
        "vintage_selection": (
            "earliest complete 13-instrument knowledge_time per event day "
            "strictly after preregistered_at_ms"
        ),
        "revision_policy": "never replace earliest admitted vintage",
        "retrospective_snapshot_allowed": False,
    },
    "universe": (
        "ADA",
        "AVAX",
        "BCH",
        "BTC",
        "DOGE",
        "DOT",
        "ETC",
        "ETH",
        "LINK",
        "LTC",
        "SOL",
        "TRX",
        "XRP",
    ),
    "attention_lookback_days": CONFIG.attention_lookback_days,
    "attention_innovation": (
        "cross_sectional_rank_z(log1p(pageviews_t) - "
        "trailing_28_event_day_median_log1p_pageviews)"
    ),
    "momentum_lookback_days": CONFIG.momentum_lookback_days,
    "momentum": (
        "cross_sectional_rank_z(log(last_complete_close_at_or_before_knowledge_time "
        "/ close_7_complete_days_earlier))"
    ),
    "primary_signal": (
        "rank_z(momentum_rank_z * max(attention_innovation_rank_z, 0))"
    ),
    "decision_and_label": {
        "decision_time": "attention earliest-complete knowledge_time",
        "execution_price": "first complete Binance USDM daily close strictly after decision_time",
        "exit_price": "next complete Binance USDM daily close",
        "holding_days": CONFIG.holding_days,
        "overlapping_labels": False,
        "missing_attention_day": "cash_zero_return_and_source_outage_counted",
        "missing_price_or_exit": "fail_closed_data_integrity_rejection",
    },
    "portfolio": {
        "construction": "cross_sectional_dollar_neutral_top_bottom_quantiles",
        "quantile": CONFIG.quantile,
        "gross_exposure": 1.0,
        "target_aum_usd": 1_000_000.0,
        "max_participation": 0.05,
        "leverage_cap": 1.0,
    },
    "costs": {
        "base_fee_bps": 4.0,
        "base_slippage_bps": 6.0,
        "impact": "calibrated_or_principled_band_fail_closed",
        "double_cost_fee_bps": 8.0,
        "double_cost_slippage_bps": 12.0,
        "double_impact_coefficient": True,
        "funding_cashflows": "include_actual_PIT_funding_when_perpetual_held",
    },
    "candidate_aliases": (
        PRIMARY_ALIAS,
        PRICE_ONLY_ALIAS,
        TIMESTAMP_PLACEBO_ALIAS,
        ATTENTION_ONLY_ALIAS,
    ),
    "timestamp_placebo": {
        "selection": (
            "prior-only lag = 1 + sha256(fixed_salt:event_time)[0:8] mod 28"
        ),
        "window_days": CONFIG.timestamp_placebo_window_days,
        "future_timestamp_allowed": False,
    },
    "source_outage": {
        "no_forward_fill": True,
        "no_day_drop": True,
        "portfolio_return": 0.0,
        "maximum_outage_fraction": 0.05,
    },
    "unblinding": {
        "earliest_unblind_at_ms": EARLIEST_UNBLIND_AT_MS,
        "minimum_eligible_outcome_days": MIN_ELIGIBLE_OUTCOME_DAYS,
        "minimum_warmup_attention_days": CONFIG.attention_lookback_days,
        "single_terminal_unblind": True,
        "interim_return_statistics_allowed": False,
        "interim_data_health_only": True,
        "extension_if_data_incomplete": "availability_only_never_outcome_based",
    },
    "decision_bars": {
        "all_four_candidates_registered_before_outcome_access": True,
        "global_trial_ledger_delta": 4,
        "full_evaluator_evidence": True,
        "evaluator_verdict": "PASS",
        "min_net_sharpe": 1.0,
        "min_net_annual_return": 0.0,
        "min_dsr": 0.95,
        "max_pbo": 0.25,
        "max_drawdown": 0.15,
        "double_cost_min_sharpe": 0.75,
        "double_cost_positive_return": True,
        "primary_minus_price_only_min_sharpe": 0.20,
        "paired_primary_minus_price_only_min_information_ratio": 0.50,
        "must_beat_timestamp_placebo": True,
        "must_beat_attention_only": True,
        "max_source_outage_fraction": 0.05,
        "max_participation": 0.05,
        "capacity_and_crowding_assessed": True,
        "regime_and_tail_evidence": True,
    },
    "parameter_scan": False,
    "outcome_access_before_unblind": False,
    "pass_authority": "L2_PAPER_ADMISSION_REVIEW_ONLY",
    "testnet_or_live_authority": False,
}
CONTRACT_HASH = stable_hash(EVALUATION_CONTRACT)

_CANDIDATE_SPECS = (
    (PRIMARY_ALIAS, "attention_conditioned_momentum", "PRIMARY"),
    (PRICE_ONLY_ALIAS, "price_only_momentum", "PRICE_ONLY_CONTROL"),
    (TIMESTAMP_PLACEBO_ALIAS, "timestamp_placebo", "TIMESTAMP_PLACEBO"),
    (ATTENTION_ONLY_ALIAS, "attention_only", "ATTENTION_ONLY_CONTROL"),
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
            factor_ids=("wikimedia_attention_innovation_28d", "price_momentum_7d"),
            universe="fixed_crypto_attention_13",
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
    *,
    source_commit: str,
    preregistered_at_ms: int,
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
    identity = {
        "contract_hash": CONTRACT_HASH,
        "source_commit": source_commit,
        "preregistered_at_ms": preregistered_at_ms,
        "candidate_definition_hashes": [
            item["definition_hash"] for item in candidates
        ],
    }
    return {
        "schema_version": "attention_momentum_preregistration.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "APPEND_ONCE_FORWARD_PREREGISTRATION",
        "source": SOURCE,
        "search_kind": SEARCH_KIND,
        "source_commit": source_commit,
        "preregistered_at_ms": preregistered_at_ms,
        "admissible_knowledge_time_rule": "knowledge_time > preregistered_at_ms",
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
        "attention momentum preregistration: "
        f"id={document['preregistration_id']} "
        f"contract={document['contract_hash']} outcomes_accessed=false"
    )
    print(f"evidence={args.output}")


if __name__ == "__main__":
    main()


__all__ = [
    "ATTENTION_ONLY_ALIAS",
    "CONTRACT_HASH",
    "EARLIEST_UNBLIND_AT_MS",
    "EVALUATION_CONTRACT",
    "MIN_ELIGIBLE_OUTCOME_DAYS",
    "PRICE_ONLY_ALIAS",
    "PRIMARY_ALIAS",
    "TIMESTAMP_PLACEBO_ALIAS",
    "build_preregistration",
    "candidate_definitions",
    "write_preregistration",
]
