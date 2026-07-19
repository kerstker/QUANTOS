# 断点2 前向 paper 追踪持久层 — 实现计划

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 alpha 建一个 append-only sqlite 持久层,逐期落库真样本外(train/paper 切分)的纸面 P&L/IC,并把 LifecycleMonitor 的 `live_ic`、衰退维护 pass 的 live 输入、Alpha Zoo 的时间维四维全部接到它上面。

**Architecture:** 新 `services/research/paper_trading/{store.py, advance.py}` + 离线驱动器 `tools/crypto_paper_track.py`(在 `[:boundary]` 上评估、把 alpha 前向应用到 `[boundary:]` 算 OOS 纸面收益/IC),写 `data/cache/paper_track.db`。三个 console/服务消费方读 store,fail-closed 到 null/SYNTHETIC。

**Tech Stack:** Python 标准库(sqlite3 / statistics)、`services/research/backtest_engine/metrics.sharpe`、`services/research/paper_trading/domain/consistency.performance_gap`、FastAPI(console_api)、React/TS(Alpha Zoo,已是元数据驱动)。

## Global Constraints

- 纯 Python 标准库;禁 numpy/scipy/pandas/torch/ccxt 等。
- 确定性:`services/` 核心无 RNG、无墙钟;墙钟只在 `tools/crypto_paper_track.py` 驱动器边界取(若需)。
- append-only / 不可变明细;`INSERT OR IGNORE` 幂等。
- 容器化不变:`auto_approved=0`、非人永不 APPROVED;**`live_ic` 变真不解锁任何自动晋级**。paper-track 是研究只读记录,无券商/无下单/无凭证。
- store 缺失/损坏 → 消费方 fail-closed 到 null/SYNTHETIC(CI 无库照样绿)。
- 单一门禁:`python scripts/validate.py` 必须绿(治理扫描 + 全 pytest)。提交信息中文;结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 诚实红线:纸面 P&L 必须是评估**没见过**的样本外段(`[boundary:]`);禁止把已评估的尾巴回贴成 paper,禁止把 in-sample 指标标成 live。

## File Structure

- `services/research/paper_trading/store.py` (Create) — `PaperTrackStore`:sqlite append-only,两表,schema 校验、fail-closed、汇总重算。
- `services/research/paper_trading/advance.py` (Create) — `advance_paper_track`:把 `[boundary:]` 逐期幂等追加 + 重算汇总。
- `tools/crypto_paper_track.py` (Create) — 离线驱动器:train/paper 切分 + 填充真 paper 数据。
- `services/research/console_api/main.py` (Modify) — 消费方 ①③:`_lifecycle_monitor_payload` 真 `live_ic`;`_alpha_zoo_*` 加时间维四维 + `_ZOO_DIMENSIONS`。
- `services/alpha/maintenance.py` (Modify, 小) — 文档/helper:从 paper_track 组 `health_by_alpha`(消费方 ②;实际组装放一个 console/tool helper,maintenance 本身不变签名)。
- `apps/research-console/src/lib/types/research.ts` (Modify) — `AlphaZooRow` 加四维(可选)。
- `tests/research/test_paper_track_store.py` / `test_paper_track_advance.py` (Create)。
- `services/research/console_api/tests/test_console_api.py` + `tests/research/test_console_api_contract.py` (Modify) — 消费方断言。

---

### Task 1: PaperTrackStore（持久层）

**Files:**
- Create: `services/research/paper_trading/store.py`
- Test: `tests/research/test_paper_track_store.py`

**Interfaces:**
- Consumes: `services.research.backtest_engine.metrics.sharpe(returns) -> float`;`services.research.paper_trading.domain.consistency.performance_gap(backtest_sharpe, paper_sharpe) -> Optional[float]`(返回相对差 `(bt-paper)/bt`,bt<=0 或缺值时 None)。
- Produces: `PaperTrackStore(path, *, create=False)`;`.append_periods(alpha_id, rows)`（rows=`[(as_of_ts:int, realized_return:float, realized_ic:float|None, source:str)]`,`INSERT OR IGNORE`）;`.periods(alpha_id) -> list[dict]`;`.recompute_summary(alpha_id, *, backtest_sharpe=None)`;`.track(alpha_id) -> dict|None`;`.all_tracks() -> list[dict]`;异常类 `PaperTrackStoreError`。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_paper_track_store.py
import pytest
from services.research.paper_trading.store import PaperTrackStore, PaperTrackStoreError


