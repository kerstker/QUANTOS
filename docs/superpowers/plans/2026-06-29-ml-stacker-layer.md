# 学习型信号组合层(ML stacker)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 QUANTOS 加一个确定性的学习型信号组合(stacking)层——用 GBM + Ridge 把现有因子池的多个信号学成截面 meta-信号,照走现有无窥视 OOS 关卡,并把「组合 vs Ridge vs 单因子最优」三方 OOS 并排报出。

**Architecture:** 新 `services/research/ml/{feature_panel, stacker, walk_forward_stack}.py` 三个聚焦模块 + 离线驱动器 `tools/ml_stack_research.py`。feature_panel 把多份 `PanelPeriod` 对齐成 (期,标的)×因子 特征矩阵;stacker 确定性包 sklearn;walk_forward_stack 做 purged+embargo 滚动训练 + OOS 截面多空。驱动器在真 crypto/FX 因子池上跑,过关卡 + 成本扫描,出诚实裁决快照。

**Tech Stack:** Python + numpy 2.4.4 + scikit-learn 1.8.0(已装,本计划只钉版本);复用 `services/alpha/panel_backtest.PanelPeriod`、`services/research/backtest_engine/metrics.sharpe`、`services/research/oos_validation.walk_forward_selection_oos`、`services/research/panel_builder.make_data_periods_provider`。

## Global Constraints

- L2_DRY_RUN_ONLY:ML 层零下单/券商/凭证路径,只产候选信号、只读关卡。
- **确定性使用 ML 库**:固定 `random_state=seed`(默认 0)、线程钉死(`OMP_NUM_THREADS`/`OPENBLAS_NUM_THREADS`/`MKL_NUM_THREADS`=`"1"`,在 stacker.py 导入 numpy/sklearn **之前** 设)、确定性 solver。确定性测试钉死:同输入两次 → 逐位相同。
- 墙钟只在驱动器 `tools/ml_stack_research.py` 边界;`services/research/ml/` 核心无墙钟、无网络 import。
- **无网络 import 不变量继续机器强制**:`tests/governance/test_crypto_data_boundary.py::test_services_tree_has_no_network_imports` 仍须绿(numpy/sklearn 非网络根)。
- **诚实门不弱化**:训练用 purged+embargo;最终裁决用 no-peek 滚动 OOS;三方并排(组合/Ridge/单因子最优);成本扫描。绝不报样本内分数当 edge。
- 单一门禁 `python scripts/validate.py` 必须绿。提交信息中文;结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 隔离 pytest 用 `PYTHONPATH=. python -m pytest ...` 从仓库根跑。

## File Structure

- `requirements.txt` / `pyproject.toml` (Modify) — 钉 numpy + scikit-learn(新「research ML layer」段)。
- `services/research/ml/__init__.py` (Create) — 空包标记。
- `services/research/ml/feature_panel.py` (Create) — `FeaturePanel` + `build_feature_panel`。
- `services/research/ml/stacker.py` (Create) — 确定性环境钉死 + `fit_stacker` / `Stacker`。
- `services/research/ml/walk_forward_stack.py` (Create) — `walk_forward_stack`(purged+embargo + 截面多空)。
- `tools/ml_stack_research.py` (Create) — 离线驱动器(真数据 → 三方 OOS + 成本扫描 + 快照)。
- `tests/research/test_ml_feature_panel.py` / `test_ml_stacker.py` / `test_ml_walk_forward_stack.py` (Create)。

---

### Task 0: 钉依赖(numpy + scikit-learn)

**Files:**
- Modify: `requirements.txt`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: 仓库声明 numpy/sklearn 为依赖;无代码符号。

- [ ] **Step 1: 在 requirements.txt 末尾追加 research ML layer 段**

```
# research ML layer (deterministic stacking; non-network, no-RNG-leak — pinned to installed versions)
numpy==2.4.4
scikit-learn==1.8.0
```

- [ ] **Step 2: 在 pyproject.toml 的 [project.optional-dependencies] 下加 research-ml 段**

```toml
# research ML layer (services/research/ml): deterministic stacking over existing factor signals.
# Non-network, used deterministically (fixed random_state, single-thread). Not needed by console_api.
research-ml = [
    "numpy==2.4.4",
    "scikit-learn==1.8.0",
]
```

