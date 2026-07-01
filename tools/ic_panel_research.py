"""截面 IC 面板驱动器:逐条 FACTOR_REGISTRY 建面板 -> 算信号 -> 逐期截面 IC -> 聚合统计,写一份快照。
只读本地库,无网络。这是"初筛诊断",不驱动任何晋级——不写 experiment_registry、不碰 AlphaCatalog。
"""
from __future__ import annotations

import json
import os
import time

from services.research.ic_panel import aggregate_ic_series, compute_period_ic_series
from tools.ic_panel_registry import FACTOR_REGISTRY, UNIVERSE_REGISTRY

_OUT = os.path.join("data", "cache", "ic_panel_snapshot.json")


def main() -> None:
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
        signal = entry["signal_fn"](panel)
        price = panel.matrices["close"]
        ics = compute_period_ic_series(signal, price)
        agg = aggregate_ic_series(ics, event_times=panel.event_times[:len(ics)])
        rows.append({"name": entry["name"], "universe": uni, "thesis": entry["thesis"],
                     "n_instruments": len(panel.instruments), **agg})
        print(f"{entry['name']:20s} [{uni:20s}] mean_ic={agg['mean_ic']} icir={agg['icir']} "
              f"hit_rate={agg['hit_rate']} n={agg['n_periods']}", flush=True)

    json.dump({"generated_at": int(time.time()), "rows": rows, "universes": universes_meta},
              open(_OUT, "w", encoding="utf-8"), indent=2, sort_keys=True)
    print(f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