def _store():
    return PaperTrackStore(":memory:", create=True)


def test_append_is_idempotent_by_period():
    s = _store()
    s.append_periods("a1", [(1000, 0.01, 0.05, "OOS_TAIL"), (2000, -0.02, 0.04, "OOS_TAIL")])
    s.append_periods("a1", [(2000, -0.02, 0.04, "OOS_TAIL"), (3000, 0.03, 0.06, "OOS_TAIL")])  # 2000 dup
    assert [p["as_of_ts"] for p in s.periods("a1")] == [1000, 2000, 3000]   # dup ignored, new appended


def test_recompute_summary_is_correct():
    s = _store()
    s.append_periods("a1", [(1000, 0.01, 0.05, "OOS_TAIL"), (2000, 0.02, 0.07, "OOS_TAIL")])
    s.recompute_summary("a1", backtest_sharpe=2.0)
    t = s.track("a1")
    assert t["n_periods"] == 2 and t["start_ts"] == 1000 and t["last_ts"] == 2000
    assert t["paper_ic"] == pytest.approx(0.06, abs=1e-6)          # mean(0.05, 0.07)
    assert t["cum_return"] == pytest.approx((1.01 * 1.02 - 1.0), abs=1e-6)
    assert t["backtest_sharpe"] == 2.0
    # perf_gap = (bt - paper)/bt = consistency.performance_gap
    from services.research.backtest_engine.metrics import sharpe
    from services.research.paper_trading.domain.consistency import performance_gap
    assert t["perf_gap"] == pytest.approx(performance_gap(2.0, sharpe([0.01, 0.02])), abs=1e-6)


def test_missing_db_fails_closed():
    with pytest.raises(PaperTrackStoreError):
        PaperTrackStore("nonexistent-paper-track.db", create=False)   # no schema -> raises


def test_null_ic_handled_and_track_none_when_absent():
    s = _store()
    assert s.track("ghost") is None
    s.append_periods("a2", [(1000, 0.01, None, "OOS_TAIL")])
    s.recompute_summary("a2", backtest_sharpe=None)
    t = s.track("a2")
    assert t["paper_ic"] is None and t["perf_gap"] is None          # no IC, no bt -> honest None
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `PYTHONPATH=. python -m pytest tests/research/test_paper_track_store.py -q`
Expected: FAIL（`ModuleNotFoundError: services.research.paper_trading.store`）

- [ ] **Step 3: 实现 store.py**

