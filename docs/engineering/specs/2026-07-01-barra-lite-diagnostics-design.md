# Barra-lite 结构化风险诊断设计

> **⚠ 已被 `c48e210`(2026-07-02,`services/portfolio/barra_lite.py`)以独立实现方式落地取代
> ——本文档仅存设计动机与参考价值,不要按本文档字面内容重新实现或覆盖已上线代码。**
> 2026-07-08 审计核实:`c48e210` 与本文档是同一条线性提交链(spec→plan→实现,同作者,
> 间隔约 14 小时),但代码层面是**独立重写**(`zscore(dict)` 而非本文档的 `Sequence` 签名、
> 百分位 winsorize 而非 σ-winsorize、缺失值静默置 0 而非本文档主张的 raise)——若未来任何
> agent 被指向执行本文档"Task 2 Step 1"里的内嵌源码,会用不兼容签名覆盖已上线、已被
> `tools/rebuild_snapshots.py` 依赖的实现。本文档 §7(若存在)开放的未来扩展条目(console
> 面板、拥挤门接组合构建等)请拆成独立新 spec 去跟踪,不要复活本文档本身去施工。
>
> 状态:设计已与用户确认(2026-07-01,方案 A:诊断优先)。  
> 边界:`L2_DRY_RUN_ONLY`。只做研究诊断与 dry-run 解释,不改组合权重,不接实盘/券商/凭证。

## 1. 目标

当前 QUANTOS 已有 `FactorRiskModel`、`services/portfolio/factor_risk.py`、约束优化器和真实 dry-run 流水线,但缺少一层“结构化风险因子载荷构造”。组合目前主要靠样本协方差和执行现实回测解释风险,缺少类似 Barra-lite 的风格/宏观/流动性/拥挤暴露视角。

本项目新增一个只读诊断层:

- 把 carry、crowd_contra、combo/risk-parity 等已有研究 book 的风险暴露结构化。
- 生成 `FactorRiskModel` 可消费的载荷、因子协方差、特异风险,并输出人可读报告。
- 落一份本地快照 `data/cache/barra_lite_snapshot.json`,供后续 console 或研究文档读取。
- 不驱动自动晋级、调仓、降仓、下单或风险门放行。

## 2. 非目标

- 不改变 `dry_run_pipeline`、`run_multi_period_dry_run` 或任何策略权重。
- 不把诊断暴露接入 pre-trade gate、research_runner 白名单或 alpha lifecycle 自动晋级。
- 不伪造 crypto 行业/板块分类;没有可靠映射时标注 `UNKNOWN` 或跳过该类因子。
- 不把宏观 regime 暴露当成 PIT 可交易择时信号;第一版只做回顾性解释。
- 不新增网络入口;网络仍只允许在既有 `tools/` 抓取器边界。
- 不接 broker/CTP/FIX SDK,不读取或提交任何真实凭证。

## 3. 架构

### 3.1 核心模块

新增 `services/portfolio/barra_lite.py`。核心保持纯函数、确定性、无 IO/RNG/墙钟/网络。

建议公开函数:

- `zscore(values) -> list[float | None]`
- `winsorize(values, limit=3.0) -> list[float | None]`
- `corr_exposure(target_returns, factor_returns, min_overlap=20) -> float | None`
- `build_barra_lite_model(alpha_returns, descriptors, factor_cov=None, min_overlap=20) -> FactorRiskModel`
- `barra_lite_report(model, alpha_ids, weights) -> dict`

其中 `descriptors` 是调用方准备好的结构化输入,例如:

```python
{
    "market_beta": {"carry": 0.12, "crowd_contra": -0.04},
    "liquidity": {"carry": -0.30, "crowd_contra": 0.18},
    "volatility": {"carry": 0.25, "crowd_contra": -0.10},
    "carry": {"carry": 1.00, "crowd_contra": 0.05},
    "crowding": {"carry": 0.10, "crowd_contra": 1.00},
}
```

核心模块只验证和转换这些输入:

- 因子名非空且唯一。
- 每个 alpha 对每个因子都有有限载荷;缺失时 fail-closed。
- `factor_cov` 缺省时使用对角协方差,但必须 PSD。
- `specific_var` 来自 alpha 收益未被结构化因子解释后的残差方差;无法估计时 fail-closed,不静默填 0。
- 输出为既有 `quantos.contracts.factor.FactorRiskModel`。

### 3.2 工具驱动器

新增 `tools/barra_lite_diagnostics.py`。它是 gate 外只读研究驱动器,负责读本地缓存和已有 book 组装逻辑:

