"""TDD for tools/ashare_register_alpha.py — A 股旧版证据注册的隔离边界。

钉死:注册计划转换保持确定性纯函数，但任何持久化入口都必须在读快照或开注册表前
以 INVALIDATED_PERP_LEDGER_V1 fail-closed，且不产生文件或修改已有数据库。
"""
import json

import pytest

from quantos.contracts.alpha import AlphaDefinition
from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
)
from services.research.experiment_registry.store import (
    ExperimentStoreError,
    SqliteExperimentStore,
)
from tools import ashare_register_alpha
from tools.ashare_register_alpha import (
    ALPHA_ID,
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


def test_plan_is_deterministic_and_preserves_diagnostic_history():
    p1 = build_registration_plan(_snapshot(), _pnl())
    p2 = build_registration_plan(_snapshot(), _pnl())
    assert p1 == p2, "same inputs must produce a bit-identical plan (idempotency root)"
    assert p1.alpha_id == ALPHA_ID
    assert p1.state == "DRAFT"
    assert p1.sharpe == 1.2285  # legacy diagnostic input; persistence is quarantined below
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


def test_apply_quarantines_before_creating_missing_registry(tmp_path):
    plan = build_registration_plan(_snapshot(), _pnl())
    db = tmp_path / "does-not-exist.db"

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        ashare_register_alpha.apply_registration(plan, str(db), recorded_at=1_000)

    assert not db.exists(), "quarantine must run before SQLite can create the path"


def test_apply_quarantines_before_reading_or_modifying_existing_registry(
    tmp_path, monkeypatch
):
    db = tmp_path / "reg.db"
    SqliteExperimentStore(str(db), create=True).close()
    plan = build_registration_plan(_snapshot(), _pnl())
    before = db.read_bytes()

    def forbidden_store(*_args, **_kwargs):
        pytest.fail("registry opened before authority quarantine")

    monkeypatch.setattr(ashare_register_alpha, "SqliteExperimentStore", forbidden_store)

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        ashare_register_alpha.apply_registration(plan, str(db), recorded_at=1_000)

    assert db.read_bytes() == before, "existing registry must remain byte-for-byte unchanged"


def test_main_quarantines_before_snapshot_dry_run_or_registry_access(monkeypatch):
    touched = []

    def forbidden(label):
        def _forbidden(*_args, **_kwargs):
            touched.append(label)
            pytest.fail(f"{label} accessed before authority quarantine")

        return _forbidden

    monkeypatch.setattr(ashare_register_alpha, "open", forbidden("snapshot"), raising=False)
    monkeypatch.setattr(
        ashare_register_alpha,
        "_load_dry_run_pnl",
        forbidden("dry-run store"),
    )
    monkeypatch.setattr(
        ashare_register_alpha,
        "apply_registration",
        forbidden("production registry"),
    )

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        ashare_register_alpha.main()

    assert touched == []