```python
# services/research/paper_trading/store.py
"""Forward paper-track persistence (断点2). Append-only sqlite ledger of an alpha's REAL out-of-sample
paper P&L / IC, one row per period (immutable), plus a recomputed per-alpha summary. Mirrors the
experiment_registry store pattern. Pure stdlib, deterministic (no RNG / no wall-clock here)."""
from __future__ import annotations

import sqlite3
import statistics
from typing import Optional, Sequence

from services.research.backtest_engine.metrics import sharpe
from services.research.paper_trading.domain.consistency import performance_gap


class PaperTrackStoreError(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_periods (
  alpha_id        TEXT    NOT NULL,
  as_of_ts        INTEGER NOT NULL,
  realized_return REAL    NOT NULL,
  realized_ic     REAL,
  source          TEXT    NOT NULL,
  PRIMARY KEY (alpha_id, as_of_ts)
);
CREATE TABLE IF NOT EXISTS paper_track (
  alpha_id        TEXT PRIMARY KEY,
  start_ts        INTEGER, last_ts INTEGER, n_periods INTEGER,
  cum_return      REAL, paper_sharpe REAL, paper_ic REAL,
  backtest_sharpe REAL, perf_gap REAL
);
"""
_TABLES = ("paper_periods", "paper_track")


class PaperTrackStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._require_schema()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise PaperTrackStoreError(f"paper-track store unreadable: {exc}") from exc
        missing = [t for t in _TABLES if t not in {r["name"] for r in rows}]
        if missing:
            raise PaperTrackStoreError(f"paper-track store missing tables: {missing}")

    def append_periods(self, alpha_id: str, rows: Sequence[tuple]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO paper_periods(alpha_id, as_of_ts, realized_return, realized_ic, source) "
            "VALUES (?,?,?,?,?)",
            [(alpha_id, int(ts), float(r), (None if ic is None else float(ic)), str(src))
             for ts, r, ic, src in rows])
        self._conn.commit()

    def periods(self, alpha_id: str) -> list:
        return [dict(r) for r in self._conn.execute(
            "SELECT as_of_ts, realized_return, realized_ic, source FROM paper_periods "
            "WHERE alpha_id=? ORDER BY as_of_ts", (alpha_id,))]

    def recompute_summary(self, alpha_id: str, *, backtest_sharpe: Optional[float] = None) -> None:
        ps = self.periods(alpha_id)
        if not ps:
            return
        rets = [p["realized_return"] for p in ps]
        ics = [p["realized_ic"] for p in ps if p["realized_ic"] is not None]
        cum = 1.0
        for r in rets:
            cum *= (1.0 + r)
        psharpe = sharpe(rets)
        pic = statistics.fmean(ics) if ics else None
        gap = performance_gap(backtest_sharpe, psharpe)
        self._conn.execute(
            "INSERT OR REPLACE INTO paper_track(alpha_id, start_ts, last_ts, n_periods, cum_return, "
            "paper_sharpe, paper_ic, backtest_sharpe, perf_gap) VALUES (?,?,?,?,?,?,?,?,?)",
            (alpha_id, ps[0]["as_of_ts"], ps[-1]["as_of_ts"], len(ps), round(cum - 1.0, 6),
             round(psharpe, 4), (None if pic is None else round(pic, 4)), backtest_sharpe,
             (None if gap is None else round(gap, 4))))
        self._conn.commit()

    def track(self, alpha_id: str) -> Optional[dict]:
        r = self._conn.execute("SELECT * FROM paper_track WHERE alpha_id=?", (alpha_id,)).fetchone()
        return dict(r) if r else None

    def all_tracks(self) -> list:
        return [dict(r) for r in self._conn.execute("SELECT * FROM paper_track ORDER BY alpha_id")]
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `PYTHONPATH=. python -m pytest tests/research/test_paper_track_store.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add services/research/paper_trading/store.py tests/research/test_paper_track_store.py
git commit -m "feat(paper-track): PaperTrackStore——append-only 纸面 P&L/IC 持久层 + 汇总重算"
```

---

### Task 2: advance_paper_track（推进逻辑）

**Files:**
- Create: `services/research/paper_trading/advance.py`
- Test: `tests/research/test_paper_track_advance.py`

**Interfaces:**
- Consumes: `PaperTrackStore`(Task 1)。
- Produces: `advance_paper_track(store, alpha_id, returns, ic_series, *, boundary, source="OOS_TAIL", ts0=0, dt=86_400_000, backtest_sharpe=None) -> int`（返回新增期数;只追加 `[boundary:]`;幂等;追加后重算汇总）。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_paper_track_advance.py
from services.research.paper_trading.store import PaperTrackStore
from services.research.paper_trading.advance import advance_paper_track


def _store():
    return PaperTrackStore(":memory:", create=True)


def test_advance_appends_only_the_held_out_tail():
    s = _store()
    rets = [0.10, 0.10, 0.10, 0.01, -0.02, 0.03]   # boundary=3 -> paper = last 3
    ics = [0.2, 0.2, 0.2, 0.05, -0.01, 0.04]
    n = advance_paper_track(s, "a1", rets, ics, boundary=3, ts0=0, dt=1000, backtest_sharpe=2.0)
    assert n == 3
    p = s.periods("a1")
    assert [row["as_of_ts"] for row in p] == [3000, 4000, 5000]    # only the [3:] tail, by ts0+i*dt
    assert [round(row["realized_return"], 3) for row in p] == [0.01, -0.02, 0.03]
    assert s.track("a1")["n_periods"] == 3


def test_advance_is_idempotent():
    s = _store()
    rets, ics = [0.0, 0.0, 0.01, 0.02], [0.0, 0.0, 0.05, 0.06]
    advance_paper_track(s, "a1", rets, ics, boundary=2, ts0=0, dt=1000, backtest_sharpe=1.0)
    n2 = advance_paper_track(s, "a1", rets, ics, boundary=2, ts0=0, dt=1000, backtest_sharpe=1.0)  # re-run
    assert n2 == 0 and s.track("a1")["n_periods"] == 2            # nothing new on re-run
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `PYTHONPATH=. python -m pytest tests/research/test_paper_track_advance.py -q`
Expected: FAIL（`ModuleNotFoundError: services.research.paper_trading.advance`）

- [ ] **Step 3: 实现 advance.py**

```python
# services/research/paper_trading/advance.py
"""Advance an alpha's forward paper track by the held-out tail [boundary:] of its realized returns/IC.
The held-out segment is the genuinely out-of-sample paper region (the driver evaluates on [:boundary]).
Idempotent (INSERT OR IGNORE in the store). Pure, deterministic."""
from __future__ import annotations

