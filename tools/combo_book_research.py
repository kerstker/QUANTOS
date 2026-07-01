"""兑现分散故事:把 carry 与 crowd_contra 两条边(宏观 regime 画像相反、互补)的逐周 gross 收益按 ISO 年-周对齐,
组合(50/50 等权 + 逆波动风险平价),比 standalone vs combo 的年化夏普/最大回撤/总收益 + combo 的宏观 regime 分桶
(是否真抹平 regime 依赖)。落 data/cache/combo_book_snapshot.json。

诚实:收益流层面的分散测试(carry 58 流动币 / crowd 14 大币,不同宇宙/规模/网格),gross 无成本 → RELATIVE 比较才是
结论。纯离线读本地库、无网络、无真单;诊断非门禁。
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time

from services.data.fundamental_store import SqliteFundamentalStore
from services.research import alpha_expression as ax
from services.research.book_combine import align_on_common_weeks, combine_returns, inverse_vol_weights
from services.research.macro_regime import bucket_returns, sign_regime, tercile_labels
from tools.scenario_tail_research import _book_series, _carry_panel
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE, QUANTILE as CARRY_Q, _legs as carry_legs
from tools.crypto_oi_research import build_panel as oi_build_panel, _legs as oi_legs, QUANTILE as OI_Q
from tools.crypto_oi_verify import CROWD_CONTRA

_MACRO_DB = os.path.join("data", "cache", "macro_fred.db")
_OUT = os.path.join("data", "cache", "combo_book_snapshot.json")


def _ann_sharpe(rs, ppy=52):
    if len(rs) < 2:
        return 0.0
    sd = statistics.pstdev(rs)
    return 0.0 if sd == 0 else statistics.fmean(rs) / sd * math.sqrt(ppy)


def _total_return(rs):
    cum = 1.0
    for r in rs:
        cum *= (1.0 + r)
    return cum - 1.0


def _max_drawdown(rs):
    peak = eq = 1.0
    mdd = 0.0
    for r in rs:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return mdd


def _metrics(rs):
    return {"n": len(rs), "ann_sharpe": round(_ann_sharpe(rs), 3),
            "max_drawdown": round(_max_drawdown(rs), 4), "total_return": round(_total_return(rs), 4)}


def _corr(a, b):
    if len(a) < 2:
        return None
    return round(statistics.correlation(a, b), 3) if statistics.pstdev(a) and statistics.pstdev(b) else 0.0


def main() -> None:
    cp = _carry_panel()
    cg, cts, _ = _book_series(cp.instruments, cp.matrices["close"], cp.matrices["funding"],
                              cp.event_times, ax.evaluate(CARRY_COMPOSITE, cp), carry_legs, CARRY_Q)
    op, _ = oi_build_panel()
    og, ots, _ = _book_series(op.instruments, op.matrices["close"], op.matrices["funding"],
                              op.event_times, ax.evaluate(CROWD_CONTRA, op), oi_legs, OI_Q)

    al = align_on_common_weeks(cg, cts, og, ots)
    carry, crowd, ts = al["a"], al["b"], al["ts"]

    w_eq = [0.5, 0.5]
    w_iv = inverse_vol_weights([carry, crowd])
    combo_eq = combine_returns([carry, crowd], w_eq)
    combo_iv = combine_returns([carry, crowd], w_iv)

    snap = {"provenance": "REAL_COMBINED_BOOK", "common_weeks": len(ts),
            "corr_carry_crowd": _corr(carry, crowd),
            "inverse_vol_weights": {"carry": round(w_iv[0], 3), "crowd_contra": round(w_iv[1], 3)},
            "standalone": {"carry": _metrics(carry), "crowd_contra": _metrics(crowd)},
            "combo": {"equal_weight": _metrics(combo_eq), "inverse_vol": _metrics(combo_iv)},
            "note": "收益流层面分散测试(不同宇宙/规模/网格);gross 无成本 → RELATIVE 比较为准。"}

    # combo 的宏观 regime 分桶(是否抹平 regime 依赖)
    store = SqliteFundamentalStore(_MACRO_DB)
    def macro(series):
        return [ (lambda fv: fv.value if fv else None)(store.latest_as_of(series, "MACRO",
                 int(time.strftime("%Y%m%d", time.gmtime(t / 1000))))) for t in ts ]
    vol_lab = tercile_labels(macro("VIXCLS"))
    rate_lab = [sign_regime(v) for v in macro("T10Y2Y")]
    snap["combo_eq_by_regime"] = {
        "vol_VIX": bucket_returns(combo_eq, vol_lab),
        "rates_2s10s": bucket_returns(combo_eq, rate_lab),
    }
    snap["standalone_carry_by_vol"] = bucket_returns(carry, vol_lab)
    snap["standalone_crowd_by_vol"] = bucket_returns(crowd, vol_lab)

    json.dump(snap, open(_OUT, "w", encoding="utf-8"), indent=2, default=str)

    print(f"=== carry+crowd 组合分散验证 · 共同周 {len(ts)} · corr {snap['corr_carry_crowd']} (REAL_COMBINED_BOOK) ===", flush=True)
    print(f"  逆波动权重: carry {w_iv[0]:.2f} / crowd {w_iv[1]:.2f}", flush=True)
    for name, m in (("carry-alone", snap["standalone"]["carry"]), ("crowd-alone", snap["standalone"]["crowd_contra"]),
                    ("combo 50/50", snap["combo"]["equal_weight"]), ("combo 逆波动", snap["combo"]["inverse_vol"])):
        print(f"  {name:14s}: 年化夏普 {m['ann_sharpe']:+.2f} · 最大回撤 {m['max_drawdown']*100:4.0f}% · 总收益 {m['total_return']*100:+.0f}%", flush=True)
    print("  combo(50/50)按 VIX regime: " +
          " · ".join(f"{lb} SR {b['ann_sharpe']:+.2f}" for lb, b in sorted(snap["combo_eq_by_regime"]["vol_VIX"].items())), flush=True)
    print(f"-> wrote {_OUT}", flush=True)


if __name__ == "__main__":
    main()
