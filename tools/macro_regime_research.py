"""数据方向:把已验证的 carry / crowd_contra 边按 FRED 宏观 regime 分桶,看边在不同 regime 的表现差异。
逐周把 book 的 gross 收益打上宏观标签(VIX 三分位 / 2s10s 符号 / HY-OAS 三分位),每桶算年化夏普/均收益。
PIT:`latest_as_of(该周)` 只取该周可知的宏观值。落 data/cache/macro_regime_snapshot.json。

2026-07-06 增量:ALFRED vintage 经济发布维(inflation_CPI_yoy 三分位 / labor_UNRATE 三分位),读独立的
data/cache/macro_alfred.db(tools/alfred_fetch.py 填)。输入值**真 PIT**——m 月 CPI 在发布前对任何周
不可见,修正值只在其 realtime_start 之后可见(与 fredgraph"最新修正值冒充当时可知"划清界限)。
fail-soft:macro_alfred.db 不存在/空 → 这两维整体缺席 + note 诚实标注,绝不 raise、绝不用
macro_fred.db 的最新值冒充 vintage。GDPC1 第一批只入库不分桶(季频 × 6 年 crypto 样本 ≈ 24 期,
分桶无统计意义)。

诚实:regime 标签用全样本分位=回顾性描述(非 PIT 择时);HY-OAS 仅 2023-07+(FRED 限)→ 信用维覆盖不足标注;
通胀/失业标签月频黏滞 ~4 周 → 桶内周收益不独立,只报描述统计;crypto 样本只覆盖一轮通胀周期(2021-22 冲高
+ 2023-25 回落),n=1 个周期,不得当择时依据。纯离线读本地库、无真单;研究诊断,不驱动晋级/下单。
"""
from __future__ import annotations

import json
import os
import time

from services.data.fundamental_store import SqliteFundamentalStore
from services.research import alpha_expression as ax
from services.research.macro_regime import bucket_returns, pit_series, sign_regime, tercile_labels
from tools.scenario_tail_research import _book_series, _carry_panel
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE, QUANTILE as CARRY_Q, _legs as carry_legs
from tools.crypto_oi_research import build_panel as oi_build_panel, _legs as oi_legs, QUANTILE as OI_Q
from tools.crypto_oi_verify import CROWD_CONTRA

_MACRO_DB = os.path.join("data", "cache", "macro_fred.db")
_ALFRED_DB = os.path.join("data", "cache", "macro_alfred.db")
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


def _alfred_rows(series, db_path):
    """fail-soft 读 ALFRED vintage 全行集 -> [(event, knowledge, value)];库缺失/坏/序列空 → None。"""
    if not os.path.exists(db_path):
        return None
    try:
        store = SqliteFundamentalStore(db_path)
        rows = store.as_of(series, "MACRO", 99999999)
    except Exception:
        return None
    if not rows:
        return None
    return [(fv.event_time, fv.knowledge_time, fv.value) for fv in rows]


def _alfred_regime_labels(ts, db_path=_ALFRED_DB):
    """通胀/就业 regime 维(输入值真 PIT vintage,pit_series 单调游标批量求值;分位标签仍全样本
    回顾性,与既有三维口径一致)。macro_alfred.db 缺失/空 → {}(诚实跳过,不 raise、不冒充)。"""
    dims = {}
    ymd = [_ymd(t) for t in ts]
    cpi = _alfred_rows("CPIAUCSL", db_path)
    if cpi:
        dims["inflation_CPI_yoy"] = tercile_labels(pit_series(cpi, ymd, months_back=12))
    unrate = _alfred_rows("UNRATE", db_path)
    if unrate:
        dims["labor_UNRATE"] = tercile_labels(pit_series(unrate, ymd))
    return dims


def _analyze(store, name, gross, ts):
    labels = {**_regime_labels(store, ts), **_alfred_regime_labels(ts)}
    dims = {}
    for dim, lab in labels.items():
        buckets = bucket_returns(gross, lab)
        covered = sum(v["n"] for v in buckets.values())
        dims[dim] = {"buckets": buckets, "covered_periods": covered}
    return {"n_periods": len(gross), "dims": dims}


def main() -> None:
    store = SqliteFundamentalStore(_MACRO_DB)
    alfred_present = _alfred_rows("CPIAUCSL", _ALFRED_DB) is not None
    alfred_note = ("通胀/失业维输入值=ALFRED 真 vintage(PIT);标签月频黏滞、样本只有一轮通胀周期"
                   "(n=1),只作描述画像,不得当择时依据。"
                   if alfred_present else
                   "ALFRED(macro_alfred.db)未抓取——通胀/失业维诚实跳过(需 FRED API key,"
                   "见 tools/alfred_fetch.py;绝不用 macro_fred.db 最新修正值冒充 vintage)。")
    snap = {"provenance": "REAL_FRED_MACRO", "books": {}, "alfred_present": alfred_present,
            "note": ("regime 标签=全样本分位(回顾性描述,非 PIT 择时);HY-OAS 仅 2023-07+(FRED 限)"
                     "→ 信用维覆盖不足。" + alfred_note)}

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