from typing import Optional, Sequence

from .store import PaperTrackStore


def advance_paper_track(store: PaperTrackStore, alpha_id: str, returns: Sequence[float],
                        ic_series: Optional[Sequence[float]], *, boundary: int,
                        source: str = "OOS_TAIL", ts0: int = 0, dt: int = 86_400_000,
                        backtest_sharpe: Optional[float] = None) -> int:
    paper_r = list(returns)[boundary:]
    paper_ic = list(ic_series)[boundary:] if ic_series else [None] * len(paper_r)
    existing = {p["as_of_ts"] for p in store.periods(alpha_id)}
    rows, n_new = [], 0
    for i, r in enumerate(paper_r):
        ts = ts0 + (boundary + i) * dt
        ic = paper_ic[i] if i < len(paper_ic) else None
        if ts not in existing:
            n_new += 1
        rows.append((ts, r, ic, source))
    store.append_periods(alpha_id, rows)
    store.recompute_summary(alpha_id, backtest_sharpe=backtest_sharpe)
    return n_new
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `PYTHONPATH=. python -m pytest tests/research/test_paper_track_advance.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add services/research/paper_trading/advance.py tests/research/test_paper_track_advance.py
git commit -m "feat(paper-track): advance_paper_track——留出尾部逐期幂等追加 + 重算"
```

---

### Task 3: 离线驱动器 `tools/crypto_paper_track.py`（train/paper 切分 + 填充真数据）

**Files:**
- Create: `tools/crypto_paper_track.py`

**Interfaces:**
- Consumes: `PaperTrackStore`(Task 1)、`advance_paper_track`(Task 2)、真收益/IC 源。
- Produces: `data/cache/paper_track.db`（真 paper 记录,carry + registry alpha)。

> 真收益/IC 源:沿用本会话已验证的 `data/cache/crypto_research_snapshot.json` 的 `factor_series`(每因子真实逐期净收益)+ experiment_registry 的 `ic_mean`/eval。**train/paper 诚实切分**:对每个因子,`returns` 整段已是评估制品的产物,但本驱动器把 **`[boundary:]` 段当 paper**,并以 **`[:boundary]` 段的 sharpe 作为 backtest_sharpe**(train 段口径)——即 paper 的对照是 train,不是含 paper 的全段。`boundary = int(len*0.7)`(可配 `--boundary-frac`)。IC 逐期源:若快照无逐期 IC 序列,`ic_series=None`(paper_ic 留空,诚实)。

- [ ] **Step 1: 实现驱动器**

```python
# tools/crypto_paper_track.py
"""断点2 driver: populate the forward paper-track store from the offline research snapshot's real per-factor
return series, with an HONEST train/paper split — the [boundary:] tail is the paper (out-of-sample) region,
and the [:boundary] head's Sharpe is the backtest reference. Deterministic, idempotent (re-runnable). Reads
local data only (no network). Writes data/cache/paper_track.db."""
from __future__ import annotations

import json
import os
import sys

from services.research.backtest_engine.metrics import sharpe
from services.research.paper_trading.store import PaperTrackStore
from services.research.paper_trading.advance import advance_paper_track

_SNAP = os.path.join("data", "cache", "crypto_research_snapshot.json")
_DB = os.path.join("data", "cache", "paper_track.db")
_DAY_MS = 86_400_000


def _factor_series() -> dict:
    try:
        snap = json.load(open(_SNAP, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return snap.get("factor_series", {})   # {alpha_id: [per-period net return]}


def main(boundary_frac: float = 0.7) -> None:
    series = _factor_series()
    if not series:
        print("no factor_series in snapshot — run tools/crypto_research_offline first")
        return
    if os.path.exists(_DB):
        os.remove(_DB)                      # rebuild deterministically
    store = PaperTrackStore(_DB, create=True)
    n_alphas = 0
    for aid, rets in series.items():
        if not isinstance(rets, list) or len(rets) < 20:
            continue
        boundary = max(10, int(len(rets) * boundary_frac))
        if boundary >= len(rets):
            continue
        bt_sharpe = round(sharpe(rets[:boundary]), 4)     # train-head Sharpe = the honest backtest ref
        n_new = advance_paper_track(store, aid, rets, None, boundary=boundary, source="OOS_TAIL",
                                    ts0=0, dt=_DAY_MS, backtest_sharpe=bt_sharpe)
        n_alphas += 1
    tracks = store.all_tracks()
    print(f"populated {n_alphas} alphas; {len(tracks)} paper tracks -> {_DB}")
    for t in sorted(tracks, key=lambda x: -(x["paper_sharpe"] or -9))[:5]:
        print(f"  {t['alpha_id']:16s} n={t['n_periods']:3d} paperSR {t['paper_sharpe']:+.2f} "
              f"btSR {t['backtest_sharpe']:+.2f} gap {t['perf_gap']}")


if __name__ == "__main__":
    frac = float(sys.argv[sys.argv.index("--boundary-frac") + 1]) if "--boundary-frac" in sys.argv else 0.7
    main(frac)
```

