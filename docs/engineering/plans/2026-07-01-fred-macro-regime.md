# FRED 宏观 + Regime 条件化 实现计划

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 抓 FRED 市场型宏观序列填 fundamental_store,把 carry/crowd_contra 边按宏观 regime 分桶,诚实记录 regime 依赖。

**Architecture:** `tools/fred_fetch.py`(网络,gate 外)→ `SqliteFundamentalStore`(macro_fred.db);`services/research/macro_regime.py`(纯核);`tools/macro_regime_research.py`(驱动器,复用 RE book 组装)。

## Global Constraints

- L2 一寸不让:fetcher 网络在 tools/;`services/` 无网络;只读诊断,不驱动晋级/下单、不碰 LIVE 标志。
- 市场序列 knowledge_time=event_time(EOD 可知,无前视);经济发布类不碰;regime 标签回顾性(全样本分位)显式标注。
- fetcher fail-closed(HTTP≠200/空 → raise,不写半截)。

---

### FM1: FRED fetcher + 填 store

**Files:** Create `tools/fred_fetch.py`

- [ ] **Step 1: 写 fetcher**（实现期先 Read `services/data/fundamental_store.py` 确认 `SqliteFundamentalStore(create=)`/`.add(FeatureValue)` 与 `FeatureValue` 字段。）

```python
# tools/fred_fetch.py
"""抓 FRED 市场型日频宏观序列(无修正、EOD 可知 → 真 PIT)填进 fundamental_store。网络在 tools/(出 gate);
services/ 保持无网络。市场序列 knowledge_time=event_time。经济发布类(CPI/GDP)不抓——免费 fredgraph 只给最新修正值
(前视),需 ALFRED vintage。fail-closed:HTTP≠200/空/全缺失 → raise。"""
from __future__ import annotations

import io
import csv
import os
import sys
import urllib.request

from quantos.contracts.feature import FeatureValue
from services.data.fundamental_store import SqliteFundamentalStore

_DB = os.path.join("data", "cache", "macro_fred.db")
_COSD = "1990-01-01"
_SERIES = ["VIXCLS", "DGS10", "T10Y2Y", "BAMLH0A0HYM2", "DTWEXBGS", "DFII10"]


def _fetch_csv(series_id: str) -> str:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={_COSD}"
    req = urllib.request.Request(url, headers={"User-Agent": "quantos-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"FRED {series_id} HTTP {resp.status}")
        body = resp.read().decode("utf-8")
    if not body.strip() or "\n" not in body:
        raise RuntimeError(f"FRED {series_id} empty body")
    return body


def _ingest(series_id: str, body: str, store: SqliteFundamentalStore) -> tuple:
    added = seen = 0
    for row in csv.DictReader(io.StringIO(body)):
        vals = list(row.values())
        date_s, val_s = vals[0], vals[1]              # observation_date, <series>
        if not date_s or val_s in (".", "", None):
            continue
        try:
            v = float(val_s)
        except ValueError:
            continue
        yyyymmdd = int(date_s.replace("-", ""))
        seen += 1
        fv = FeatureValue(feature_id=series_id, instrument_id="MACRO",
                          event_time=yyyymmdd, knowledge_time=yyyymmdd, value=v)  # 市场序列 EOD 可知
        if store.add(fv):
            added += 1
    if seen == 0:
        raise RuntimeError(f"FRED {series_id} all-missing")
    return added, seen


def main() -> None:
    if os.path.exists(_DB):
        os.remove(_DB)
    store = SqliteFundamentalStore(_DB, create=True)
    total = 0
    for s in _SERIES:
        body = _fetch_csv(s)
        added, seen = _ingest(s, body, store)
        total += added
        first = store.as_of(s, "MACRO", 99999999)
        span = f"{first[0].event_time}–{first[-1].event_time}" if first else "—"
        print(f"  {s:14s} added {added}/{seen} · span {span}", flush=True)
    print(f"=== FRED 宏观填库完成 · {len(_SERIES)} 序列 · {total} 行 -> {_DB} ===", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FRED fetch FAILED (fail-closed): {exc}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 2: 真跑** — `PYTHONPATH=. python tools/fred_fetch.py` → 6 序列 added/span 打印;`macro_fred.db` 落盘。
- [ ] **Step 3: Commit** — `research(data): FRED 市场型宏观序列填 fundamental_store(VIX/利率/斜率/信用/美元/实利率,PIT kt=et)`

---

### FM2: 宏观 regime 分类器(纯核)

**Files:** Create `services/research/macro_regime.py`;Test `tests/research/test_macro_regime.py`

- [ ] **Step 1: 失败测试**

```python
# tests/research/test_macro_regime.py
from services.research.macro_regime import bucket_returns, sign_regime, tercile_labels


