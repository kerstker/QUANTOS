"""数据方向:把已验证的 carry / crowd_contra 边按 FRED 宏观 regime 分桶,看边在不同 regime 的表现差异。
逐周把 book 的 gross 收益打上宏观标签(VIX 三分位 / 2s10s 符号 / HY-OAS 三分位),每桶算年化夏普/均收益。
PIT:`latest_as_of(该周)` 只取该周可知的宏观值。落 data/cache/macro_regime_snapshot.json。

诚实:regime 标签用全样本分位=回顾性描述(非 PIT 择时);HY-OAS 仅 2023-07+(FRED 限)→ 信用维覆盖不足标注。
纯离线读本地库、无真单;研究诊断,不驱动晋级/下单。
"""
from __future__ import annotations

import json
import os
import time

from services.data.fundamental_store import SqliteFundamentalStore
from services.research import alpha_expression as ax
from services.research.macro_regime import bucket_returns, sign_regime, tercile_labels
from tools.scenario_tail_research import _book_series, _carry_panel
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE, QUANTILE as CARRY_Q, _legs as carry_legs
from tools.crypto_oi_research import build_panel as oi_build_panel, _legs as oi_legs, QUANTILE as OI_Q
from tools.crypto_oi_verify import CROWD_CONTRA

_MACRO_DB = os.path.join("data", "cache", "macro_fred.db")
_OUT = os.path.join("data", "cache", "macro_regime_snapshot.json")


def _ymd(ms):
    return int(time.strftime("%Y%m%d", time.gmtime(ms / 1000)))


def _macro_series(store, series, ts):
    """逐周该期可知的宏观值(PIT latest_as_of)。"""
    out = []
    for t in ts:
        fv = store.latest_as_of(series, "MACRO", _ymd(t))
        out.append(fv.value if fv is not None else None)
    return out


def _regime_labels(store, ts):
    vix = _macro_series(store, "VIXCLS", ts)
    slope = _macro_series(store, "T10Y2Y", ts)
    credit = _macro_series(store, "BAMLH0A0HYM2", ts)
    return {
        "vol_VIX": tercile_labels(vix),
        "rates_2s10s": [sign_regime(v) for v in slope],
        "credit_HYOAS": tercile_labels(credit),
    }


def _analyze(store, name, gross, ts):
    labels = _regime_labels(store, ts)
    dims = {}
    for dim, lab in labels.items():
        buckets = bucket_returns(gross, lab)
        covered = sum(v["n"] for v in buckets.values())
        dims[dim] = {"buckets": buckets, "covered_periods": covered}
    return {"n_periods": len(gross), "dims": dims}


def main() -> None:
    store = SqliteFundamentalStore(_MACRO_DB)
    snap = {"provenance": "REAL_FRED_MACRO", "books": {},
            "note": "regime 标签=全样本分位(回顾性描述,非 PIT 择时);HY-OAS 仅 2023-07+(FRED 限)→ 信用维覆盖不足。"}

    cp = _carry_panel()
    cg, cts, _ = _book_series(cp.instruments, cp.matrices["close"], cp.matrices["funding"],
                              cp.event_times, ax.evaluate(CARRY_COMPOSITE, cp), carry_legs, CARRY_Q)
    snap["books"]["carry"] = _analyze(store, "carry", cg, cts)

    op, _ = oi_build_panel()
    og, ots, _ = _book_series(op.instruments, op.matrices["close"], op.matrices["funding"],
                              op.event_times, ax.evaluate(CROWD_CONTRA, op), oi_legs, OI_Q)
    snap["books"]["crowd_contra"] = _analyze(store, "crowd_contra", og, ots)

    json.dump(snap, open(_OUT, "w", encoding="utf-8"), indent=2, default=str)

    print("=== 边 × 宏观 regime 分桶(年化夏普;REAL_FRED_MACRO)===", flush=True)
    for name, a in snap["books"].items():
        print(f"\n[{name}] {a['n_periods']} 周", flush=True)
        for dim, d in a["dims"].items():
            parts = " · ".join(f"{lb}: SR {b['ann_sharpe']:+.2f}(n{b['n']})"
                               for lb, b in sorted(d["buckets"].items()))
            cov = f" [覆盖 {d['covered_periods']}/{a['n_periods']}]" if d["covered_periods"] < a["n_periods"] else ""
            print(f"  {dim:14s}: {parts}{cov}", flush=True)
    print(f"\n-> wrote {_OUT}", flush=True)


if __name__ == "__main__":
    main()