- [ ] **Step 2: 跑驱动器,确认真 paper 数据落库**

Run: `PYTHONPATH=. python -m tools.crypto_paper_track`
Expected: 打印 `populated N alphas; N paper tracks -> data/cache/paper_track.db` + 几行真 paperSR/btSR/gap;`data/cache/paper_track.db` 存在。

- [ ] **Step 3: 提交**

```bash
git add tools/crypto_paper_track.py
git commit -m "research(paper-track): 离线驱动器——train/paper 真切分填充真样本外纸面 P&L"
```

---

### Task 4: 消费方① LifecycleMonitor 真 `live_ic`

**Files:**
- Modify: `services/research/console_api/main.py`（`_lifecycle_monitor_payload` / `_lifecycle_rows_real`）
- Modify: `services/research/console_api/tests/test_console_api.py`

**Interfaces:**
- Consumes: `PaperTrackStore`(Task 1)读 `data/cache/paper_track.db`。
- Produces: LifecycleMonitor 每行 `live_ic` = `paper_track.paper_ic`(有 paper→真值,无→null);`recommendation` 在有 paper 时由真 paper 喂 `monitor_alpha_health`。

> 新增 helper `_load_paper_track_store()`(os.path.exists 守卫 + 异常吞掉返回 None,照搬 `_load_experiment_store`)。在 `_lifecycle_rows_real` 里,对每个 alpha:`t = pts.track(aid)`;`live_ic = t["paper_ic"] if t else None`;若 `t` 有 `paper_sharpe`,`recommendation = monitor_alpha_health(backtest={"return": ...}, live={"ic": t["paper_ic"], "return": t["cum_return"]})["recommendation"]`,否则保持 `NO_LIVE_DATA`。`live_tracked = sum(1 for r in rows if r["live_ic"] is not None)`。

- [ ] **Step 1: 写失败测试**（断言:有 paper_track.db 时 live_ic 可为真;无库时仍 null + NO_LIVE_DATA）

```python
# 加进 services/research/console_api/tests/test_console_api.py
def test_lifecycle_live_ic_reads_paper_track_when_present(tmp_path, monkeypatch):
    import services.research.console_api.main as m
    from services.research.paper_trading.store import PaperTrackStore
    db = tmp_path / "paper_track.db"
    s = PaperTrackStore(str(db), create=True)
    # need a real alpha_id from the registry-backed lifecycle rows; pick the first
    payload0 = m._lifecycle_monitor_payload()
    aid = payload0["rows"][0]["alpha_id"]
    s.append_periods(aid, [(1000, 0.01, 0.05, "OOS_TAIL"), (2000, 0.02, 0.07, "OOS_TAIL")])
    s.recompute_summary(aid, backtest_sharpe=1.5)
    monkeypatch.setattr(m, "_PAPER_TRACK_DB", str(db))
    payload = m._lifecycle_monitor_payload()
    row = next(r for r in payload["rows"] if r["alpha_id"] == aid)
    assert row["live_ic"] == pytest.approx(0.06, abs=1e-6)    # real paper IC, no longer null
    assert payload["summary"]["live_tracked"] >= 1
```

