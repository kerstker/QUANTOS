"""可执行的零 monkeypatch 生产验证 DAG 贯通检查。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from quantos.contracts.cost import CostModel
from quantos.contracts.data import Bar
from quantos.contracts.feature import FeatureValue
from services.alpha.family_testing import FamilyAssignment
from services.research.trial_ledger import TrialLedger, TrialRecord
from services.research.validation_dag import (
    ValidationPolicy,
    _canonical,
    _digest,
    register_leakage_compute,
    run_validation_dag,
)


@dataclass(frozen=True)
class _Candidate:
    alpha_id: str
    family_id: str
    exact_definition_id: str
    validation_policy_digest: str


def _past_close(rows: Sequence[Bar]) -> list[FeatureValue]:
    """只使用每根 bar 自身收盘价；未来扰动不应改变目标时点。"""
    return [
        FeatureValue(
            feature_id="past_close",
            instrument_id=row.instrument_id,
            event_time=row.event_time,
            knowledge_time=row.knowledge_time,
            value=row.close,
        )
        for row in rows
    ]




def _valid_panel() -> tuple[dict[str, Any], str]:
    candidate_ids = ("alpha-0", "alpha-1", "alpha-2")
    definitions = tuple(
        {"candidate_id": candidate_id, "definition": {"name": candidate_id, "version": 1}}
        for candidate_id in candidate_ids
    )
    definition_hashes = tuple(
        _digest({"canonical_definition": _canonical(item["definition"])})
        for item in definitions
    )
    registered_compute = register_leakage_compute(
        "tools.validation_dag_production_check.past_close:1",
        _past_close,
        exact_definition_id=definition_hashes[0],
    )
    returns = tuple(
        (-(0.010 + 0.001 * (i % 11)) if i % 7 == 0 else 0.004 + (i % 5) * 0.001)
        for i in range(80)
    )
    benchmark = tuple(
        (-0.025 if i % 19 == 0 else -0.010 if i % 8 == 0 else 0.003 + (i % 4) * 0.001)
        for i in range(80)
    )
    market_closes = [100.0]
    for i in range(1, 80):
        step = 1.0 + (0.002 if i < 40 else (0.012 if i % 2 else -0.009))
        market_closes.append(market_closes[-1] * step)
    gross_returns = tuple(0.003 + (0.002 if i % 3 else -0.001) for i in range(60))
    turnover = tuple((((0.20, 100_000.0),)) for _ in gross_returns)
    bars = tuple(
        Bar(
            instrument_id="BTC-USD",
            event_time=i,
            knowledge_time=i,
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1_000.0 + i,
        )
        for i in range(6)
    )
    panel = {
        "cheap": {
            "feature_id": "past_close",
            "compute": registered_compute,
            "bars": bars,
            "target_event_time": 3,
            "instrument_id": "BTC-USD",
        },
        "oos": {
            "n": 80,
            "train_size": 30,
            "test_size": 10,
            "step": 10,
            "k": 4,
            "embargo": 2,
            "returns": returns,
            "return_series_by_definition": {
                definition_hashes[0]: returns,
                definition_hashes[1]: tuple(value * 0.5 + (0.001 if index % 2 else -0.001) for index, value in enumerate(returns)),
                definition_hashes[2]: tuple(-value + (0.002 if index % 3 else -0.002) for index, value in enumerate(returns)),
            },
            "observed_sr": 2.0,
            "n_obs": len(returns),
            "n_trials": 1,
            "var_sr": 0.04,
            "candidate_ids": candidate_ids,
            "candidate_id": candidate_ids[0],
            "candidate_index": 0,
            "families": FamilyAssignment(
                {"alpha-0": "family-a", "alpha-1": "family-a", "alpha-2": "family-b"},
                {"alpha-0": "DECLARED", "alpha-1": "DECLARED", "alpha-2": "DECLARED"},
            ),
            "candidate_family": "family-a",
            "pbo_return_series_by_definition": {
                definition_hash: tuple((event_time, float(value)) for event_time, value in enumerate(series))
                for definition_hash, series in {
                    definition_hashes[0]: returns,
                    definition_hashes[1]: tuple(value * 0.5 + (0.001 if index % 2 else -0.001) for index, value in enumerate(returns)),
                    definition_hashes[2]: tuple(-value + (0.002 if index % 3 else -0.002) for index, value in enumerate(returns)),
                }.items()
            },
            "pbo_candidate_definition_hashes": definition_hashes,
            "pbo_candidate_definitions": definitions,
        },
        "realism": {
            "gross_returns": gross_returns,
            "turnover_by_period": turnover,
            "aum_grid": (10_000.0, 1_000_000.0, 100_000_000.0, 10_000_000_000.0, 1_000_000_000_000.0),
            "impact_coef_lo_hi": (0.05, 0.20),
            "returns": returns,
            "market_closes": tuple(market_closes),
            "benchmark_returns": benchmark,
            "tail_q": 0.25,
            "cost_model": CostModel(fee_bps=1.0, slippage_bps=2.0, impact_coef=5.0),
            "trade_notional": 50_000.0,
            "trade_shares": 5.0,
            "adv_shares": 1_000.0,
        },
    }
    return panel, definition_hashes[0]


def _run_case(root: Path, name: str, panel: Mapping[str, Any], exact_definition_id: str) -> dict[str, Any]:
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)
    with TrialLedger(str(root / f"{name}.db")) as ledger:
        peer_definitions = tuple(panel.get("oos", {}).get("pbo_candidate_definition_hashes", ()))[1:]
        for index, definition_hash in enumerate(peer_definitions, start=1):
            peer_trial_id = _digest({"case": name, "peer": index})
            peer_alpha_id = _digest({"case": name, "peer_alpha": index})
            ledger.append(TrialRecord(peer_trial_id, peer_alpha_id, candidate.family_id, definition_hash, policy.digest, "PLANNED", 0, 0))
            ledger.append(TrialRecord(peer_trial_id, peer_alpha_id, candidate.family_id, definition_hash, policy.digest, "RUNNING", 1, 0))
        artifact = run_validation_dag(
            candidate,
            panel,
            policy,
            ledger,
            event_at=1,
            lease_now=1,
            production=True,
        )
        receipts = ledger.validation_receipt(artifact.trial_id)
    blocked_stage = next(
        (stage for stage in ("cheap", "oos", "realism")
         if receipts[stage]["payload"]["outcome"] == "BLOCK"
         and not receipts[stage]["payload"]["evidence"].get("short_circuit")),
        None,
    )
    return {
        "verdict": artifact.verdict,
        "reason": artifact.reason,
        "blocked_stage": blocked_stage,
        "stages": list(artifact.stage_metadata),
        "trial_id": artifact.trial_id,
        "artifact_commitment": artifact.artifact_commitment,
    }


def run_production_validation_check(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    panel, exact_definition_id = _valid_panel()

    malformed_oos = {**panel, "oos": {**panel["oos"], "returns": panel["oos"]["returns"][:-1]}}
    n_mismatch = {**panel, "oos": {**panel["oos"], "n": len(panel["oos"]["returns"]) - 1}}
    ordered_decay_returns = tuple(sorted(panel["oos"]["returns"], reverse=True))
    ordered_decay_population = {
        definition_hash: tuple(sorted(values, reverse=True))
        for definition_hash, values in panel["oos"]["return_series_by_definition"].items()
    }
    ordered_decay = {
        **panel,
        "oos": {
            **panel["oos"],
            "returns": ordered_decay_returns,
            "return_series_by_definition": ordered_decay_population,
            "pbo_return_series_by_definition": {
                definition_hash: tuple((event_time, float(value)) for event_time, value in enumerate(series))
                for definition_hash, series in ordered_decay_population.items()
            },
        },
    }
    malformed_realism = {**panel, "realism": {**panel["realism"], "cost_model": {"fee_bps": 1.0}}}
    subject_hash=panel["oos"]["pbo_candidate_definition_hashes"][0]
    negative_returns=tuple(-abs(value) for value in panel["oos"]["returns"])
    negative_population=dict(panel["oos"]["return_series_by_definition"])
    negative_population[subject_hash]=negative_returns
    self_reported_positive={**panel,"oos":{**panel["oos"],"returns":negative_returns,"return_series_by_definition":negative_population,"observed_sr":99.0}}
    return {
        "pass": _run_case(root, "pass", panel, exact_definition_id),
        "self_reported_positive": _run_case(root, "self-reported-positive", self_reported_positive, exact_definition_id),
        "missing": _run_case(root, "missing", {}, exact_definition_id),
        "malformed": _run_case(root, "malformed-oos", malformed_oos, exact_definition_id),
        "n_mismatch": _run_case(root, "n-mismatch", n_mismatch, exact_definition_id),
        "ordered_decay": _run_case(root, "ordered-decay", ordered_decay, exact_definition_id),
        "malformed_realism": _run_case(root, "malformed-realism", malformed_realism, exact_definition_id),
    }


if __name__ == "__main__":
    import json
    import tempfile

    with tempfile.TemporaryDirectory(prefix="quantos-validation-dag-") as directory:
        print(json.dumps(run_production_validation_check(directory), indent=2, sort_keys=True))
