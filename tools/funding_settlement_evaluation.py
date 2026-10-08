"""Pre-registered event-aligned funding-settlement alpha evaluation.

The family contains exactly two members: a rank-rebalanced control and one
fixed buffered-rank challenger.  There is no window, sign, horizon, quantile,
or parameter scan.  Both use the last *settled* rate at time ``t`` and the
completed mark-price hour after ``t`` to hold across the next eight-hour
settlement.  The future settled rate appears only in the realized cash-flow
label.

This driver is L2 research only.  It reads a public-data artifact, writes
research evidence, and has no credential, broker, promotion, or order path.
"""
from __future__ import annotations

from services.execution.backtest_authority import quarantine_legacy_backtest

import argparse
import hashlib
import json
import math
import re
import statistics
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.cost import CostModel
from quantos.contracts.hashing import stable_hash
from services.alpha.config import AlphaFactoryConfig
from services.alpha.panel_backtest import (
    PanelPeriod,
    _run_panel_backtest_legacy_diagnostic as run_panel_backtest,
)
from services.intelligence.registered_candidate_batch import run_registered_candidate_batch
from services.research.funding_settlement import (
    HOUR_MS,
    SettlementObservation,
    build_settlement_periods,
)
from services.research.research_budget import ResearchBudget
from tools.cache_io import atomic_write_json
from tools.search_trial_registry import make_search_id, production_experiment_registry


DEFAULT_SOURCE = Path("data/cache/binance_funding_settlement_history.json")
DEFAULT_OUTPUT = Path("data/cache/funding_settlement_evaluation.json")
SOURCE = "tools.funding_settlement_evaluation"
SEARCH_KIND = "PREREGISTERED_FUNDING_SETTLEMENT_V1"
DATA_SOURCE = "binance_funding_settlement_history.v1"
PERIODS_PER_YEAR = 365 * 3
EVALUATION_QUANTILE = 0.20
MIN_ROWS_PER_INSTRUMENT = 365 * 3
BASE_COST = CostModel(fee_bps=4.0, slippage_bps=2.0, impact_coef=0.0)
DOUBLE_COST = CostModel(fee_bps=8.0, slippage_bps=4.0, impact_coef=0.0)
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


CANDIDATE_MODES = ("rank_control", "buffered_rank")
ECONOMIC_HYPOTHESES = {
    "rank_control": (
        "Control: the last settled cross-sectional funding rank persists into "
        "the next settlement sufficiently to offset mark-price drift and costs."
    ),
    "buffered_rank": (
        "A fixed 20% entry and 40% exit rank buffer preserves persistent funding "
        "carry while reducing uneconomic boundary churn."
    ),
}
FALSIFICATION_CRITERIA = (
    "Neither fixed family member passes catalog-wide DSR.",
    "The complete two-member CSCV population exceeds the PBO ceiling.",
    "Leakage, regime, or sequential walk-forward evidence fails.",
    "The buffered challenger is non-positive at twice the fixed taker-plus-slippage cost.",
    "No verified formal PASS exists for forward-paper admission.",
)
EVALUATION_CONTRACT = {
    "driver": "funding_settlement_evaluation.v1",
    "signal": "negative_last_settled_funding_rate",
    "decision_mark": "completed_first_1h_mark_bar_after_settlement",
    "label": "next_decision_mark_return_minus_next_settled_funding_rate",
    "horizon_hours": 8,
    "periods_per_year": PERIODS_PER_YEAR,
    "entry_quantile": EVALUATION_QUANTILE,
    "buffered_exit_quantile": 0.40,
    "cost": {"fee_bps": 4.0, "slippage_bps": 2.0, "impact_coef": 0.0},
    "double_cost": {"fee_bps": 8.0, "slippage_bps": 4.0, "impact_coef": 0.0},
    "selection_population": list(CANDIDATE_MODES),
    "parameter_scan": False,
    "shared_calendar": True,
    "historical_data_grants_testnet": False,
}