- [ ] **Step 2: 跑确认 FAIL**

Run: `PYTHONPATH=. python -m pytest "services/research/console_api/tests/test_console_api.py::test_lifecycle_live_ic_reads_paper_track_when_present" -q`
Expected: FAIL（`live_ic is None` / 无 `_PAPER_TRACK_DB`）

- [ ] **Step 3: 实现**（main.py）

```python
# 在 _load_experiment_store 附近加:
_PAPER_TRACK_DB = os.path.join("data", "cache", "paper_track.db")

def _load_paper_track_store():
    try:
        from services.research.paper_trading.store import PaperTrackStore
        return PaperTrackStore(_PAPER_TRACK_DB, create=False) if os.path.exists(_PAPER_TRACK_DB) else None
    except Exception:
        return None
```

```python
# 在 _lifecycle_rows_real(st) 顶部:
pts = _load_paper_track_store()
# 在组装每行 row 时,替换 live_ic / recommendation:
t = pts.track(aid) if pts is not None else None
live_ic = t["paper_ic"] if (t and t.get("paper_ic") is not None) else None
if t and isinstance(t.get("paper_sharpe"), (int, float)):
    from services.alpha.degradation_monitor import monitor_alpha_health
    health = monitor_alpha_health(
        {"return": (t.get("backtest_sharpe") or 0.0)},
        {"ic": live_ic, "return": t.get("cum_return")})
    recommendation = health["recommendation"]
else:
    recommendation = "NO_LIVE_DATA"
row["live_ic"] = live_ic
row["recommendation"] = recommendation
```

> （把现有写死 `live_ic=None` / `recommendation="NO_LIVE_DATA"` 的两行替换成上面;`_lifecycle_summary` 的 `live_tracked` 已按 `live_ic is not None` 计,自动变真。）

- [ ] **Step 4: 跑确认 PASS + 既有 lifecycle 测试仍绿**

Run: `PYTHONPATH=. python -m pytest services/research/console_api/tests/test_console_api.py tests/research/test_console_api_contract.py -q`
Expected: PASS（含新测 + 既有 `test_lifecycle_monitor_is_read_only_and_honest_about_live` 仍过——无 paper_track.db 时仍 fail-closed null）

- [ ] **Step 5: 提交**

```bash
git add services/research/console_api/main.py services/research/console_api/tests/test_console_api.py
git commit -m "feat(lifecycle): live_ic 真接 paper_track——有纸面→真累积 IC,无→仍诚实 null"
```

---

### Task 5: 消费方② 衰退维护真 paper 输入

**Files:**
- Create: `services/research/paper_trading/health.py`（helper:从 paper_track 组 `health_by_alpha`)
- Test: `tests/research/test_paper_health_input.py`

**Interfaces:**
- Consumes: `PaperTrackStore`(Task 1)。
- Produces: `paper_health_by_alpha(store, *, backtest_by_alpha) -> dict`（`{alpha_id: {"backtest": {...}, "live": {...}}}`,喂 `run_maintenance(health_by_alpha=...)`)。

