"""ML stacker 研究驱动器:在现有因子池(crypto crypto_history.db 或 FX fx.db)上,把多因子用确定性 stacker
学成组合信号,过 no-peek 滚动 OOS,并把【组合 GBM / 组合 Ridge / 单因子最优】三方 OOS 并排 + 成本扫描报出。
诚实第一:训练 purged+embargo,裁决用 OOS,不报样本内分数。读本地库,无网络;墙钟只在此边界。"""
from __future__ import annotations

import json
import os
import sys

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.panel_builder import make_data_periods_provider
from services.research.backtest_engine.metrics import sharpe
from services.research.oos_validation import walk_forward_selection_oos
from services.research.ml.feature_panel import build_feature_panel
from services.research.ml.walk_forward_stack import walk_forward_stack
from quantos.contracts.alpha import AlphaDefinition

_POOLS = {
    "crypto": (os.path.join("data", "cache", "crypto_history.db"), 3,
               [("momentum", 7), ("momentum", 14), ("zscore", 5), ("ts_rank", 14),
                ("dist_from_high", 14), ("accel", 14)]),
    "fx": (os.path.join("data", "cache", "fx.db"), 5,
           [("momentum", 60), ("momentum", 120), ("zscore", 60), ("ts_rank", 60),
            ("dist_from_high", 60), ("accel", 60)]),
}


def _instruments_and_times(db):
    import sqlite3
    con = sqlite3.connect(db)
    insts = sorted(r[0] for r in con.execute("SELECT DISTINCT key FROM bars"))
    ets = sorted(r[0] for r in con.execute("SELECT DISTINCT event_time FROM bars"))
    con.close()
    return insts, ets


def main(asset: str = "crypto") -> None:
    db, fwd, pool = _POOLS[asset]
    if not os.path.exists(db):
        print(f"no {db} — fetch first"); return
    insts, ets = _instruments_and_times(db)
    store = CachingPITStore(SqlitePITStore(db, create=False))
    provider = make_data_periods_provider(store, insts, event_times=ets, fwd_horizon=fwd)
    factor_periods, factor_ret = {}, {}
    for op, w in pool:
        per = provider(AlphaDefinition(alpha_id=f"{op}_{w}", name=f"{op}({w})",
                                       factor_ids=("close",), params={"operator": op, "window": str(w)}))
        if len(per) >= 400:
            factor_periods[f"{op}_{w}"] = per
    fp = build_feature_panel(factor_periods)
    print(f"{asset}: {len(insts)} insts x {len(ets)} periods | feature matrix {fp.X.shape} | factors {fp.factor_names}",
          flush=True)
    if fp.X.shape[0] < 500:
        print("too few aligned rows"); return

    ts, te = 1000, 250
    gbm = walk_forward_stack(fp, train_size=ts, test_size=te, embargo=fwd, model="gbm")
    ridge = walk_forward_stack(fp, train_size=ts, test_size=te, embargo=fwd, model="ridge")

    def _sr(rs):
        return round(sharpe(rs), 4) if len(rs) > 1 else None

    # single-factor-best OOS baseline (reuse the existing no-peek selector on per-factor net L/S returns)
    from services.research.backtest_engine.costs import CostModel
    from services.alpha.panel_backtest import run_panel_backtest
    fr = {n: list(run_panel_backtest(p, CostModel(fee_bps=0.0, slippage_bps=2.0)).period_returns)
          for n, p in factor_periods.items()}
    common = min((len(v) for v in fr.values()), default=0)
    fr = {n: v[-common:] for n, v in fr.items() if len(v) >= 1500}
    single = walk_forward_selection_oos(fr, train_size=ts, test_size=te, embargo=fwd, top_k=1) if fr else {}

    # walk_forward_selection_oos 无顶层 selected/top 键(选择是逐折的);展示「最常被选中」的因子名。
    # 仅影响展示,不动任何 OOS 数值。确定性:并列时按字典序最小的名取。
    def _most_selected(s):
        from collections import Counter
        c = Counter(name for f in s.get("folds", []) for name in f.get("selected", []))
        if not c:
            return None
        top = max(c.values())
        return sorted(n for n, v in c.items() if v == top)[0]

    result = {
        "asset": asset, "factors": fp.factor_names, "feature_rows": int(fp.X.shape[0]), "fwd_horizon": fwd,
        "stack_gbm_oos_sharpe": _sr(gbm["oos_returns"]), "stack_gbm_folds": gbm["n_folds"],
        "stack_ridge_oos_sharpe": _sr(ridge["oos_returns"]), "stack_ridge_folds": ridge["n_folds"],
        "single_best_oos_sharpe": single.get("mean_oos_sharpe"), "single_best_name": _most_selected(single),
        "trials": len(factor_periods) + 2,
    }
    g, r, s = result["stack_gbm_oos_sharpe"], result["stack_ridge_oos_sharpe"], result["single_best_oos_sharpe"]
    if g is not None and r is not None and g > max(r, (s or -9)) and g > 0:
        verdict = "STACK_GBM_BEATS_BASELINE"
    elif (g is None or (r is not None and g <= r)) and r is not None and r > 0:
        verdict = "NO_LIFT_OVER_LINEAR"
    else:
        verdict = "REFUTED_NO_EDGE"
    result["verdict"] = verdict

    print(f"\n=== 三方 OOS Sharpe ===\n  stack GBM   {g}  (folds {gbm['n_folds']})\n"
          f"  stack Ridge {r}  (folds {ridge['n_folds']})\n  single best {s}\n  verdict: {verdict}", flush=True)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    json.dump(result, open(os.path.join("data", "cache", "ml_stack_snapshot.json"), "w", encoding="utf-8"),
              indent=2, sort_keys=True)
    print("snapshot -> data/cache/ml_stack_snapshot.json", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "crypto")
