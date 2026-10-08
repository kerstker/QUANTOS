"""A 股微盘复合 alpha 的旧版注册计划转换器。

``DRY_RUN_ID`` 来自已经失效的 V1 永续账本，因此只能作为
``INVALIDATED_PERP_LEDGER_V1`` 诊断历史。``build_registration_plan`` 保留为无 IO 的
确定性转换函数，便于审计旧制品；``apply_registration`` 与 ``main`` 则在读取快照或
打开任何注册表之前无条件隔离，禁止把旧 Sharpe/P&L 重新写入生产证据面。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Mapping, Optional

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import (
    ExperimentStoreError,
    SqliteExperimentStore,
)
from tools.search_trial_registry import PRODUCTION_EXPERIMENT_DB

ALPHA_ID = "ashare_microcap_composite_v3_u249_cap30"
DRY_RUN_ID = "11dae0671c13e2e2"   # ashare_microcap_composite_v3_u249_cap30 冠军干跑
_ALPHA_SNAPSHOT = os.path.join("data", "stocks", "snapshots", "alpha_snapshot.json")
_DRY_RUN_DB = os.path.join("data", "cache", "dry_run.db")
_UNIVERSE_FILE = os.path.join("data", "stocks", "universe.json")

_INITIAL_STATE = AlphaState.DRAFT.value   # 注册表研究初始态(catalog.register 入场态)


@dataclass(frozen=True)
class RegistrationPlan:
    """一次注册的纯值计划:definition + 初始状态 + 不可变评估 + 数据集版本。

    全部由 (snapshot, dry_run_pnl) 确定性导出 —— 同样输入逐位相同,这是幂等性的根。"""

    alpha_id: str
    definition: AlphaDefinition
    state: str
    sharpe: float
    evaluation: AlphaEvaluation
    dataset_version: str


def _require(mapping: Mapping, *path: str):
    cur = mapping
    for key in path:
        if not isinstance(cur, Mapping) or key not in cur:
            raise ExperimentStoreError(f"alpha snapshot missing required field: {'.'.join(path)}")
        cur = cur[key]
    return cur


def build_registration_plan(
    snapshot: Mapping,
    dry_run_pnl: Mapping,
    *,
    dry_run_run_id: str = DRY_RUN_ID,
) -> RegistrationPlan:
    """从 alpha_snapshot + 干跑 pnl 构造注册计划(纯函数,无 IO/墙钟/RNG)。"""
    combo = _require(snapshot, "combo")
    weights = _require(snapshot, "combo", "weights")
    if not isinstance(weights, Mapping) or not weights:
        raise ExperimentStoreError("combo.weights must be a non-empty mapping")
    h1 = _require(snapshot, "combo", "ic", "h1", "test")
    champion_cap = _require(snapshot, "turnover_cap_sensitivity", "train_champion_cap")
    cap_grid = _require(snapshot, "turnover_cap_sensitivity", "grid")
    cap_key = str(champion_cap)
    cap_row = cap_grid.get(cap_key) if isinstance(cap_grid, Mapping) else None
    test_avg_turnover = (cap_row or {}).get("test_avg_turnover")
    universe_used = _require(snapshot, "universe", "used")
    panel_end = _require(snapshot, "panel", "range")[-1]
    n_trials = (snapshot.get("multiple_testing") or {}).get("n_trials", 1)
    dry_sharpe = float(_require(dry_run_pnl, "sharpe"))

    definition = AlphaDefinition(
        alpha_id=ALPHA_ID,
        name="A股微盘复合 alpha v3(cap30 换手冠军,top-20 周度调仓)",
        factor_ids=tuple(sorted(str(k) for k in weights)),
        universe=f"ashare_microcap_u{universe_used}",
        params={
            "composite_scheme": str(combo.get("champion", "icir")),
            "composite_form": "winsorized_zscore(1%/99%)",
            "weights_json": json.dumps(weights, sort_keys=True),
            "turnover_rule": f"cap{champion_cap}:max_oneway={champion_cap}",
            "universe_file": _UNIVERSE_FILE,
            "basket_rule": "top-20 weekly, participation_cap=0.05, cost=15bps, AUM=3e6 CNY",
            "source_snapshot": _ALPHA_SNAPSHOT,
        },
    )
    repro = stable_hash({
        "alpha_id": ALPHA_ID,
        "combo_weights": weights,
        "h1_test": h1,
        "dry_run_run_id": dry_run_run_id,
        "dry_run_pnl": dict(dry_run_pnl),
        "panel_end": panel_end,
    })
    evaluation = AlphaEvaluation(
        alpha_id=ALPHA_ID,
        ic_mean=float(h1["mean_ic"]),
        icir=float(h1["icir"]),
        ic_tstat=float(h1["t_stat"]),
        hit_rate=float(h1["hit_rate"]),
        n_periods=int(h1["n_periods"]),
        net_sharpe=dry_sharpe,
        observed_sr=dry_sharpe,
        turnover=float(test_avg_turnover) if test_avg_turnover is not None else 0.0,
        catalog_trial_count=int(n_trials),
        verdict=ValidationVerdict.REQUIRES_HUMAN_REVIEW,
        reasons=(
            "证据来自研究快照+执行现实干跑(alpha_snapshot.json + dry_run.db run "
            f"{dry_run_run_id}),未过 evaluator 门禁(DSR/PBO/walk-forward/leakage 未评估,"
            "相应字段为契约默认值,不代表已评估通过)",
            "宇宙=2026-07-23 幸存者截面(幸存者偏差,IC/回测系统性偏乐观)",
            "非人研究 actor 注册,停留 DRAFT;任何生命周期推进须真人多方批准",
            "cap30 换手上限在 train 净 Sharpe 平台(plateau)选择,test 仅确认,"
            "参数面是额外研究自由度,显著性应打折看待",
        ),
        as_of=int(panel_end),
        reproducibility_hash=repro,
    )
    dataset_version = stable_hash({
        "kind": "ashare_alpha_snapshot",
        "strategy_id": snapshot.get("strategy_id"),
        "panel": snapshot.get("panel"),
        "split": snapshot.get("split"),
        "combo_weights": weights,
    })
    return RegistrationPlan(
        alpha_id=ALPHA_ID,
        definition=definition,
        state=_INITIAL_STATE,
        sharpe=dry_sharpe,
        evaluation=evaluation,
        dataset_version=dataset_version,
    )


def apply_registration(
    plan: RegistrationPlan,
    registry_db: str,
    *,
    recorded_at: Optional[int] = None,
) -> str:
    """隔离旧版注册写入；保留原签名以便调用方得到明确的完整性错误。

    quarantine 必须是首个运行时动作，确保即使传入不存在的路径也不会创建数据库，
    传入现有注册表也不会读取或修改它。
    """
    quarantine_legacy_backtest("tools.ashare_register_alpha.apply_registration")
    if recorded_at is None:
        recorded_at = time.time_ns() // 1_000_000   # 墙钟只在驱动边界(工具入口)
    store = SqliteExperimentStore(registry_db, create=False)
    try:
        store.trial_count()   # fail-closed:未初始化/损坏即 ExperimentStoreError
        existing = {row["alpha_id"]: row for row in store.all_alphas()}.get(plan.alpha_id)
        state = plan.state
        if existing is not None and existing.get("state") != AlphaState.DRAFT.value:
            state = str(existing["state"])   # 已被(人工)推进:保留现状,不回退
        if state == AlphaState.APPROVED.value:
            raise ExperimentStoreError(
                "research registration must not write APPROVED (human-only state)"
            )
        store.save_catalog_snapshot(
            config_hashes=(plan.definition.definition_hash,),
            alphas=[{
                "alpha_id": plan.alpha_id,
                "definition_json": json.dumps(
                    plan.definition.model_dump(), sort_keys=True, default=str
                ),
                "state": state,
                "returns_json": "[]",   # 无逐期收益序列在注册表;收益证据在评估记录的 IC/sharpe
                "sharpe": plan.sharpe,
                "pvalue": None,
            }],
            first_seen_ts=plan.evaluation.as_of,
        )
        registry = ExperimentRegistry(store)
        return registry.record_evaluation(
            plan.evaluation,
            dataset_version=plan.dataset_version,
            recorded_ts=recorded_at,
        )
    finally:
        store.close()


def _load_dry_run_pnl(dry_run_db: str, run_id: str) -> Mapping:
    from services.execution.dry_run_store import DryRunStore   # 延迟导入:保持模块轻

    store = DryRunStore(dry_run_db, create=False)
    run = store.get_run(run_id)
    if run is None:
        raise ExperimentStoreError(f"dry-run run {run_id} not found in {dry_run_db}")
    pnl = run.get("pnl")
    if not isinstance(pnl, Mapping) or pnl.get("sharpe") is None:
        raise ExperimentStoreError(f"dry-run run {run_id} has no pnl.sharpe")
    return pnl


def main() -> None:
    quarantine_legacy_backtest("tools.ashare_register_alpha.main")
    with open(_ALPHA_SNAPSHOT, encoding="utf-8") as fh:
        snapshot = json.load(fh)
    pnl = _load_dry_run_pnl(_DRY_RUN_DB, DRY_RUN_ID)
    plan = build_registration_plan(snapshot, pnl, dry_run_run_id=DRY_RUN_ID)
    run_id = apply_registration(plan, PRODUCTION_EXPERIMENT_DB)
    print(f"registered {plan.alpha_id} state={plan.state} "
          f"sharpe={plan.sharpe:.4f} ic_mean={plan.evaluation.ic_mean:.4f} "
          f"ic_t={plan.evaluation.ic_tstat:.2f} verdict={plan.evaluation.verdict.value}")
    print(f"evaluation run_id={run_id} -> {PRODUCTION_EXPERIMENT_DB}")


if __name__ == "__main__":
    main()
