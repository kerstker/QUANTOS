"""TDD for tools/ashare_register_alpha.py — A 股微盘复合 alpha 的注册表身份注册。

钉死:DRAFT 初始态(绝不 APPROVED)、REQUIRES_HUMAN_REVIEW 诚实 verdict、确定性幂等
(重跑无重复行、不推进/回退已有生命周期)、definition_json 可被 AlphaDefinition 校验。
"""
import json

import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState
from services.research.experiment_registry.store import (
    ExperimentStoreError,
    SqliteExperimentStore,
)
from tools.ashare_register_alpha import (
    ALPHA_ID,
    apply_registration,
    build_registration_plan,
)


def _snapshot():
    return {
        "strategy_id": "ashare_microcap_composite_v3_u249",
        "universe": {"file_count": 249, "in_db": 249, "used": 249},
        "panel": {"n_dates": 1345, "n_stocks": 249, "range": [20210104, 20260723]},
        "split": {"train_end": 20241231, "test_start": 20250101},
        "combo": {
            "champion": "icir",
            "weights": {"reversal_5d": 0.1178, "turnover_20d": -0.1792,
                        "amihud_illiq_20d": 0.2},
            "ic": {"h1": {"test": {"mean_ic": 0.0949, "ic_std": 0.144, "icir": 0.6587,
                                   "hit_rate": 0.7413, "t_stat": 12.7552, "n_periods": 375}}},
        },
        "turnover_cap_sensitivity": {
            "train_champion_cap": 0.3,
            "grid": {"0.3": {"test_avg_turnover": 0.6054}},
        },
        "multiple_testing": {"n_trials": 20},
    }


def _pnl():
    return {"total_return": 0.405078, "sharpe": 1.2285, "max_drawdown": 0.1692,
            "residual": 1e-09}


def _apply(plan, db_path):
    return apply_registration(plan, str(db_path), recorded_at=1_000)


def test_plan_is_deterministic_and_carries_real_evidence():
    p1 = build_registration_plan(_snapshot(), _pnl())
    p2 = build_registration_plan(_snapshot(), _pnl())
    assert p1 == p2, "same inputs must produce a bit-identical plan (idempotency root)"
    assert p1.alpha_id == ALPHA_ID
    assert p1.state == "DRAFT"
    assert p1.sharpe == 1.2285                      # dry_run.db run 11dae0671c13e2e2
    ev = p1.evaluation
    assert ev.ic_mean == 0.0949 and ev.ic_tstat == 12.7552   # combo test h1
    assert ev.verdict.value == "REQUIRES_HUMAN_REVIEW"
    assert ev.reproducibility_hash
    assert ev.catalog_trial_count == 20
    assert any("未过 evaluator 门禁" in r for r in ev.reasons)


def test_plan_definition_is_a_valid_alpha_definition_with_composite_spec():
    plan = build_registration_plan(_snapshot(), _pnl())
    defn = plan.definition
    assert isinstance(defn, AlphaDefinition)
    assert defn.factor_ids == tuple(sorted(plan.definition.factor_ids))
    weights = json.loads(defn.params["weights_json"])
    assert weights == _snapshot()["combo"]["weights"]
    assert defn.params["turnover_rule"] == "cap0.3:max_oneway=0.3"
    assert defn.params["universe_file"].endswith("universe.json")
    assert defn.universe == "ashare_microcap_u249"


def test_plan_fails_closed_on_missing_evidence():
    with pytest.raises(ExperimentStoreError):
        build_registration_plan({"combo": {}}, _pnl())
    with pytest.raises(ExperimentStoreError):
        build_registration_plan(_snapshot(), {"total_return": 0.4})   # no sharpe


def test_apply_writes_draft_alpha_and_evaluation_and_is_idempotent(tmp_path):
    db = tmp_path / "reg.db"
    SqliteExperimentStore(str(db), create=True).close()
    plan = build_registration_plan(_snapshot(), _pnl())

    run_id_1 = _apply(plan, db)
    run_id_2 = _apply(plan, db)
    assert run_id_1 == run_id_2

    store = SqliteExperimentStore(str(db), create=False)
    try:
        alphas = [a for a in store.all_alphas() if a["alpha_id"] == ALPHA_ID]
        assert len(alphas) == 1, "re-run must not duplicate the alpha row"
        row = alphas[0]
        assert row["state"] == "DRAFT"
        assert row["sharpe"] == 1.2285
        # definition_json must round-trip through the canonical contract (load_into path)
        parsed = AlphaDefinition.model_validate(json.loads(row["definition_json"]))
        assert parsed.alpha_id == ALPHA_ID
        evals = store.list_evaluations(ALPHA_ID)
        assert len(evals) == 1, "re-run must not duplicate the evaluation artifact"
        assert evals[0]["verdict"] == "REQUIRES_HUMAN_REVIEW"
        ej = json.loads(evals[0]["evaluation_json"])
        assert ej["ic_mean"] == 0.0949 and ej["net_sharpe"] == 1.2285
    finally:
        store.close()


def test_apply_never_writes_approved_and_preserves_advanced_state(tmp_path):
    db = tmp_path / "reg.db"
    SqliteExperimentStore(str(db), create=True).close()
    plan = build_registration_plan(_snapshot(), _pnl())
    _apply(plan, db)

    # simulate a human-advanced lifecycle state; a re-run must NOT regress it
    store = SqliteExperimentStore(str(db), create=False)
    try:
        store.upsert_alpha(
            ALPHA_ID,
            json.dumps(plan.definition.model_dump(), sort_keys=True, default=str),
            AlphaState.TESTING.value, "[]", plan.sharpe, None,
        )
    finally:
        store.close()
    _apply(plan, db)
    store = SqliteExperimentStore(str(db), create=False)
    try:
        row = [a for a in store.all_alphas() if a["alpha_id"] == ALPHA_ID][0]
        assert row["state"] == "TESTING", "re-run must preserve the advanced state"
    finally:
        store.close()

    # a plan that somehow carries APPROVED must be structurally refused
    bad = build_registration_plan(_snapshot(), _pnl())
    object.__setattr__(bad, "state", AlphaState.APPROVED.value)
    db2 = tmp_path / "reg2.db"
    SqliteExperimentStore(str(db2), create=True).close()
    with pytest.raises(ExperimentStoreError):
        _apply(bad, db2)


def test_apply_fails_closed_when_registry_missing(tmp_path):
    plan = build_registration_plan(_snapshot(), _pnl())
    with pytest.raises(Exception):
        _apply(plan, tmp_path / "does-not-exist.db")