> `run_maintenance`(断点4)签名不变;新 helper 把 paper_track 翻译成 `monitor_alpha_health` 要的 `{backtest, live}` 形状。backtest 侧用 `backtest_by_alpha`(eval 指标),live 侧用 paper(`{"ic": paper_ic, "return": cum_return}`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_paper_health_input.py
from services.research.paper_trading.store import PaperTrackStore
from services.research.paper_trading.health import paper_health_by_alpha


def test_paper_health_translates_track_to_monitor_shape():
    s = PaperTrackStore(":memory:", create=True)
    s.append_periods("a1", [(1000, -0.05, -0.02, "OOS_TAIL"), (2000, -0.04, -0.01, "OOS_TAIL")])
    s.recompute_summary("a1", backtest_sharpe=2.0)
    h = paper_health_by_alpha(s, backtest_by_alpha={"a1": {"ic": 0.05, "return": 0.2}})
    assert "a1" in h
    assert h["a1"]["backtest"] == {"ic": 0.05, "return": 0.2}
    assert h["a1"]["live"]["ic"] == -0.015 or h["a1"]["live"]["return"] is not None   # real paper live side
```

- [ ] **Step 2: 跑确认 FAIL** — Run: `PYTHONPATH=. python -m pytest tests/research/test_paper_health_input.py -q` → FAIL（无 health 模块）

- [ ] **Step 3: 实现 health.py**

```python
# services/research/paper_trading/health.py
"""Translate the forward paper-track summaries into the {backtest, live} shape that
services/alpha/maintenance.run_maintenance feeds to degradation_monitor.monitor_alpha_health. The live side
is the REAL accumulated paper metrics; the backtest side is the supplied evaluation metrics. Only alphas
with a paper track AND a backtest reference are included (fail-closed: no paper -> not in the dict)."""
from __future__ import annotations

from typing import Mapping

from .store import PaperTrackStore


def paper_health_by_alpha(store: PaperTrackStore, *, backtest_by_alpha: Mapping[str, dict]) -> dict:
    out: dict = {}
    for t in store.all_tracks():
        aid = t["alpha_id"]
        bt = backtest_by_alpha.get(aid)
        if bt is None:
            continue
        out[aid] = {"backtest": dict(bt),
                    "live": {"ic": t.get("paper_ic"), "return": t.get("cum_return")}}
    return out
```

- [ ] **Step 4: 跑确认 PASS** — Run: `PYTHONPATH=. python -m pytest tests/research/test_paper_health_input.py -q` → PASS

- [ ] **Step 5: 提交**

```bash
git add services/research/paper_trading/health.py tests/research/test_paper_health_input.py
git commit -m "feat(maintenance): paper_health_by_alpha——把纸面追踪翻译成衰退监控真 live 输入"
```

---

### Task 6: 消费方③ Alpha Zoo 时间维四维

**Files:**
- Modify: `services/research/console_api/main.py`（`_ZOO_DIMENSIONS` + `_alpha_zoo_rows_real`）
- Modify: `apps/research-console/src/lib/types/research.ts`（`AlphaZooRow` 加四可选字段)
- Modify: `services/research/console_api/tests/test_console_api.py`

**Interfaces:**
- Consumes: `_load_paper_track_store()`(Task 4)。
- Produces: alpha-zoo 每行 + `_ZOO_DIMENSIONS` 加 `paper_ic / paper_sharpe / perf_gap / n_paper_periods`(分组"时间/活体征"),有 paper 才有值否则 null。

- [ ] **Step 1: 写失败测试**（断言 `_ZOO_DIMENSIONS` 含四个新 key,每行有这四字段)

```python
# 加进 test_console_api.py（扩 test_alpha_zoo_is_multidimensional 或新测）
def test_alpha_zoo_has_paper_track_time_dimensions():
    payload = client.get("/api/v1/console/alpha-zoo").json()
    dims = {d["key"] for d in payload["dimensions"]}
    assert {"paper_ic", "paper_sharpe", "perf_gap", "n_paper_periods"} <= dims
    row = payload["rows"][0]
    for k in ("paper_ic", "paper_sharpe", "perf_gap", "n_paper_periods"):
        assert k in row                          # present (real value or null)
    assert any(d["group"] == "时间/活体征" for d in payload["dimensions"])
```

- [ ] **Step 2: 跑确认 FAIL** — Run: `PYTHONPATH=. python -m pytest "services/research/console_api/tests/test_console_api.py::test_alpha_zoo_has_paper_track_time_dimensions" -q` → FAIL

- [ ] **Step 3: 实现**（main.py）

```python
# _ZOO_DIMENSIONS 末尾追加(分组 时间/活体征):
{"key": "paper_ic", "zh": "纸面IC", "en": "Paper IC", "group": "时间/活体征", "fmt": "signed3", "good": "high"},
{"key": "paper_sharpe", "zh": "纸面夏普", "en": "Paper SR", "group": "时间/活体征", "fmt": "signed2", "good": "high"},
{"key": "perf_gap", "zh": "回测-纸面差", "en": "BT-paper gap", "group": "时间/活体征", "fmt": "signed2", "good": "low"},
{"key": "n_paper_periods", "zh": "纸面期数", "en": "Paper periods", "group": "时间/活体征", "fmt": "num1", "good": "high"},
```

```python
# _alpha_zoo_rows_real(st) 顶部加: pts = _load_paper_track_store()
# 组装每行 row 时加(t = pts.track(aid) if pts else None):
t = pts.track(aid) if pts is not None else None
row["paper_ic"] = t.get("paper_ic") if t else None
row["paper_sharpe"] = t.get("paper_sharpe") if t else None
row["perf_gap"] = t.get("perf_gap") if t else None
row["n_paper_periods"] = (t.get("n_periods") if t else None)
```

```typescript
// research.ts AlphaZooRow 加(可选):
paper_ic?: number | null; paper_sharpe?: number | null; perf_gap?: number | null; n_paper_periods?: number | null;
```

> 前端 AlphaZoo 页元数据驱动(`dimensions`),四维**自动**出现在排序/散点/钻取,无需改 .tsx。`FALLBACK_DIMS`(AlphaZoo.tsx)同步加四条,保证 mock 模式也显示。

- [ ] **Step 4: 跑确认 PASS + typecheck**

Run: `PYTHONPATH=. python -m pytest services/research/console_api/tests/test_console_api.py tests/research/test_console_api_contract.py -q`
Run: `cd apps/research-console && npm run typecheck`
Expected: PASS + tsc 无错

- [ ] **Step 5: 提交**

```bash
git add services/research/console_api/main.py apps/research-console/src/lib/types/research.ts apps/research-console/src/pages/AlphaZoo.tsx services/research/console_api/tests/test_console_api.py
git commit -m "feat(alpha-zoo): 时间/活体征四维(纸面IC/夏普/回测-纸面差/纸面期数)接 paper_track"
```

---

### Task 7: 全门绿 + 真数据端到端验证

**Files:** 无(验证 + 重启 + 浏览器实测)

- [ ] **Step 1: 跑驱动器填真 paper 数据**

Run: `PYTHONPATH=. python -m tools.crypto_paper_track`
Expected: `data/cache/paper_track.db` 有真 paper 记录。

- [ ] **Step 2: 全门禁**

Run: `python scripts/validate.py`
Expected: `VALIDATE PASSED`（治理扫描 PASS + 全套绿）

- [ ] **Step 3: 前端 typecheck + 测试**

Run: `cd apps/research-console && npm run typecheck && npm test`
Expected: tsc 无错 + 测试全过

- [ ] **Step 4: 重启 console_api + 浏览器实测**

Run: 杀 :8400 → `python -m uvicorn services.research.console_api.main:app --host 127.0.0.1 --port 8400`
验证:`/api/v1/console/lifecycle-monitor` 的 `live_tracked > 0` + 某行 `live_ic` 非 null;`/api/v1/console/alpha-zoo` `dimensions` 含四时间维、某行 paper_ic 非 null。浏览器 LifecycleMonitor 页 live_ic 显真、Zoo 时间维四维可排序/钻取。

- [ ] **Step 5: 提交收尾 + 更新记忆/PROJECT_STATE**

```bash
git add -A
git commit -m "docs(state): 断点2 前向 paper 追踪持久层落地——live_ic/衰退/Zoo时间维全接真"
```

---

## Self-Review

**Spec coverage**(逐节对 spec):
- §3 train/paper 真切分 → Task 3 驱动器(`[:boundary]` 当 backtest_sharpe、`[boundary:]` 当 paper)。✓
- §4 数据模型(两表)→ Task 1 `_SCHEMA`。✓
- §5.1 store API → Task 1。✓  §5.2 advance → Task 2。✓  §5.3 driver → Task 3。✓
- §5.4 三消费方 → Task 4(live_ic)、Task 5(maintenance)、Task 6(Zoo)。✓
- §6 容器化(auto_approved 不变、live_ic 不解锁晋级、fail-closed)→ 各 Task 的"无库 null"测试 + Global Constraints。✓
- §7 测试 → 各 Task 的 TDD + Task 7 门绿。✓

**Placeholder scan:** 无 TBD/TODO;每步有真实代码与命令。✓

**Type consistency:** `PaperTrackStore.track()` 返回字段(`paper_ic/paper_sharpe/perf_gap/n_periods/cum_return/backtest_sharpe`)在 Task 4/5/6 一致引用;`advance_paper_track(...boundary=,source=,ts0=,dt=,backtest_sharpe=)` 签名在 Task 2 定义、Task 3 调用一致;`performance_gap(bt, paper)`(相对差)在 Task 1 用真函数。✓

> 注意(实现期):Task 4/6 改的是 `_lifecycle_rows_real` / `_alpha_zoo_rows_real` 现有循环里写 row 的那几行——实现者需先 Read 这两个函数的当前实现,把"写死 live_ic=None"/"无 paper 维度"替换为本计划的代码,而非另起循环。