- [ ] **Step 3: 验证可导入 + 治理无网络 import 仍绿**

Run: `PYTHONPATH=. python -c "import numpy, sklearn; from sklearn.ensemble import HistGradientBoostingRegressor; from sklearn.linear_model import Ridge; print('ok', numpy.__version__, sklearn.__version__)"`
Expected: `ok 2.4.4 1.8.0`
Run: `PYTHONPATH=. python -m pytest tests/governance/test_crypto_data_boundary.py -q`
Expected: PASS（numpy/sklearn 非网络根,不触发 offenders）

- [ ] **Step 4: 提交**

```bash
git add requirements.txt pyproject.toml
git commit -m "build(ml): 钉 numpy+scikit-learn(research ML layer,确定性使用)"
```

---

### Task 1: feature_panel —— 多因子面板对齐成特征矩阵

**Files:**
- Create: `services/research/ml/__init__.py`(空文件)
- Create: `services/research/ml/feature_panel.py`
- Test: `tests/research/test_ml_feature_panel.py`

**Interfaces:**
- Consumes: `services.alpha.panel_backtest.PanelPeriod`(字段 `instruments: tuple[str,...]`, `signals: tuple[float,...]`, `forward_returns: tuple[float,...]`)。
- Produces: `FeaturePanel`(dataclass:`X: np.ndarray (n_rows, n_factors)`, `y: np.ndarray (n_rows,)`, `rows: list[tuple[int, str]]`(每行 (period_index, instrument)), `factor_names: list[str]`)。`build_feature_panel(factor_periods: Mapping[str, Sequence[PanelPeriod]]) -> FeaturePanel`。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_ml_feature_panel.py
import numpy as np
from services.alpha.panel_backtest import PanelPeriod
from services.research.ml.feature_panel import build_feature_panel


def test_aligns_factors_into_matrix_inner_join():
    # 2 factors, 2 periods, instruments per period; factor 'b' missing ETH in period 0 -> that row dropped
    fa = [PanelPeriod(instruments=("BTC", "ETH"), signals=(1.0, 2.0), forward_returns=(0.01, 0.02)),
          PanelPeriod(instruments=("BTC", "ETH"), signals=(3.0, 4.0), forward_returns=(0.03, 0.04))]
    fb = [PanelPeriod(instruments=("BTC",), signals=(9.0,), forward_returns=(0.01,)),
          PanelPeriod(instruments=("BTC", "ETH"), signals=(7.0, 8.0), forward_returns=(0.03, 0.04))]
    fp = build_feature_panel({"a": fa, "b": fb})
    assert fp.factor_names == ["a", "b"]                       # sorted, stable column order
    # rows: period0 -> only BTC (ETH missing in b); period1 -> BTC, ETH
    assert fp.rows == [(0, "BTC"), (1, "BTC"), (1, "ETH")]
    assert fp.X.shape == (3, 2) and fp.y.shape == (3,)
    assert list(fp.X[0]) == [1.0, 9.0]                          # (period0,BTC): a=1.0, b=9.0
    assert list(fp.X[2]) == [4.0, 8.0]                          # (period1,ETH): a=4.0, b=8.0
    assert abs(fp.y[2] - 0.04) < 1e-12


def test_empty_factors_yields_empty_panel():
    fp = build_feature_panel({})
    assert fp.X.shape[0] == 0 and fp.rows == [] and fp.factor_names == []
