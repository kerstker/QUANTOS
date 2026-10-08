"""Pre-registered formal evaluation of one PIT VIX carry/crowd switch.

The rule is fixed before this driver observes any result.  At each weekly
decision, use the VIX observation available at the preceding weekly decision:
VIX >= 20 selects the canonical crowd-contrarian sleeve; otherwise select the
canonical funding-carry sleeve.  Twenty is a fixed market convention, not a
threshold estimated from these returns.  The complete CSCV population contains
the two canonical controls and the single regime switch; there is no threshold,
lag, sign, weight, horizon, or quantile scan.

All three rows use executable perpetual-futures payoff (price plus every daily
funding cash flow in the seven-day hold) and the production Paper quantile 30%.
The VIX series is read PIT from the local FRED market-series authority.  This is
L2 research only and never admits, promotes, reads credentials, connects to a
broker, creates an intent, or submits an order.  A historical PASS remains
insufficient for Testnet eligibility.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.alpha.config import AlphaFactoryConfig
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.data.fundamental_store import SqliteFundamentalStore
from services.factor_garden.canonical import (
    CARRY_COMPOSITE_EXPRESSION,
    CROWD_CONTRA_EXPRESSION,
)
from services.intelligence.registered_candidate_batch import (
    run_registered_candidate_batch,
)
from services.research import alpha_expression as ax
from services.research.carry_pnl import funding_adjusted_periods
from services.research.expression_leakage import compute_leakage_ok
from services.research.macro_regime import pit_series
from services.research.research_budget import ResearchBudget
from tools.cache_io import atomic_write_json
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)


DEFAULT_OUTPUT = Path("data/cache/macro_regime_switch_evaluation.json")
DEFAULT_MACRO_DB = Path("data/cache/macro_fred.db")
FWD_HORIZON = 7
REGIME_LAG_ROWS = FWD_HORIZON
PERIODS_PER_YEAR = 52
EVALUATION_QUANTILE = 0.3
VIX_STRESS_THRESHOLD = 20.0
DATA_SOURCE = "crypto_oi_pit_panel+fred_vix_market_pit"
SOURCE = "tools.macro_regime_switch_evaluation"
SEARCH_KIND = "PREREGISTERED_PIT_VIX_CARRY_CROWD_SWITCH_V1"
_COMMIT = re.compile(r"[0-9a-f]{40}")

CARRY_EXPRESSION = CARRY_COMPOSITE_EXPRESSION
CROWD_EXPRESSION = CROWD_CONTRA_EXPRESSION
CANDIDATE_ALIASES = (
    "carry_control",
    "crowd_control",
    "vix_regime_switch",
)

ECONOMIC_HYPOTHESES = {
    "carry_control": (
        "Control: cross-sectional perpetual funding is a carry premium after "
        "complete funding accrual and realistic costs."
    ),
    "crowd_control": (
        "Control: broad-account positioning crowding should mean-revert "
        "cross-sectionally after complete perpetual payoff and costs."
    ),
    "vix_regime_switch": (
        "Funding carry is vulnerable when market volatility is elevated, while "
        "crowding unwind is designed for stressed positioning.  A lagged fixed-VIX "
        "switch should retain carry in calm markets and crowd reversal in stress."
    ),
}

FALSIFICATION_CRITERIA = (
    "The VIX switch fails the immutable catalog-wide DSR threshold.",
    "The complete three-member CSCV selection population exceeds the PBO ceiling.",
    "The switch fails leakage, regime, or sequential walk-forward evidence.",
    "The switch fails formal evaluation even when either control is positive.",
)

EVALUATION_CONTRACT = {
    "driver": "macro_regime_switch_evaluation.v1",
    "fwd_horizon": FWD_HORIZON,
    "non_overlapping": True,
    "periods_per_year": PERIODS_PER_YEAR,
    "quantile": EVALUATION_QUANTILE,
    "return_basis": "PRICE_PLUS_FUNDING",
    "funding_accrual": "complete_rows_after_decision_through_horizon.v1",
    "vix_authority": "macro_fred.db:VIXCLS:MACRO:latest_as_of",
    "vix_stress_rule": "VIXCLS>=20.0",
    "regime_lag_rows": REGIME_LAG_ROWS,
    "decision_calendar": "panel_rows_start_7_step_7.v1",
    "selection_population": list(CANDIDATE_ALIASES),
    "candidate_selection": "vix_regime_switch_only.v1",
    "carry_expression": ax.serialize(CARRY_EXPRESSION),
    "crowd_expression": ax.serialize(CROWD_EXPRESSION),
}


def select_regime_signal(
    carry_signal: Sequence[Sequence[Any]],
    crowd_signal: Sequence[Sequence[Any]],
    vix_values: Sequence[float | None],
    *,
    threshold: float = VIX_STRESS_THRESHOLD,
    lag_rows: int = REGIME_LAG_ROWS,
) -> list[list[Any]]:
    """Select a whole cross-section using only the lagged VIX observation."""
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise TypeError("threshold must be a finite number")
    if not math.isfinite(float(threshold)):
        raise ValueError("threshold must be finite")
    if isinstance(lag_rows, bool) or not isinstance(lag_rows, int):
        raise TypeError("lag_rows must be an integer")
    if lag_rows < 1:
        raise ValueError("lag_rows must be positive")
    if len(carry_signal) != len(crowd_signal) or len(carry_signal) != len(vix_values):
        raise ValueError("carry, crowd, and VIX rows must align exactly")
    if not carry_signal:
        return []

    width = len(carry_signal[0])
    selected: list[list[Any]] = []
    for row in range(len(carry_signal)):
        if len(carry_signal[row]) != width or len(crowd_signal[row]) != width:
            raise ValueError("carry and crowd cross-sections must have stable width")
        source_row: Sequence[Any] | None = None
        if row >= lag_rows:
            lagged_vix = vix_values[row - lag_rows]
            if (
                isinstance(lagged_vix, (int, float))
                and not isinstance(lagged_vix, bool)
                and math.isfinite(float(lagged_vix))
            ):
                source_row = (
                    crowd_signal[row]
                    if float(lagged_vix) >= float(threshold)
                    else carry_signal[row]
                )
        selected.append([None] * width if source_row is None else list(source_row))
    return selected


def _selector_leakage_ok(
    carry_signal: Sequence[Sequence[Any]],
    crowd_signal: Sequence[Sequence[Any]],
    vix_values: Sequence[float | None],
) -> bool:
    base = _mask_to_decision_calendar(
        select_regime_signal(carry_signal, crowd_signal, vix_values)
    )
    if not base:
        return False
    width = len(base[0])
    extra = REGIME_LAG_ROWS + 2
    carry_extended = [*carry_signal, *([[999.0] * width for _ in range(extra)])]
    crowd_extended = [*crowd_signal, *([[-999.0] * width for _ in range(extra)])]
    vix_extended = [*vix_values, *([10_000.0] * extra)]
    extended = _mask_to_decision_calendar(
        select_regime_signal(carry_extended, crowd_extended, vix_extended)
    )
    return extended[: len(base)] == base


def _mask_to_decision_calendar(signal: Sequence[Sequence[Any]]) -> list[list[Any]]:
    if not signal:
        return []
    width = len(signal[0])
    out = [[None] * width for _ in signal]
    for row in range(REGIME_LAG_ROWS, len(signal), FWD_HORIZON):
        if len(signal[row]) != width:
            raise ValueError("signal cross-sections must have stable width")
        out[row] = list(signal[row])
    return out


def _ymd(event_time_ms: int) -> int:
    return int(time.strftime("%Y%m%d", time.gmtime(event_time_ms / 1000)))


def load_vix_values(panel, *, macro_db: str | Path = DEFAULT_MACRO_DB) -> list:
    path = Path(macro_db)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"VIX authority is missing: {path}")
    store = SqliteFundamentalStore(str(path), create=False)
    as_ofs = [_ymd(int(event_time)) for event_time in panel.event_times]
    rows = store.as_of("VIXCLS", "MACRO", max(as_ofs, default=0))
    values = pit_series(
        [(item.event_time, item.knowledge_time, item.value) for item in rows],
        as_ofs,
    )
    normalized = []
    for value in values:
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise RuntimeError("VIX authority contains a non-finite observation")
        normalized.append(None if value is None else float(value))
    if sum(value is not None for value in normalized) < 52:
        raise RuntimeError("VIX authority has fewer than 52 aligned observations")
    return normalized


def _definition(
    alias: str,
    *,
    factor_ids: tuple[str, ...],
    mechanism: Mapping[str, Any],
) -> tuple[AlphaDefinition, str]:
    params = {
        "evaluation_contract": stable_hash(EVALUATION_CONTRACT),
        "mechanism": stable_hash(mechanism),
    }
    seed = AlphaDefinition(
        alpha_id=alias,
        name=alias,
        factor_ids=factor_ids,
        universe="crypto_oi_majors_pit",
        params=params,
    )
    research_id = f"macro_regime_{alias}_{seed.definition_hash[:16]}"
    definition = AlphaDefinition(
        alpha_id=research_id,
        name=alias,
        factor_ids=seed.factor_ids,
        universe=seed.universe,
        params=params,
    )
    if definition.definition_hash != seed.definition_hash:
        raise RuntimeError("content-addressed macro-regime identity drifted")
    return definition, seed.definition_hash


def build_candidate_batch(
    panel,
    vix_values: Sequence[float | None],
) -> tuple[list, dict[str, dict], dict[str, dict]]:
    if len(vix_values) != len(panel.event_times):
        raise ValueError("VIX observations must align to every panel event time")
    carry_signal = ax.evaluate(CARRY_EXPRESSION, panel)
    crowd_signal = ax.evaluate(CROWD_EXPRESSION, panel)
    switch_signal = _mask_to_decision_calendar(
        select_regime_signal(carry_signal, crowd_signal, vix_values)
    )
    carry_signal = _mask_to_decision_calendar(carry_signal)
    crowd_signal = _mask_to_decision_calendar(crowd_signal)
    specs = {
        "carry_control": {
            "signal": carry_signal,
            "factor_ids": ("funding",),
            "mechanism": {"expression": ax.serialize(CARRY_EXPRESSION)},
            "leakage_ok": compute_leakage_ok(CARRY_EXPRESSION, panel),
        },
        "crowd_control": {
            "signal": crowd_signal,
            "factor_ids": ("global_ls",),
            "mechanism": {"expression": ax.serialize(CROWD_EXPRESSION)},
            "leakage_ok": compute_leakage_ok(CROWD_EXPRESSION, panel),
        },
        "vix_regime_switch": {
            "signal": switch_signal,
            "factor_ids": ("VIXCLS", "funding", "global_ls"),
            "mechanism": {
                "carry_expression": ax.serialize(CARRY_EXPRESSION),
                "crowd_expression": ax.serialize(CROWD_EXPRESSION),
                "rule": EVALUATION_CONTRACT["vix_stress_rule"],
                "lag_rows": REGIME_LAG_ROWS,
            },
            "leakage_ok": (
                compute_leakage_ok(CARRY_EXPRESSION, panel)
                and compute_leakage_ok(CROWD_EXPRESSION, panel)
                and _selector_leakage_ok(carry_signal, crowd_signal, vix_values)
            ),
        },
    }

    candidates = []
    evidence: dict[str, dict[str, bool | None]] = {}
    mappings: dict[str, dict[str, Any]] = {}
    for alias in CANDIDATE_ALIASES:
        spec = specs[alias]
        definition, definition_hash = _definition(
            alias,
            factor_ids=spec["factor_ids"],
            mechanism=spec["mechanism"],
        )
        periods = funding_adjusted_periods(
            spec["signal"],
            panel,
            fwd_horizon=FWD_HORIZON,
            non_overlapping=False,
        )
        candidates.append((definition, periods))
        evidence[definition.alpha_id] = {"leakage_ok": spec["leakage_ok"]}
        mappings[alias] = {
            "alias": alias,
            "research_alpha_id": definition.alpha_id,
            "definition_hash": definition_hash,
            "factor_ids": list(definition.factor_ids),
            "mechanism": spec["mechanism"],
            "economic_hypothesis": ECONOMIC_HYPOTHESES[alias],
            "quantile": EVALUATION_QUANTILE,
            "return_basis": "PRICE_PLUS_FUNDING",
        }
    return candidates, evidence, mappings


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
    if len(links) != 1:
        raise RuntimeError("evaluation has no unique immutable search binding")
    link = links[0]
    if link.get("evaluation_content_hash") != row.get("content_hash"):
        raise RuntimeError("evaluation content binding mismatch")
    research_id = str(row.get("alpha_id", ""))
    alias = next(
        (
            name
            for name, mapping in mappings.items()
            if mapping["research_alpha_id"] == research_id
        ),
        None,
    )
    if alias is None:
        raise RuntimeError("evaluation is not bound to the registered regime family")
    mapping = mappings[alias]
    if link.get("config_hash") != mapping["definition_hash"]:
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
        "capacity_adj_sharpe": evaluation.get("capacity_adj_sharpe"),
        "observed_sr": evaluation.get("observed_sr"),
        "n_periods": evaluation.get("n_periods"),
        "regime_profitable_frac": evaluation.get("regime_profitable_frac"),
        "reasons": list(evaluation.get("reasons") or ()),
        "catalog_trial_count": evaluation.get("catalog_trial_count"),
        "as_of": evaluation.get("as_of"),
        "dataset_version": row.get("dataset_version"),
        "content_hash": row.get("content_hash"),
    }


def _decision(evaluation: Mapping[str, Any] | None) -> dict[str, bool]:
    verified = isinstance(evaluation, Mapping) and evaluation.get("verified") is True
    verdict = evaluation.get("verdict") if verified else None
    formal_pass = verdict == "PASS"
    return {
        "formal_evaluation_pass": formal_pass,
        "human_review_required": verdict == "REQUIRES_HUMAN_REVIEW",
        "forward_admission_allowed": formal_pass,
        "testnet_eligible": False,
    }


def _run_legacy_diagnostic(
    *,
    output: str | Path = DEFAULT_OUTPUT,
    recorded_at: int | None = None,
    source_commit: str,
    panel=None,
    vix_values: Sequence[float | None] | None = None,
    macro_db: str | Path = DEFAULT_MACRO_DB,
) -> dict[str, Any]:
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character git commit")
    if panel is None:
        from tools.crypto_oi_research import build_panel

        panel, _instruments = build_panel()
    if vix_values is None:
        vix_values = load_vix_values(panel, macro_db=macro_db)
    vix_values = list(vix_values)
    candidates, leakage_evidence, mappings = build_candidate_batch(panel, vix_values)
    observed_at = int(
        recorded_at if recorded_at is not None else time.time_ns() // 1_000_000
    )
    dataset_version = field_panel_dataset_version(
        panel,
        source_versions={
            **EVALUATION_CONTRACT,
            "vix_series_hash": stable_hash(vix_values),
        },
    )
    search_id = make_search_id(
        source=SOURCE,
        search_kind=SEARCH_KIND,
        dataset_version=dataset_version,
        recorded_at=observed_at,
        params={
            "candidate_aliases": list(CANDIDATE_ALIASES),
            "source_commit": source_commit,
        },
    )
    budget = ResearchBudget(
        max_trials=len(CANDIDATE_ALIASES),
        max_feature_count=3,
        max_parameter_dimensions=2,
        allowed_data_sources=(DATA_SOURCE,),
        stopping_rules={"max_trials": len(CANDIDATE_ALIASES)},
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
            evidence=leakage_evidence,
            budget=budget,
            parent_hypothesis=(
                "A lagged fixed-VIX regime switch may combine calm-market funding "
                "carry with stress-market positioning unwind."
            ),
            search_space=(
                "Exactly the canonical carry control, canonical crowd control, and "
                "one VIX>=20 switch using a seven-row lag at quantile 0.30; no scan."
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

    rows = {
        alias: {
            **mapping,
            "evaluation": evaluations.get(alias),
            "status": (
                "VERIFIED"
                if evaluations.get(alias, {}).get("verified") is True
                else "PENDING"
            ),
        }
        for alias, mapping in mappings.items()
    }
    complete = all(row["status"] == "VERIFIED" for row in rows.values())
    switch_evaluation = evaluations.get("vix_regime_switch")
    vix_coverage = sum(value is not None for value in vix_values)
    document = {
        "schema_version": "macro_regime_switch_evaluation.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": observed_at,
        "source_commit": source_commit,
        "dataset_version": dataset_version,
        "search_id": search_id,
        "status": "VERIFIED" if complete else "PENDING",
        "evaluation_contract": EVALUATION_CONTRACT,
        "vix_coverage": {
            "available_rows": vix_coverage,
            "total_rows": len(vix_values),
        },
        "research_budget": budget.metadata(),
        "falsification_criteria": list(FALSIFICATION_CRITERIA),
        "family": rows,
        "decision": _decision(switch_evaluation),
        "safety": {
            "research_only": True,
            "forward_admission_performed": False,
            "testnet_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }
    atomic_write_json(str(output), document, indent=2)
    return document


def run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    quarantine_legacy_backtest("tools.macro_regime_switch_evaluation.run")


def main(argv: Sequence[str] | None = None) -> int:
    quarantine_legacy_backtest("tools.macro_regime_switch_evaluation.main")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--macro-db", default=str(DEFAULT_MACRO_DB))
    args = parser.parse_args(argv)
    try:
        document = run(
            output=args.output,
            source_commit=args.source_commit,
            macro_db=args.macro_db,
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        print(f"macro regime switch evaluation blocked: {type(exc).__name__}: {exc}")
        return 2
    evaluation = document["family"]["vix_regime_switch"]["evaluation"] or {}
    print(
        "macro regime switch evaluation: "
        f"status={document['status']} verdict={evaluation.get('verdict')} "
        f"dsr={evaluation.get('deflated_sharpe')} pbo={evaluation.get('pbo')}"
    )
    return 0 if document["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CARRY_EXPRESSION",
    "CROWD_EXPRESSION",
    "DATA_SOURCE",
    "DEFAULT_OUTPUT",
    "EVALUATION_CONTRACT",
    "EVALUATION_QUANTILE",
    "REGIME_LAG_ROWS",
    "VIX_STRESS_THRESHOLD",
    "build_candidate_batch",
    "load_vix_values",
    "run",
    "select_regime_signal",
]
