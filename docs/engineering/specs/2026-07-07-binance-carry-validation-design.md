# QUANTOS — Binance 真实数据 Carry 验证 · 设计规格

> **日期**: 2026-07-07  
> **目标**: 用 Binance 真实永续合约数据重跑 carry alpha 全量验证 → 判断信号真实性 → 建设标准化管道  
> **方案**: 混合路径（先快速验证 1 天 → 有效则标准化管道 3 天）  
> **前置**: 用户持有 Binance 只读 API Key · carry 是唯一验证过的可部署 lead

---

## 一、背景

### 1.1 为什么需要这个验证

carry alpha 的所有量化指标（Net Sharpe ~2.1, 年化 ~47%, 7/7 年全正）建立在**合成数据**上：
- CoinGecko OHLC（无成交量）
- 合成 bar（非真实 tick/OHLCV）
- 可能存在的幸存者偏差

在投入任何实盘资金前，必须用**真实 Binance 数据**重新验证。

### 1.2 方案选择：混合路径

| 阶段 | 内容 | 时间 |
|------|------|:--:|
| **第一阶段** | 复用现有 tools/ 脚本快速拉取数据 + 用 crypto_carry_walkforward.py 重跑验证 | 1 天 |
| **第二阶段** | 建设标准化 Binance 数据管道 + PIT Store + Alpha 工厂评估 | 3 天 |

---

## 二、第一阶段：快速验证

### 2.1 数据流

```
Binance REST API
  ├── GET /klines          → binance_klines_fetch.py     → 日频 OHLCV
  ├── GET /fundingRate     → binance_funding_fetch.py    → 资金费率历史
  └── GET /openInterest    → binance_oi_metrics_fetch.py → 持仓量
       ↓
align_to_pit_periods()      → 按 event_time 对齐截面
filter_liquid_universe()    → 按 OI/成交量过滤流动子集
       ↓
crypto_carry_walkforward.py → 全量 Walk-Forward 回测
       ↓
对比报告: 合成数据 vs Binance 真实数据
```

### 2.2 核心验证指标

| 指标 | 合成数据 baseline | Binance 真实数据 |
|------|:--:|:--:|
| 覆盖交易对 | ~115 永续 | Binance 实时 USDT 永续 |
| 覆盖时间 | ~3-7 年 | 同上 |
| Net Sharpe | ~2.1（建模） | 待验证 |
| 年化收益 | ~47%（建模） | 待验证 |
| 7/7 年全正 | ✅ | 待验证 |
| IC 均值 | ≈0（尾部收益） | 待验证 |
| Walk-Forward OOS | 待确认 | 待验证 |
| 滑点敏感性 | 5bp→2.1, 20bp→1.4 | 待验证 |

### 2.3 风险点

- **幸存者偏差**: Binance 历史上已下架永续合约，数据是否可获取？
- **资金费率数据完整性**: 早期交易对可能缺少 funding rate 历史
- **流动性阈值**: 不同时期的活跃交易对数量差异大

### 2.4 成功标准

| 判定 | 条件 |
|:--:|------|
| ✅ 绿灯 | Net Sharpe > 1.0, Walk-Forward 全正, IC 方向一致 → 进入第二阶段 |
| ⚠️ 黄灯 | Net Sharpe 0.5-1.0, 部分 W-F 负, IC 减弱 → 数据/模型需校准 |
| ❌ 红灯 | Net Sharpe < 0.5, W-F 多数负, IC 反转 → signals 不可行, 止损 |

---

## 三、第二阶段：标准化管道

### 3.1 新文件

| 文件 | 说明 | 行数估算 |
|------|------|:--:|
| `services/data/binance_fetcher.py` | Binance REST API 封装，限速 1200 req/min，指数退避重试，返回原始 JSON | ~120 |
| `services/data/binance_ingestion.py` | 标准化为 `Bar` 契约 + funding rate 数据结构，按 event_time 排序去重 | ~100 |
| `services/data/binance_universe.py` | USDT 永续全量列表 + 退市/新上市检测 + 流动性过滤 | ~80 |

### 3.2 修改文件

| 文件 | 变更 |
|------|------|
| `services/data/ingestion.py` | 新增 `ingest_binance_daily()` — 调用上述三模块写入 PIT Store |

### 3.3 架构

```
Binance REST API (网络边界)
  └── binance_fetcher.py   (HTTP + 限速 + 重试)
         └── binance_ingestion.py (标准化 → Bar 契约)
                └── binance_universe.py (交易对筛选 + 退市检测)
                       └── PIT Store (pit_store.py + universe_store.py + versioning.py)
                              └── Alpha 工厂 evaluator.py (标准评估流程)
                                     └── 验证报告
```

### 3.4 安全性（不破坏现有 fail-closed ）

- API Key 从环境变量读取（不硬编码）
- 只用只读端点（零下单、零账户、零转账）
- `SafetyConfig` 五开关保持默认 SAFE
- `services/` 零网络 import（网络只在 tools/）

### 3.5 与现有架构的契合

| 现有组件 | 复用方式 |
|----------|----------|
| `pit_store.py` | `store_bar()` / `as_of()` — 写入 Bar 契约 |
| `universe_store.py` | `set_universe()` / `members_as_of()` — 按时间写入活跃交易对 |
| `versioning.py` | 数据集快照 + hash — 每次写入自动版本化 |
| `evaluator.py` | 输入端换成 PitStore 读取 |
| `factory.py` | `batch_pbo()` 复用 |

### 3.6 交付物

| 交付物 | 说明 |
|--------|------|
| 数据完整性报告 | 交易对数量、时间跨度、缺失率 |
| 幸存者偏差审计 | 退市交易对历史数据完整性 |
| Carry 全量回测报告 | Net Sharpe / IC / Walk-Forward / Regime 分析 |
| 对比差异分析 | 合成数据 vs Binance 真实数据的指标差异 |

---

## 四、不做的（YAGNI）

| 项 | 原因 |
|------|------|
| WebSocket 实时行情 | 日频验证不需要 tick 级 |
| 多交易所接入 | 先验证单交易所 |
| 前端可视化 | carry 研究已有 crypto-carry-strategy 页面 |
| 生产部署 | L2 only |
| 订单执行 | 不在本阶段范围 |

---

**文档版本**: v1.0  
**状态**: 等待审查
