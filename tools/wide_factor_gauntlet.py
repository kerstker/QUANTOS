"""宽宇宙候选证伪 gauntlet(2026-07-05,机构级补全 ⑨)。

背景:IC 面板日频初筛里 `reversal_7_wide`(短反转)/`low_vol_wide`(低波)t-stat≈16——统计上
巨大,但这正是最该怀疑的时刻:日频截面 IC 大 ≠ 可交易(FX 那次 +0.70 的教训:毛口径 + 不可
交易宇宙 = mirage)。本 gauntlet 按项目纪律做第一层证伪:**周度调仓 L/S 分位组合 + 换手计费
的成本扫描**(10/25/50bps 单边),报告毛/净夏普与真实换手。

诚实边界:等权 L/S 分位组合、周度收盘调仓、成本=换手×单边费率——没有冲击模型(那是 RE-1
的活),也没有做空可行性/借币成本(宽宇宙小币的空腿现实中往往做不了,结果再好也要打这个
折扣)。这是初筛证伪,不是晋级门禁。输出 `data/cache/wide_factor_gauntlet.json`。
"""
from __future__ import annotations

import json
import os
import statistics
import time
from datetime import datetime, timezone

from tools.ic_panel_registry import FACTOR_REGISTRY, UNIVERSE_REGISTRY

_OUT = os.path.join("data", "cache", "wide_factor_gauntlet.json")
_CANDIDATES = ("reversal_7_wide", "low_vol_wide", "momentum_7_wide")   # momentum 作对照
_COST_BPS = (0.0, 10.0, 25.0, 50.0)
_QUANTILE = 0.2


def _weekly_rows(event_times) -> list:
    rows, last = [], None
    for r, t in enumerate(event_times):
        wk = datetime.fromtimestamp(t / 1000, tz=timezone.utc).isocalendar()[:2]
        if wk != last:
            rows.append(r)
            last = wk
    return rows


def _legs(sig_row, close_row, quantile) -> dict:
    valid = [(i, s) for i, s in enumerate(sig_row)
             if s is not None and close_row[i] is not None and close_row[i] > 0]
    n_leg = int(len(valid) * quantile)
    if n_leg < 3:
        return {}
    ranked = sorted(valid, key=lambda p: p[1])
    w = {}
    for i, _s in ranked[-n_leg:]:
        w[i] = 1.0 / (2 * n_leg)                     # 多头腿,gross 合计 1
    for i, _s in ranked[:n_leg]:
        w[i] = -1.0 / (2 * n_leg)
    return w


def _evaluate(name: str, signal_fn, panel) -> dict:
    sig = signal_fn(panel)
    close = panel.matrices["close"]
    rows = _weekly_rows(panel.event_times)
    gross_rets, turnovers = [], []
    prev_w: dict = {}
    for a, b in zip(rows, rows[1:]):
        w = _legs(sig[a], close[a], _QUANTILE)
        if not w:
            prev_w = {}
            continue
        # 覆盖率 fail-closed:两端都有价才计收益;掉价的腿按 0 收益诚实少算(不是补插值)
        ret = 0.0
        for i, wi in w.items():
            p0, p1 = close[a][i], close[b][i]
            if p0 and p1 and p0 > 0:
                ret += wi * (p1 / p0 - 1.0)
        gross_rets.append(ret)
        keys = set(w) | set(prev_w)
        turnovers.append(sum(abs(w.get(i, 0.0) - prev_w.get(i, 0.0)) for i in keys))
        prev_w = w

    if len(gross_rets) < 30:
        return {"name": name, "verdict": "INSUFFICIENT_DATA", "n_weeks": len(gross_rets)}

    avg_turnover = statistics.fmean(turnovers)
    out_by_cost = {}
    for cost in _COST_BPS:
        nets = [g - t * cost / 1e4 for g, t in zip(gross_rets, turnovers)]
        mu, sd = statistics.fmean(nets), statistics.pstdev(nets)
        sharpe = (mu / sd * (52 ** 0.5)) if sd > 0 else None
        out_by_cost[f"{cost:.0f}bps"] = {
            "ann_sharpe": round(sharpe, 3) if sharpe is not None else None,
            "ann_return": round(mu * 52, 4),
        }
    return {
        "name": name,
        "n_weeks": len(gross_rets),
        "avg_weekly_turnover": round(avg_turnover, 3),
        "by_cost": out_by_cost,
        "verdict": ("SURVIVES_COSTS" if (out_by_cost["25bps"]["ann_sharpe"] or 0) > 1.0
                    else "COST_DOMINATED" if (out_by_cost["0bps"]["ann_sharpe"] or 0) > 1.0
                    else "NO_EDGE_EVEN_GROSS"),
    }


def main() -> None:
    panel = UNIVERSE_REGISTRY["crypto_binance_wide"]()
    by_name = {f["name"]: f for f in FACTOR_REGISTRY}
    results = [_evaluate(n, by_name[n]["signal_fn"], panel) for n in _CANDIDATES if n in by_name]
    payload = {"generated_at": int(time.time()), "quantile": _QUANTILE,
              "universe": "crypto_binance_wide", "rebalance": "weekly",
              "cost_model": "turnover x per-side bps (no impact model; short-leg feasibility NOT assessed)",
              "results": results}
    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    for r in results:
        print(r["name"], r.get("verdict"), json.dumps(r.get("by_cost", {})), flush=True)
    print(f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