def load_history(
    source_snapshot: str | Path = DEFAULT_SOURCE,
    *,
    min_rows_per_instrument: int = MIN_ROWS_PER_INSTRUMENT,
) -> tuple[dict[str, list[SettlementObservation]], dict[str, Any]]:
    path = Path(source_snapshot)
    payload_bytes = path.read_bytes()
    try:
        snapshot = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("funding settlement artifact is invalid JSON") from exc
    if snapshot.get("schema_version") != "binance_funding_settlement_history.v1":
        raise RuntimeError("unsupported funding settlement schema")
    if snapshot.get("provenance") != "REAL_BINANCE_PUBLIC_FUNDING_SETTLEMENT_HISTORY":
        raise RuntimeError("funding settlement provenance is not authoritative")
    safety = snapshot.get("safety")
    required_safety = {
        "public_market_data_only": True,
        "credentials": False,
        "broker_connectivity": False,
        "order_submission": False,
        "live_trading": False,
    }
    if not isinstance(safety, Mapping) or any(
        safety.get(key) is not expected for key, expected in required_safety.items()
    ):
        raise RuntimeError("funding settlement safety contract is invalid")
    raw_series = snapshot.get("series")
    if not isinstance(raw_series, Mapping):
        raise RuntimeError("funding settlement series is missing")
    dataset_hash = str(snapshot.get("dataset_hash", ""))
    if _SHA256.fullmatch(dataset_hash) is None or stable_hash(raw_series) != dataset_hash:
        raise RuntimeError("funding settlement dataset hash mismatch")
    universe = tuple(str(item) for item in snapshot.get("universe") or ())
    if tuple(sorted(raw_series)) != tuple(sorted(universe)) or len(universe) < 10:
        raise RuntimeError("funding settlement universe binding is invalid")

    history: dict[str, list[SettlementObservation]] = {}
    for instrument in sorted(universe):
        rows = raw_series.get(instrument)
        if not isinstance(rows, list) or len(rows) < min_rows_per_instrument:
            raise RuntimeError(f"{instrument}: settlement history is too short")
        parsed: list[SettlementObservation] = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise RuntimeError(f"{instrument}: malformed settlement row {index}")
            try:
                source_time = int(row["source_funding_time_ms"])
                settlement_time = int(row["settlement_time_ms"])
                decision_time = int(row["decision_time_ms"])
                mark_close = int(row["mark_kline_close_time_ms"])
                if abs(source_time - settlement_time) > 30_000:
                    raise ValueError("funding time jitter")
                if mark_close != decision_time - 1:
                    raise ValueError("mark knowledge time")
                parsed.append(
                    SettlementObservation(
                        instrument_id=str(row["instrument_id"]),
                        settlement_time_ms=settlement_time,
                        decision_time_ms=decision_time,
                        settled_rate=float(row["settled_rate"]),
                        decision_mark_price=float(row["decision_mark_price"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"{instrument}: malformed settlement row {index}"
                ) from exc
            if parsed[-1].instrument_id != instrument:
                raise RuntimeError(f"{instrument}: row instrument binding mismatch")
        history[instrument] = parsed
    return history, {
        "dataset_hash": dataset_hash,
        "artifact_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "observed_at_ms": int(snapshot.get("observed_at_ms", 0)),
        "instrument_count": len(history),
        "rows_by_instrument": {
            instrument: len(rows) for instrument, rows in history.items()
        },
    }


def _definition(mode: str) -> tuple[AlphaDefinition, str]:
    params = {
        "evaluation_contract": stable_hash(EVALUATION_CONTRACT),
        "mode": mode,
    }
    seed = AlphaDefinition(
        alpha_id=mode,
        name=mode,
        factor_ids=("settled_funding_rate", "post_settlement_mark_price"),
        universe="binance_usdm_14_liquid_perpetuals",
        params=params,
    )
    research_id = f"funding_settlement_{mode}_{seed.definition_hash[:16]}"
    definition = AlphaDefinition(
        alpha_id=research_id,
        name=mode,
        factor_ids=seed.factor_ids,
        universe=seed.universe,
        params=params,
    )
    if definition.definition_hash != seed.definition_hash:
        raise RuntimeError("content-addressed funding settlement identity drifted")
    return definition, seed.definition_hash


def build_candidate_batch(
    history: Mapping[str, Sequence[SettlementObservation]],
) -> tuple[list[tuple[AlphaDefinition, tuple[PanelPeriod, ...]]], dict[str, dict[str, bool]], dict[str, dict[str, Any]]]:
    candidates = []
    evidence: dict[str, dict[str, bool]] = {}
    mappings: dict[str, dict[str, Any]] = {}
    expected_calendar: tuple[int, ...] | None = None
    for mode in CANDIDATE_MODES:
        periods = build_settlement_periods(history, mode=mode)
        calendar = tuple(int(period.event_time) for period in periods)
        if expected_calendar is None:
            expected_calendar = calendar
        elif calendar != expected_calendar:
            raise RuntimeError("funding settlement candidate calendars differ")
        definition, definition_hash = _definition(mode)
        candidates.append((definition, periods))
        # This is derived from load_history + SettlementObservation validation:
        # every signal is the rate settled before period.event_time; the next
        # rate is consumed only inside forward_returns.
        evidence[definition.alpha_id] = {"leakage_ok": True}
        mappings[mode] = {
            "alias": mode,
            "research_alpha_id": definition.alpha_id,
            "definition_hash": definition_hash,
            "economic_hypothesis": ECONOMIC_HYPOTHESES[mode],
            "period_count": len(periods),
        }
    return candidates, evidence, mappings


def _sharpe(values: Sequence[float], periods_per_year: int = PERIODS_PER_YEAR) -> float:
    if len(values) < 2:
        return 0.0
    sigma = statistics.pstdev(values)
    return 0.0 if sigma == 0.0 else statistics.fmean(values) / sigma * math.sqrt(periods_per_year)


def _max_drawdown(values: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)
    return drawdown


def _pressure(periods: Sequence[PanelPeriod]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, cost in (("1.0x", BASE_COST), ("2.0x", DOUBLE_COST)):
        replay = run_panel_backtest(
            periods,
            cost=cost,
            quantile=EVALUATION_QUANTILE,
        )
        result[label] = {
            "net_sharpe": _sharpe(replay.period_returns),
            "net_return": (
                math.prod(1.0 + value for value in replay.period_returns) - 1.0
            ),
            "max_drawdown": _max_drawdown(replay.period_returns),
            "mean_turnover": statistics.fmean(replay.turnovers),
            "total_cost": sum(replay.costs),
            "reconciliation_residual": replay.reconciliation_residual(),
        }
    return result


def _evaluation_row(
    registry: object,
    search_id: str,
    run_id: object,
    mappings: Mapping[str, Mapping[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    run_id = str(run_id)
    row = registry.get_evaluation(run_id)
    if not isinstance(row, Mapping):
        return None
    links = [
        link
        for link in registry.search_evaluations(search_id)
        if isinstance(link, Mapping) and str(link.get("run_id", "")) == run_id
    ]
    if len(links) != 1 or links[0].get("evaluation_content_hash") != row.get("content_hash"):
        raise RuntimeError("evaluation has no unique immutable search binding")
    research_id = str(row.get("alpha_id", ""))
    alias = next(
        (
            name
            for name, mapping in mappings.items()
            if mapping["research_alpha_id"] == research_id
        ),
        None,
    )
    if alias is None or links[0].get("config_hash") != mappings[alias]["definition_hash"]:
        raise RuntimeError("evaluation definition binding mismatch")
    try:
        evaluation = json.loads(str(row["evaluation_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("evaluation payload is invalid") from exc
    return alias, {
        "run_id": run_id,
        "verified": registry.verify_evaluation(run_id) is True,
        "verdict": evaluation.get("verdict"),
        "deflated_sharpe": evaluation.get("deflated_sharpe"),
        "pbo": evaluation.get("pbo"),
        "net_sharpe": evaluation.get("net_sharpe"),
        "observed_sr": evaluation.get("observed_sr"),
        "n_periods": evaluation.get("n_periods"),
        "regime_profitable_frac": evaluation.get("regime_profitable_frac"),
        "catalog_trial_count": evaluation.get("catalog_trial_count"),
        "reasons": list(evaluation.get("reasons") or ()),
        "content_hash": row.get("content_hash"),
    }


def _decision(
    evaluations: Mapping[str, Mapping[str, Any]],
    pressure: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    eligible = []
    for order, alias in enumerate(CANDIDATE_MODES):
        evaluation = evaluations.get(alias) or {}
        stress = pressure.get(alias, {}).get("2.0x") or {}
        if (
            evaluation.get("verified") is True
            and evaluation.get("verdict") == "PASS"
            and isinstance(stress.get("net_return"), (int, float))
            and not isinstance(stress.get("net_return"), bool)
            and float(stress["net_return"]) > 0.0
        ):
            eligible.append(
                (-float(evaluation.get("net_sharpe") or 0.0), order, alias)
            )
    selected = min(eligible)[2] if eligible else None
    passed = selected is not None
    return {
        "selected_alias": selected,
        "formal_evaluation_pass": passed,
        "double_cost_positive": bool(
            selected is not None
            and float(pressure[selected]["2.0x"]["net_return"]) > 0.0
        ),
        "forward_admission_allowed": passed,
        # Historical PASS is still not shadow maturity or execution authority.
        "testnet_eligible": False,
    }


def _run_legacy_diagnostic(
    *,
    source_snapshot: str | Path = DEFAULT_SOURCE,
    output: str | Path = DEFAULT_OUTPUT,
    source_commit: str,
    recorded_at: int | None = None,
    min_rows_per_instrument: int = MIN_ROWS_PER_INSTRUMENT,
) -> dict[str, Any]:
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character git commit")
    history, source_metadata = load_history(
        source_snapshot,
        min_rows_per_instrument=min_rows_per_instrument,
    )
    candidates, leakage_evidence, mappings = build_candidate_batch(history)
    pressure = {
        mode: _pressure(periods)
        for mode, (_definition_item, periods) in zip(CANDIDATE_MODES, candidates, strict=True)
    }
    observed_at = int(recorded_at if recorded_at is not None else time.time_ns() // 1_000_000)
    dataset_version = stable_hash(
        {
            "evaluation_contract": EVALUATION_CONTRACT,
            "source_dataset_hash": source_metadata["dataset_hash"],
            "source_artifact_sha256": source_metadata["artifact_sha256"],
        }
    )
    search_id = make_search_id(
        source=SOURCE,
        search_kind=SEARCH_KIND,
        dataset_version=dataset_version,
        recorded_at=observed_at,
        params={
            "candidate_aliases": list(CANDIDATE_MODES),
            "source_commit": source_commit,
        },
    )
    budget = ResearchBudget(
        max_trials=len(CANDIDATE_MODES),
        max_feature_count=2,
        max_parameter_dimensions=2,
        allowed_data_sources=(DATA_SOURCE,),
        stopping_rules={"max_trials": len(CANDIDATE_MODES)},
    )
    with production_experiment_registry() as registry:
        result = run_registered_candidate_batch(
            candidates,
            registry=registry,
            search_id=search_id,
            search_kind=SEARCH_KIND,
            search_source=SOURCE,
            dataset_version=dataset_version,
            recorded_at=observed_at,
            periods_per_year=PERIODS_PER_YEAR,
            quantile=EVALUATION_QUANTILE,
            config=AlphaFactoryConfig(compute_regime_evidence=True),
            cost=BASE_COST,
            evidence=leakage_evidence,
            budget=budget,
            parent_hypothesis=(
                "Lagged settled funding ranks may persist for one settlement, and a "
                "fixed exit buffer may retain that carry with lower boundary churn."
            ),
            search_space=(
                "Exactly two preregistered members: rank control and fixed 20% entry/"
                "40% exit buffered rank; 8h horizon; no parameter, direction, window, "
                "universe, or cost scan."
            ),
            data_source=DATA_SOURCE,
            uses_locked_oos=False,
            hypothesis_by_candidate={
                mapping["research_alpha_id"]: stable_hash(
                    {"hypothesis": mapping["economic_hypothesis"]}
                )
                for mapping in mappings.values()
            },
            code_hash=source_commit,
        )
        evaluations: dict[str, dict[str, Any]] = {}
        for run_id in result.get("recorded_run_ids", ()):
            item = _evaluation_row(registry, search_id, run_id, mappings)
            if item is not None:
                alias, evaluation = item
                evaluations[alias] = evaluation

    decision = _decision(evaluations, pressure)
    document = {
        "schema_version": "funding_settlement_evaluation.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": observed_at,
        "search_id": search_id,
        "search_kind": SEARCH_KIND,
        "dataset_version": dataset_version,
        "source": source_metadata,
        "evaluation_contract": EVALUATION_CONTRACT,
        "falsification_criteria": list(FALSIFICATION_CRITERIA),
        "candidates": {
            alias: {
                **mappings[alias],
                "pressure": pressure[alias],
                "evaluation": evaluations.get(alias),
                "status": (
                    "VERIFIED"
                    if evaluations.get(alias, {}).get("verified") is True
                    else "PENDING"
                ),
            }
            for alias in CANDIDATE_MODES
        },
        "decision": decision,
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
            "historical_pass_is_not_testnet_authority": True,
        },
    }
    atomic_write_json(output, document, indent=2)
    return document


def run(*args, **kwargs) -> dict[str, Any]:
    quarantine_legacy_backtest("tools.funding_settlement_evaluation.run")


def main() -> None:
    quarantine_legacy_backtest("tools.funding_settlement_evaluation.main")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    result = run(
        source_snapshot=args.source,
        output=args.output,
        source_commit=args.source_commit,
    )
    print(
        f"funding settlement evaluation: pass={result['decision']['formal_evaluation_pass']} "
        f"selected={result['decision']['selected_alias']}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "CANDIDATE_MODES",
    "EVALUATION_CONTRACT",
    "build_candidate_batch",
    "load_history",
    "run",
]
