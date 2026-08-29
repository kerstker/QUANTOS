"""截面 IC 面板驱动器:逐条 FACTOR_REGISTRY 建面板 -> 算信号 -> 逐期截面 IC -> 聚合统计,写一份快照。
只读本地库,无网络。这是"初筛诊断",不驱动任何晋级——不写 experiment_registry、不碰 AlphaCatalog。

2026-07-06 起支持 per-factor 多持有期口径(schema_version=2,只加字段不删字段):
- 顶层六统计 + per_year 仍然是 h=1 口径、走 compute_period_ic_series 旧函数(字节级复现既有数字,
  向后兼容锚,tools/ic_panel_h1_parity_check.py 可对旧快照逐位校验);
- 新增 by_period["<h>"](h>1 走 compute_horizon_ic_series,非重叠抽样,t-stat 诚实)。
  持有期单位 = 面板自身周期(当前四宇宙全日频,h=7≈周度口径)。
"""
from __future__ import annotations

import json
import os
import time

from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research.ic_panel import (aggregate_ic_series, compute_horizon_ic_series,
                                        compute_period_ic_series)
from tools.ic_panel_registry import FACTOR_REGISTRY, UNIVERSE_REGISTRY

_OUT = os.path.join("data", "cache", "ic_panel_snapshot.json")
_SCHEMA_VERSION = 2


def factor_row(entry: dict, panel) -> dict:
    """单因子 -> 快照 row(纯组装,可单测)。h=1 走 compute_period_ic_series(旧函数、旧调用形态,
    字节级复现既有快照数字);h>1 走 compute_horizon_ic_series(非重叠抽样)。顶层统计字段恒 ==
    by_period['1'](向后兼容锚,测试钉死);h=1 不在注册表列表里也强制补上(顶层锚必须存在)。"""
    signal = entry["signal_fn"](panel)
    price = panel.matrices["close"]
    hps = sorted(set([1] + [int(h) for h in entry.get("holding_periods", [1])]))

    by_period = {}
    for h in hps:
        if h == 1:
            ics = compute_period_ic_series(signal, price)
            agg = aggregate_ic_series(ics, event_times=panel.event_times[:len(ics)])
        else:
            ics, starts = compute_horizon_ic_series(signal, price, h)
            agg = aggregate_ic_series(ics, event_times=[panel.event_times[i] for i in starts])
        by_period[str(h)] = agg

    return {"name": entry["name"], "universe": entry["universe"], "thesis": entry["thesis"],
            "n_instruments": len(panel.instruments), **by_period["1"],
            "holding_periods": hps, "by_period": by_period}


def main() -> None:
    quarantine_legacy_backtest("tools.ic_panel_research")
    panel_cache = {}
    universes_meta = {}
    rows = []
    for entry in FACTOR_REGISTRY:
        uni = entry["universe"]
        if uni not in panel_cache:
            panel_cache[uni] = UNIVERSE_REGISTRY[uni]()
        panel = panel_cache[uni]
        if uni not in universes_meta:
            universes_meta[uni] = {
                "n_instruments": len(panel.instruments),
                "date_start": time.strftime("%Y-%m-%d", time.gmtime(panel.event_times[0] / 1000)),
                "date_end": time.strftime("%Y-%m-%d", time.gmtime(panel.event_times[-1] / 1000)),
            }
        row = factor_row(entry, panel)
        rows.append(row)
        multi = " ".join(f"h{h}:t={row['by_period'][str(h)]['t_stat']}" for h in row["holding_periods"])
        print(f"{entry['name']:20s} [{uni:20s}] mean_ic={row['mean_ic']} icir={row['icir']} "
              f"hit_rate={row['hit_rate']} n={row['n_periods']} · {multi}", flush=True)

    json.dump({"generated_at": int(time.time()), "schema_version": _SCHEMA_VERSION,
               "rows": rows, "universes": universes_meta},
              open(_OUT, "w", encoding="utf-8"), indent=2, sort_keys=True)
    print(f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