- 复用 carry/crowd_contra/risk-parity 现有面板和逐周收益。
- 计算策略级描述符:
  - `market_beta`: 对 BTC 或等权 crypto market proxy 的相关/回归暴露。
  - `liquidity`: 用 ADV/成交额分位做暴露,低流动性偏正则代表容量脆弱。
  - `volatility`: 对高波动币或高波动 regime 的暴露。
  - `carry`: 对 funding/carry sleeve 的暴露。
  - `crowding`: 对 `global_ls`/OI 拥挤反向信号的暴露。
  - `macro_vix`、`macro_rates`: 从已有 `macro_regime_snapshot.json` 或 `macro_fred.db` 派生,标注为回顾性诊断。
- 调用核心模块生成 `FactorRiskModel` 和报告。
- 写入 `data/cache/barra_lite_snapshot.json`。

快照建议结构:

```json
{
  "provenance": "BARra_LITE_DIAGNOSTIC_OFFLINE",
  "readiness": "L2_DRY_RUN_ONLY",
  "generated_from": ["dry_run.db", "macro_regime_snapshot.json"],
  "honesty_note": "diagnostic only; no trading, no promotion, no weight change",
  "factors": ["market_beta", "liquidity", "volatility", "carry", "crowding"],
  "models": {
    "carry": {"loadings": {}, "specific_var": 0.0},
    "crowd_contra": {"loadings": {}, "specific_var": 0.0}
  },
  "portfolio_reports": {
    "crowd_contra": {
      "exposures": {},
      "factor_var": 0.0,
      "specific_var": 0.0,
      "top_risks": []
    }
  }
}
```

实际数值由工具真跑生成;文档和代码不得手填研究结论数字。

## 4. 数据流

```text
existing dry-run / research caches
  -> tools/barra_lite_diagnostics.py
  -> descriptor maps per alpha/book
  -> services/portfolio/barra_lite.py
  -> FactorRiskModel + risk_decomposition
  -> data/cache/barra_lite_snapshot.json
```

核心不读 `data/cache`。工具层可以读:

- `data/cache/dry_run.db`
- `data/cache/macro_regime_snapshot.json`
- 既有 crypto/funding/OI 本地缓存

若任何输入缺失,工具层必须把对应暴露标注为 unavailable 或 fail-closed,不能默认为“风险为 0”。

## 5. 错误处理

- 缺少必需 alpha 收益序列:raise `ValueError`。
- overlap 不足:该暴露为 `None`;若该暴露是必需项,构建模型 fail-closed。
- 非有限数、负特异方差、非 PSD 因子协方差:由 `FactorRiskModel` 拒绝。
- 宏观数据覆盖不足:报告 `coverage` 和 `diagnostic_only`,不用于模型核心约束。
- sector/industry mapping 不存在:标注 `UNKNOWN`,不编造行业暴露。

## 6. 测试

新增 `tests/portfolio/test_barra_lite.py`:

- `zscore`/`winsorize` 确定性与 None 处理。
- `corr_exposure` 对齐 overlap,样本不足返回 `None`。
- `build_barra_lite_model` 生成的 `FactorRiskModel` 维度正确且 PSD 校验通过。
- 缺失载荷、非有限值、负特异风险 fail-closed。
- `barra_lite_report` 的 `factor_var + specific_var == total_var`。
- 同输入重复运行输出完全一致。

新增 `tests/governance/test_barra_lite_boundary.py`:

- `services/portfolio/barra_lite.py` 不含网络 import。
- 不含 broker/CTP/FIX/交易所 SDK import。
- 不含下单/转账/凭证读取关键字。

工具层可加轻量 smoke test 或通过现有治理扫描器覆盖;真实数据驱动器不进入单元测试强依赖。

## 7. 后续扩展

第一版只读诊断稳定后,后续可以独立设计:

- console 页面或 Portfolio 页新增 Barra-lite 诊断面板。
- 拥挤门接进组合构建(Rank #3),但需单独 spec。
- factor-neutral 组合实验,但需单独 spec 和明确不进入默认 dry-run。
- BYO-CSV 的行业/板块映射接入,但不能在缺数据时伪造 sector。

## 8. 自检

- 占位符扫描:无 TBD/TODO;所有缺失数据均以 fail-closed 或 explicit unavailable 表达。
- 范围检查:本设计是一个单一实现计划,限定为核心诊断模块 + gate 外驱动器 + 测试。
- 一致性检查:复用既有 `FactorRiskModel`、`factor_risk.risk_decomposition`、现有 dry-run/research 缓存;不重写风险模型。
- 安全检查:全程 L2;无 live、无 broker、无 credential、无下单、无自动晋级。