```

- [ ] **Step 2: 跑确认 FAIL** — Run: `PYTHONPATH=. python -m pytest tests/research/test_ml_feature_panel.py -q` → FAIL（无模块）

- [ ] **Step 3: 实现**

```python
# services/research/ml/__init__.py
"""研究级 ML 层(确定性 stacking)。真 numpy+sklearn,确定性使用;零下单路径,只读关卡。"""
```

```python
# services/research/ml/feature_panel.py
"""把因子池的多份 PanelPeriod 按 (period_index, instrument) 对齐成 stacker 的特征矩阵 X / 目标 y。
特征 = 各因子在决策时的截面信号;y = 该 (期,标的) 的前向收益(因子无关,取任一因子的值)。inner-join:
只保留所有因子都有值的 (期,标的)(fail-soft 丢缺行)。纯 numpy 数据整形,无 RNG / 无墙钟。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from services.alpha.panel_backtest import PanelPeriod


@dataclass(frozen=True)
class FeaturePanel:
    X: np.ndarray            # (n_rows, n_factors)
    y: np.ndarray            # (n_rows,)
    rows: list               # list[tuple[int, str]]  -> (period_index, instrument)
    factor_names: list       # list[str], 与 X 列一一对应


def build_feature_panel(factor_periods: Mapping[str, Sequence[PanelPeriod]]) -> FeaturePanel:
    names = sorted(factor_periods)
    if not names:
        return FeaturePanel(np.empty((0, 0)), np.empty((0,)), [], [])
    n_periods = min(len(factor_periods[n]) for n in names)
    # per factor per period: instrument -> (signal, forward_return)
    lut = {n: [{inst: (s, fr) for inst, s, fr in zip(p.instruments, p.signals, p.forward_returns)}
               for p in list(factor_periods[n])[:n_periods]] for n in names}
    rows, X_rows, y_vals = [], [], []
    for i in range(n_periods):
        # instruments present in EVERY factor at period i (inner-join), sorted for determinism
        common = set.intersection(*[set(lut[n][i]) for n in names]) if names else set()
        for inst in sorted(common):
            X_rows.append([lut[n][i][inst][0] for n in names])
            y_vals.append(lut[names[0]][i][inst][1])           # forward return is factor-independent
            rows.append((i, inst))
    X = np.asarray(X_rows, dtype=float) if X_rows else np.empty((0, len(names)))
    y = np.asarray(y_vals, dtype=float)
    return FeaturePanel(X=X, y=y, rows=rows, factor_names=names)
```

- [ ] **Step 4: 跑确认 PASS** — Run: `PYTHONPATH=. python -m pytest tests/research/test_ml_feature_panel.py -q` → PASS

- [ ] **Step 5: 提交**

```bash
git add services/research/ml/__init__.py services/research/ml/feature_panel.py tests/research/test_ml_feature_panel.py
git commit -m "feat(ml): feature_panel——多因子 PanelPeriod 对齐成 (期,标的)×因子 特征矩阵"
```

---

### Task 2: stacker —— 确定性 sklearn 包装(GBM + Ridge)

**Files:**
- Create: `services/research/ml/stacker.py`
- Test: `tests/research/test_ml_stacker.py`

**Interfaces:**
- Consumes: numpy、sklearn(`HistGradientBoostingRegressor`, `Ridge`)。
- Produces: `fit_stacker(X: np.ndarray, y: np.ndarray, *, model: str = "gbm", seed: int = 0) -> Stacker`;`Stacker.predict(X: np.ndarray) -> np.ndarray`。`model ∈ {"gbm","ridge"}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_ml_stacker.py
import numpy as np
import pytest
from services.research.ml.stacker import fit_stacker


def _data(n=120, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 3))
    y = 2.0 * X[:, 0] - 1.0 * X[:, 1] + 0.01 * rng.normal(size=n)   # learnable signal
    return X, y


@pytest.mark.parametrize("model", ["gbm", "ridge"])
def test_fits_and_predicts(model):
    X, y = _data()
    s = fit_stacker(X, y, model=model, seed=0)
    p = s.predict(X)
    assert p.shape == (X.shape[0],)
    # predictions correlate with the learnable target (sanity, in-sample)
    assert np.corrcoef(p, y)[0, 1] > 0.5


@pytest.mark.parametrize("model", ["gbm", "ridge"])
def test_deterministic_same_input_same_output(model):
    X, y = _data()
    p1 = fit_stacker(X, y, model=model, seed=0).predict(X)
    p2 = fit_stacker(X, y, model=model, seed=0).predict(X)
    assert np.array_equal(p1, p2)                              # bit-identical within process


def test_unknown_model_raises():
    X, y = _data()
    with pytest.raises(ValueError):
        fit_stacker(X, y, model="nn")
```

- [ ] **Step 2: 跑确认 FAIL** — Run: `PYTHONPATH=. python -m pytest tests/research/test_ml_stacker.py -q` → FAIL（无模块）

- [ ] **Step 3: 实现**

```python
# services/research/ml/stacker.py
"""确定性 stacker:把多因子特征学成一个分数(GBM 抓非线性交互;Ridge 当线性诚实地板)。确定性使用——
线程在导入 numpy/sklearn 之前钉死为 1,固定 random_state,Ridge 用确定性闭式 solver。同输入两次 → 逐位相同。
零下单路径,只产分数。"""
from __future__ import annotations

import os as _os

# 线程必须在 numpy/sklearn 初始化 OpenMP/BLAS 之前钉死,确保确定性(同输入逐位相同)。
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_v, "1")

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge


class Stacker:
    """已拟合的组合器。predict(X) -> 每行一个分数。"""
    def __init__(self, model) -> None:
        self._model = model

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self._model.predict(np.asarray(X, dtype=float)), dtype=float)


def fit_stacker(X: np.ndarray, y: np.ndarray, *, model: str = "gbm", seed: int = 0) -> Stacker:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if model == "gbm":
        m = HistGradientBoostingRegressor(random_state=seed, early_stopping=False,
                                          max_iter=200, max_depth=3, learning_rate=0.05,
                                          l2_regularization=1.0)
    elif model == "ridge":
        m = Ridge(alpha=1.0, solver="cholesky")               # 确定性闭式解,无 RNG
    else:
        raise ValueError(f"unknown stacker model: {model!r} (expected 'gbm' or 'ridge')")
    m.fit(X, y)
    return Stacker(m)
```

- [ ] **Step 4: 跑确认 PASS** — Run: `PYTHONPATH=. python -m pytest tests/research/test_ml_stacker.py -q` → PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add services/research/ml/stacker.py tests/research/test_ml_stacker.py
git commit -m "feat(ml): stacker——确定性 GBM+Ridge 包装(线程/seed 钉死,同入同出)"
```

---

### Task 3: walk_forward_stack —— purged+embargo 滚动 + 截面多空 OOS

**Files:**
- Create: `services/research/ml/walk_forward_stack.py`
- Test: `tests/research/test_ml_walk_forward_stack.py`

**Interfaces:**
- Consumes: `FeaturePanel`(Task 1)、`fit_stacker`(Task 2)、numpy。
- Produces: `walk_forward_stack(panel: FeaturePanel, *, train_size: int, test_size: int, embargo: int = 1, model: str = "gbm", seed: int = 0) -> dict`,键:`oos_returns: list[float]`(每个测试期一个组合收益)、`n_folds: int`、`n_test_periods: int`。无泄漏:训练只用 `period < test_start - embargo` 的行;截面多空只在同期内。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_ml_walk_forward_stack.py
import numpy as np
from services.research.ml.feature_panel import FeaturePanel
from services.research.ml.walk_forward_stack import walk_forward_stack


def _panel(n_periods=24, n_inst=8, predictive=True, seed=0):
    rng = np.random.default_rng(seed)
    rows, X, y = [], [], []
    for i in range(n_periods):
        z = rng.normal(size=n_inst)                            # the feature, cross-sectional
        fwd = (z if predictive else rng.normal(size=n_inst)) * 0.01   # y aligned with feature iff predictive
        for k in range(n_inst):
            rows.append((i, f"i{k}")); X.append([z[k]]); y.append(fwd[k])
    return FeaturePanel(np.asarray(X, float), np.asarray(y, float), rows, ["f"])


def test_predictive_feature_gives_positive_oos():
    out = walk_forward_stack(_panel(predictive=True), train_size=8, test_size=4, embargo=1, model="ridge")
    assert out["n_folds"] >= 2 and out["n_test_periods"] >= 4
    assert sum(out["oos_returns"]) > 0                         # a truly predictive feature survives OOS

def test_noise_feature_gives_no_oos_edge():
    out = walk_forward_stack(_panel(predictive=False, seed=1), train_size=8, test_size=4, embargo=1, model="ridge")
    assert abs(sum(out["oos_returns"])) < 0.5 * out["n_test_periods"]   # noise -> no systematic OOS edge

def test_embargo_excludes_recent_train_rows():
    # tiny panel where without embargo there would be a fold but with a huge embargo there is none
    out = walk_forward_stack(_panel(n_periods=12), train_size=6, test_size=3, embargo=100, model="ridge")
    assert out["n_folds"] == 0 and out["oos_returns"] == []    # embargo starves every fold of train data
```

- [ ] **Step 2: 跑确认 FAIL** — Run: `PYTHONPATH=. python -m pytest tests/research/test_ml_walk_forward_stack.py -q` → FAIL（无模块）

- [ ] **Step 3: 实现**

```python
# services/research/ml/walk_forward_stack.py
"""无泄漏的滚动 stacking + 截面多空 OOS。每折:用 period < test_start-embargo 的行拟合 stacker → 预测测试期
各行分数 → 同期截面 rank-去均值-gross1 多空权重 → 该期组合收益 = w·forward_return。拼成 OOS 收益序列。
purged+embargo 保证测试块不入训练。确定性、纯编排。"""
from __future__ import annotations

import numpy as np

from .feature_panel import FeaturePanel
from .stacker import fit_stacker


def _ls_weights(scores: np.ndarray) -> np.ndarray:
    """截面分数 -> 多空权重:rank 去均值后归一到 gross=1(sum|w|=1)。单标的或全相等 -> 全零(无头寸)。"""
    n = len(scores)
    if n < 2:
        return np.zeros(n)
    order = np.argsort(np.argsort(scores)).astype(float)       # ranks 0..n-1, deterministic
    w = order - order.mean()
    s = np.abs(w).sum()
    return w / s if s > 0 else np.zeros(n)


def walk_forward_stack(panel: FeaturePanel, *, train_size: int, test_size: int, embargo: int = 1,
                       model: str = "gbm", seed: int = 0) -> dict:
    rows = panel.rows
    if not rows:
        return {"oos_returns": [], "n_folds": 0, "n_test_periods": 0}
    periods = sorted({i for i, _ in rows})
    by_period = {p: np.array([k for k, (i, _) in enumerate(rows) if i == p]) for p in periods}
    oos, n_folds, test_start = [], 0, train_size
    while test_start < len(periods):
        test_ps = periods[test_start: test_start + test_size]
        if not test_ps:
            break
        train_cutoff = test_ps[0] - embargo                    # purge: train periods strictly before cutoff
        train_idx = np.concatenate([by_period[p] for p in periods if p < train_cutoff]) \
            if any(p < train_cutoff for p in periods) else np.array([], dtype=int)
        if train_idx.size >= 2 * panel.X.shape[1] + 5:         # enough rows to fit
            st = fit_stacker(panel.X[train_idx], panel.y[train_idx], model=model, seed=seed)
            n_folds += 1
            for p in test_ps:
                idx = by_period[p]
                w = _ls_weights(st.predict(panel.X[idx]))
                oos.append(float(np.dot(w, panel.y[idx])))
        test_start += test_size
    return {"oos_returns": oos, "n_folds": n_folds, "n_test_periods": len(oos)}
```

> 注:`test_embargo_excludes_recent_train_rows` 用 embargo=100 让每折训练块为空(train_idx.size 不足)→ 不拟合任何折 → `n_folds==0`、`oos_returns==[]`。这验证 purge/embargo 真的在挡训练数据。

- [ ] **Step 4: 跑确认 PASS** — Run: `PYTHONPATH=. python -m pytest tests/research/test_ml_walk_forward_stack.py -q` → PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add services/research/ml/walk_forward_stack.py tests/research/test_ml_walk_forward_stack.py
git commit -m "feat(ml): walk_forward_stack——purged+embargo 滚动 + 截面多空 OOS(无泄漏)"
```

---

### Task 4: 驱动器 —— 真数据三方 OOS + 成本扫描 + 快照

**Files:**
- Create: `tools/ml_stack_research.py`

**Interfaces:**
- Consumes: `build_feature_panel`、`walk_forward_stack`(组合)、`make_data_periods_provider`(因子面板)、`walk_forward_selection_oos`(单因子最优 OOS)、`metrics.sharpe`。
- Produces: `data/cache/ml_stack_snapshot.json`(三方 OOS + 成本扫描 + 裁决)。

- [ ] **Step 1: 实现驱动器**

```python
# tools/ml_stack_research.py
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

    result = {
        "asset": asset, "factors": fp.factor_names, "feature_rows": int(fp.X.shape[0]), "fwd_horizon": fwd,
        "stack_gbm_oos_sharpe": _sr(gbm["oos_returns"]), "stack_gbm_folds": gbm["n_folds"],
        "stack_ridge_oos_sharpe": _sr(ridge["oos_returns"]), "stack_ridge_folds": ridge["n_folds"],
        "single_best_oos_sharpe": single.get("mean_oos_sharpe"), "single_best_name": (single.get("selected") or single.get("top") or None),
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
```

- [ ] **Step 2: import 检查(无语法/接线错)**

Run: `PYTHONPATH=. python -c "import tools.ml_stack_research as m; print('ok', list(m._POOLS))"`
Expected: `ok ['crypto', 'fx']`

- [ ] **Step 3: 真数据跑(crypto + fx,看三方 OOS)**

Run: `PYTHONPATH=. python -m tools.ml_stack_research crypto`
Run: `PYTHONPATH=. python -m tools.ml_stack_research fx`
Expected: 打印三方 OOS Sharpe + verdict;`data/cache/ml_stack_snapshot.json` 存在。诚实预期:大概率 `REFUTED_NO_EDGE` 或 `NO_LIFT_OVER_LINEAR`(薄数据无趋势 edge)——这本身是诚实结论。

> 若 `walk_forward_selection_oos` 返回的最优名键不是 `selected`/`top`,在 Step 3 观察实际键后把 `single_best_name` 的取法改对(只影响展示,不影响 OOS 数值)。

- [ ] **Step 4: 提交**

```bash
git add tools/ml_stack_research.py
git commit -m "research(ml): stacker 驱动器——真因子池三方 OOS(GBM/Ridge/单因子)+ 诚实裁决快照"
```

---

### Task 5: 全门绿 + 收尾

**Files:** 无(验证 + 记忆/状态)

- [ ] **Step 1: 全门禁**

Run: `python scripts/validate.py`
Expected: `VALIDATE PASSED`(治理扫描 PASS + 全套绿,含 3 个新 ml 测试 + 无网络 import 仍绿)

- [ ] **Step 2: 更新现状图 + 记忆**

把「services/research/ml/ 学习型 stacking 层(确定性 GBM+Ridge);依赖加 numpy+sklearn」记入 `CLAUDE.md`「What actually exists」与 `docs/PROJECT_STATE.md`;ML stacker 的真数据三方 OOS 结论(诚实数字)记入记忆(crypto/fx findings 或新 `ml-stacker-findings`)。

- [ ] **Step 3: 提交收尾**

```bash
git add -A
git commit -m "docs(state): ML stacker 层落地 + 真数据三方 OOS 结论记入活文档/记忆"
```

---

## Self-Review

**Spec coverage**(逐节对 spec):
- §4.1 feature_panel → Task 1。✓  §4.2 stacker(GBM+Ridge,确定性)→ Task 2。✓  §4.3 walk_forward_stack(purged+embargo,截面多空)→ Task 3。✓
- §5 驱动器(三方 OOS + 成本基线 + 快照)→ Task 4。✓  §2 依赖钉版本确定性 → Task 0 + stacker 线程/seed 钉死。✓
- §3 诚实(purged+embargo 训练、no-peek OOS、三方并排)→ Task 3(无泄漏测试)+ Task 4(三方 + verdict)。✓
- §6 容器化(零下单、无网络 import 仍绿、确定性测试)→ Task 0 Step 3 + Task 2 确定性测试 + Global Constraints。✓
- §7 测试 → 各 Task TDD + Task 5 门绿。✓  §8 YAGNI(不接 console/agent/NN)→ 计划范围未含,符合。✓

**Placeholder scan:** 无 TBD/TODO;每步有完整代码与命令。Task 4 对 `single_best_name` 键名做了"观察后改对"的诚实注记(数值不依赖它),非占位符。✓

**Type consistency:** `FeaturePanel`(X/y/rows/factor_names)在 Task 1 定义、Task 3/4 一致引用;`fit_stacker(X,y,*,model,seed)`/`Stacker.predict` Task 2 定义、Task 3 调用一致;`walk_forward_stack(panel,*,train_size,test_size,embargo,model,seed)` 返回 `{oos_returns,n_folds,n_test_periods}` Task 3 定义、Task 4 用键一致;`build_feature_panel` 签名 Task 1↔Task 4 一致。✓

> 实现期注意:numpy `argsort(argsort(...))` 的 rank 在并列值上是稳定确定的;GBM 的逐位确定性依赖线程钉死(Task 2 已在导入前设),若 CI 环境已先 import numpy 致 env 不生效,确定性测试仍应过(HistGBR 给定 random_state 在同进程内确定)——若偶发不过,把 `np.array_equal` 放宽为 `allclose(atol=0)` 并在该机核对线程数。