def test_tercile_labels_splits_three():
    labels = tercile_labels([1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert labels[0] == "low" and labels[-1] == "high" and "mid" in labels


def test_sign_regime():
    assert sign_regime(-0.5) == "inverted"
    assert sign_regime(0.3) == "normal"


def test_bucket_returns_groups_and_scores():
    rs = [0.02, -0.01, 0.03, -0.02]
    lb = ["high", "low", "high", "low"]
    out = bucket_returns(rs, lb)
    assert out["high"]["n"] == 2 and out["low"]["n"] == 2
    assert out["high"]["mean_return"] > out["low"]["mean_return"]


def test_bucket_returns_ignores_none_labels():
    out = bucket_returns([0.01, 0.02], ["a", None])
    assert "a" in out and None not in out and out["a"]["n"] == 1
```

- [ ] **Step 2: 确认失败** — ModuleNotFoundError

- [ ] **Step 3: 实现**

```python
# services/research/macro_regime.py
"""宏观 regime 分类器(数据方向)—— 把宏观序列切成 regime,把一条边的逐周收益按 regime 分桶算表现。纯函数、
确定性、无 IO/RNG/墙钟/网络。注:tercile 用全样本分位=回顾性描述,非 PIT 可交易择时(调用方须标注)。"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence


def tercile_labels(values: Sequence[float]) -> list:
    """全样本三分位 → ['low'|'mid'|'high']。<3 个有效值 → 全 'mid'。"""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return ["mid"] * len(values)
    s = sorted(vals)
    lo, hi = s[len(s) // 3], s[2 * len(s) // 3]
    out = []
    for v in values:
        if v is None:
            out.append(None)
        elif v <= lo:
            out.append("low")
        elif v >= hi:
            out.append("high")
        else:
            out.append("mid")
    return out


def sign_regime(value: Optional[float], neg_label: str = "inverted", pos_label: str = "normal") -> Optional[str]:
    if value is None:
        return None
    return neg_label if value < 0 else pos_label


def _ann_sharpe(rs: Sequence[float], ppy: int = 52) -> float:
    if len(rs) < 2:
        return 0.0
    sd = statistics.pstdev(rs)
    return 0.0 if sd == 0 else statistics.fmean(rs) / sd * math.sqrt(ppy)


def bucket_returns(returns: Sequence[float], labels: Sequence, ppy: int = 52) -> dict:
    """按 label 分桶 → {label: {n, ann_sharpe, mean_return, total_return}}。label=None 的期跳过。"""
    buckets = {}
    for r, lb in zip(returns, labels):
        if lb is None:
            continue
        buckets.setdefault(lb, []).append(r)
    out = {}
    for lb, rs in buckets.items():
        cum = 1.0
        for r in rs:
            cum *= (1.0 + r)
        out[lb] = {"n": len(rs), "ann_sharpe": round(_ann_sharpe(rs, ppy), 3),
                   "mean_return": round(statistics.fmean(rs), 6), "total_return": round(cum - 1.0, 6)}
    return out
```

- [ ] **Step 4: 确认通过** — PASS
- [ ] **Step 5: Commit** — `feat(research): 宏观 regime 分类器(三分位/符号 regime + 收益分桶,纯函数)`

---

### FM3: regime 条件化边分析驱动器

**Files:** Create `tools/macro_regime_research.py`

- [ ] **Step 1: 写驱动器**（实现期先 Read `tools/scenario_tail_research.py::_book_series`、`_carry_panel`;`SqliteFundamentalStore.latest_as_of`。）

要点:
- 复用 `_book_series` 得 carry/crowd 的 `(gross, ts)`。
- `import time`;`ymd = lambda ms: int(time.strftime("%Y%m%d", time.gmtime(ms/1000)))`。
- `store = SqliteFundamentalStore("data/cache/macro_fred.db")`;逐周 `fv = store.latest_as_of(series,"MACRO",ymd(t))`;`macro[series][k] = fv.value if fv else None`。
- 三维:`vol` = `tercile_labels(macro["VIXCLS"])`;`rates` = `[sign_regime(v) for v in macro["T10Y2Y"]]`;`credit` = `tercile_labels(macro["BAMLH0A0HYM2"])`。
- 每条边 × 每维:`bucket_returns(gross, labels)`。落 `data/cache/macro_regime_snapshot.json`(provenance `REAL_FRED_MACRO`)+ 打印每桶年化夏普/n。诚实结论:边在哪些 regime 强/弱/死;信用维若覆盖不足(HY OAS 2015+ vs book 2020+)标注。

- [ ] **Step 2: 真跑** — 记每条边 × 每 regime 的分桶夏普供文档。
- [ ] **Step 3: Commit** — `research(data): carry/crowd_contra 按 FRED 宏观 regime 分桶(REAL_FRED_MACRO)`

---

### FM4: 全门绿 + 诚实记结论

**Files:** Modify CLAUDE.md、`docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`、PROJECT_STATE、记忆 `macro-regime.md`

- [ ] **Step 1: 全门绿** — `PYTHONPATH=. python scripts/validate.py` → `VALIDATE PASSED`(macro_regime.py 纯函数、fred_fetch 在 tools/ 出 gate;若治理报 services 网络先查)。
- [ ] **Step 2: 记结论** — CLAUDE.md 加 macro 条目 + regime 分桶结论;gap 文档数据缺口标「宏观已接」;PROJECT_STATE + 记忆 `macro-regime.md`(FRED 已接、regime 依赖结论、诚实边界、股票仍待 BYO/ALFRED 待办)。
- [ ] **Step 3: Commit** — `test+docs: FRED 宏观 regime 结论记入活文档(数据缺口:宏观已接)`

---

## 自审

- **Spec coverage:** spec §3.1/3.2/3.3/FM4 ↔ FM1/2/3/4;诚实边界(§2)在 FM1 kt=et + FM2 docstring + FM3 regime 标注 + FM4 文档四处落地。
- **Placeholder scan:** FM1/FM2 完整代码;FM3 给明确组装指令(复用 _book_series + latest_as_of,先 Read)。
- **Type consistency:** `tercile_labels`/`sign_regime`/`bucket_returns` 在 FM2 定义与测试/FM3 引用一致;FeatureValue(feature_id/instrument_id/event_time/knowledge_time/value)与 store API 吻合。
- **读优先红线:** FM1 先 Read fundamental_store;FM3 先 Read scenario_tail_research + store。
- **诚实:** 经济发布类 + PIT regime 显式划出;fetcher fail-closed;信用维覆盖不足标注。
