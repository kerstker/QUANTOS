# MEXC 现货对接全景文档

状态:**human-ready(代码面就绪,待人工步骤)** · 日期:2026-07-23 · 准备度:L2_DRY_RUN_ONLY(默认)

> 2026-07-23 接线现状(Part 1 已落地):① MEXC klines 已注册为 `tools/live_data_refresh.py`
> 第 5 个幂等源(落 `crypto_mexc.db` + `mexc_btc_klines.json`);② `btc_spot_trend` 干跑主口径
> 已切 MEXC PIT(CoinGecko 以 `_cg` 后缀保留对照),并接入 paper_track.db 前向持久层;
> ③ `_VENUES["mexc"]`/`_STRATEGIES["btc_spot_trend"]` 已注册进 `tools/live_track_advance.py`;
> ④ §10 凭证加载器 `tools/mexc_credentials.py` + `verify_key_permissions.py` MEXC 分支已落地;
> ⑤ 驱动层现货现金门 + 现货口径风控限额(单名满仓 long 不被期货形默认误杀)已落地;
> ⑥ `--arm-live` 人工 CLI arming 路径已落地(ops API 固定 argv 永不携带,治理测试钉死);
> ⑦ 只读首验工具 `tools/mexc_readonly_smoke.py` 已就绪(**人工执行**,产出
> `evidence/mexc_mainnet_smoke.json`;未跑前 readiness 诚实报 UNVERIFIED_MAINNET_RESPONSE);
> ⑧ ops API 现货 dashboard 分支 + 首单 runbook(`docs/runbooks/mexc-live-runbook.md`)已就绪;
> ⑨ fake-transport pipeline 级集成测试(全链/轮询/取消竞态/崩溃恢复)已落地。
> **剩余人工步骤**:放 key → 权限校验 → 只读 smoke → 手写 mandate+attestation →
> (研究治理决定:btc_spot_trend 纳入 paper cycle)→ `--arm-live` ≤$100 首单 → 对账。
> AI 绝不执行这些步骤中的任何一步。

本文档描述 QUANTOS 对接 MEXC 交易所(BTC 现货、零手续费活动)的完整设计:
架构、适配器协议、签名机制、现货 vs 合约语义差异、数据流、成本模型含义、
实盘毕业路径与治理边界。代码实现由并行 worker 建设,本文档是契约与边界。

---

## 1. 目标与动机

- **业务目标**:MEXC 对 BTC/USDT 现货提供零挂单/吃单手续费(zero-fee)活动,
  是低成本验证「研究 → 干跑 → 小额真实 → 规模化」全链路的理想首个实盘场地。
- **策略目标**:`btc_spot_trend`(BTC 现货趋势策略)以 MEXC 日线 klines 为数据源,
  经 PIT 存储、干跑流水线检验后,走人工 mandate 的小额真实毕业路径。
- **工程目标**:复用已联调的 `live/` 执行栈(Binance/Bybit 期货 testnet 模板),
  新增第三个 `VenueAdapter` 实现,证明执行栈的场地无关性。

---

## 2. 架构总览(ASCII)

```
                        ┌────────────────────────────────────────────┐
                        │              MEXC Spot API v3              │
                        │          https://api.mexc.com              │
                        │  /api/v3/{klines,exchangeInfo,order,...}   │
                        └───────▲─────────────────────▲──────────────┘
              公共行情(GET,无签名)│                     │私有(签名:X-MEXC-APIKEY
                                │                     │  + timestamp + HMAC-SHA256)
        ┌───────────────────────┴──┐   ┌──────────────┴───────────────────┐
        │ tools/mexc_klines_fetch  │   │ live/exchange/mexc_spot.py       │
        │ (抓取器,网络在 tools/    │   │ (VenueAdapter 实现,网络只允许   │
        │  出 governance gate)     │   │  在 live/exchange/)              │
        └───────────┬──────────────┘   └──────────────┬───────────────────┘
                    │ ingest_provider_rows            │ VenueAdapter Protocol
                    ▼ (dedup + 未来数据守卫)           │ (submit/fetch/cancel/…)
        ┌───────────────────────┐                     │
        │ SqlitePITStore +      │                     │
        │ CachingPITStore       │                     │
        │ data/cache/crypto_*.db│                     │
        └───────────┬───────────┘                     │
                    │ PIT latest_as_of (无前视)        │
                    ▼                                 │
        ┌───────────────────────┐                     │
        │ btc_spot_trend 策略    │                     │
        │ (周线权重,纯函数核)    │                     │
        └───────────┬───────────┘                     │
                    │ weights+prices per period       │
                    ▼                                 │
        ┌──────────────────────────────────┐          │
        │ services/execution/              │          │
        │  dry_run_pipeline.py             │          │
        │  run_multi_period_dry_run        │          │
        │  (gate + kill-switch fail-closed │          │
        │   → 纸面成交 → reconcile ≈ 0)     │          │
        └───────────┬──────────────────────┘          │
                    │ DryRunRun                       │
                    ▼                                 │
        ┌───────────────────────┐                     │
        │ save_and_project_     │                     │
        │ dry_run → dry_run.db  │                     │
        └───────────┬───────────┘                     │
                    │ console 只读展示                 │
                    ▼                                 │
        ═══ 人工毕业决策(独立判断,非自动)═══          │
                    │                                 │
                    ▼                                 ▼
        ┌───────────────────────────────────────────────────┐
        │ live/pipeline.py::submit_live_batch               │
        │ attestation → plan → pre-trade gate → filters     │
        │ → mandate(AUTO 拒绝 / 人工复核)→ ledger 预记账     │
        │ → place(mexc_spot adapter)→ confirm → audit       │
        └───────────────────────┬───────────────────────────┘
                                │ 只读运维(绝不直接下单)
                                ▼
                    tools/live_ops_api.py (port 8440, X-Ops-Token)
```

关键方向约束:`services/` **永不** import `live/`(机器钉死);网络 import
只允许出现在 `live/exchange/` 适配器与 `tools/`;干跑流水线不触碰任何真实
HTTP —— 适配器的 `http_request` 注入点使全部网络可替换为测试替身。

---

## 3. 适配器协议(VenueAdapter)

`mexc_spot.py` 实现 `live/exchange/base.py::VenueAdapter`,构造与
`binance_futures.py` 完全一致(327 行模板,纯 stdlib):

```python
MexcSpotAdapter(
    *,
    api_key: str,
    api_secret: str,
    base_url: str,                 # 白名单:"https://api.mexc.com"
    http_request=None,             # 注入点:测试替身/重试包装
    clock=None,                    # 注入点:确定性时间戳
    safety_config: SafetyConfig | None = None,
    max_retries: int = 3,
    retry_base_delay: float = 1.0,
    sleep=None,                    # 注入点:确定性退避
)
```

安全不变量(与 Binance/Bybit 相同,治理测试机器钉死):

- **mainnet base**(`https://api.mexc.com`)在默认 `SafetyConfig`
  (`live_trading_armed=False`)下 **构造即 `raise SafetyViolation`**。
- **未知 base_url** `raise ValueError`(URL 白名单,不放行任意端点)。
- MEXC **没有现货 testnet** → 适配器没有 testnet base;可测性全部来自
  `http_request`/`clock`/`sleep` 注入,而非假端点。
- 无交易所 SDK(ccxt/python-binance 等一律禁止);只用 urllib + hmac + hashlib。
- **永不实现提现/划转**:不出现 `.withdraw(`、`create_withdrawal`、
  `sapiPost` 字面量(governance scanner 硬禁)。
- 密钥永不硬编码、永不落盘;`key/` gitignored,只写格式文档。

方法面(现货语义):`place_order` / `cancel_order` / `get_order` /
`get_open_orders` / `get_account` / 行情只读方法(`get_klines`、
`get_book_ticker` 等,供抓取器与运维 API 复用)。重试经
`live/exchange/retry.py::call_with_backoff`(429/5xx 指数退避,4xx 不重试)。

---

## 4. 签名机制(MEXC Spot v3)

Binance 兼容,细节:

- 请求头:`X-MEXC-APIKEY: <api_key>`。
- 私有端点 query 必须含 `timestamp=<ms>` 与 `recvWindow=<ms>`(默认 5000)。
- 签名:`signature = HMAC_SHA256(api_secret, urlencoded_query_string).hexdigest()`,
  以 `&signature=<hex>` 追加到 query string。
- 时间源:`GET /api/v3/time` 校时;适配器 `clock` 注入保证测试确定性。
- 错误:HTTP 400 + JSON `{"code":..., "msg":...}`;429 = 限频(下单 ~20 req/s,
  其他 ~10 req/s)→ 交给 `call_with_backoff`。

---

## 5. 现货 vs 合约语义差异(与既有 Binance/Bybit 期货适配器对照)

| 维度 | 期货适配器(已有) | MEXC 现货适配器 |
|---|---|---|
| 持仓 | 有方向仓位,可空 | 只有现货余额;「做空」= 不持有 |
| 杠杆 | 支持 | 无(1x,现货买入) |
| 资金费 | 有(carry 的边) | 无现金流项 |
| 强平 | 有 | 无(最大亏损 = 本金) |
| 下单数量 | 张数/币数 | 币数,受 LOT_SIZE/minNotional 过滤 |
| 保证金 | 初始/维持 | 全额预付 |
| 组合语义 | 多腿 L/S | 单名 BTC 多/空仓切换 |

工程含义:`btc_spot_trend` 的权重到目标手数映射更简单(无杠杆、无强平
缓冲),但 **lot-size / min-notional 舍入**(exchangeInfo 的 filters)必须在
适配器内诚实处理并体现在对账残差里;reconcile 恒等式(realized +
unrealized − costs ≈ 0)不变。

---

## 6. 数据流

```
MEXC klines (GET /api/v3/klines, page-by-limit)
  → tools/mexc_klines_fetch.py
  → services/data/market_provider.py::ingest_provider_rows
      (dedup + 未来数据守卫;knowledge_time=event_time EOD 可知)
  → SqlitePITStore (data/cache/crypto_*.db)
  → btc_spot_trend 策略核(PIT latest_as_of,无前视)
  → run_multi_period_dry_run (gate+kill-switch fail-closed,成本见 §7)
  → save_and_project_dry_run → dry_run.db → console 只读
  → 人工毕业决策 → submit_live_batch (attestation+mandate+gate+ledger)
  → mexc_spot adapter → MEXC → confirm → audit 哈希链
```

已知坑(抓取器必须处理):

- **MEXC klines 在部分 symbol 上忽略 `startTime`** → 用 `limit` 分页,
  不按时间窗口翻页;与本地已存最大 openTime 去重。
- klines 行是数组 `[openTime, open, high, low, close, volume, closeTime,
  quoteVolume, ...]`,解析用位置索引并断言长度。
- `volume` 是 base 数量、`quoteVolume` 是 USD 报价额 —— 与 QUANTOS 统一口径
  (USD 24h notional)对齐时用 `quoteVolume`,机器约束见 `VolumeUnit/
  VolumeSemantics`(2026-07-13 双乘根因教训)。
- 429 限频 → `call_with_backoff`;抓取器限速 ≤10 req/s。

---

## 7. 零手续费口径的成本模型含义

MEXC BTC/USDT 零手续费改变干跑/回测的成本假设,但必须诚实:

- **手续费项**:taker/maker 均为 0 bps(活动口径)→ `cost_bps` 可为 0。
- **但成本≠0**:仍有 ① 买卖价差(现货盘口,小名尤其);② 滑点/市场冲击
  (RE-1 Almgren-Chriss 引擎:参与率驱动,sqrt 临时 + 线性永久);③
  exchangeInfo 的 tick/lot 舍入。
- **建议口径**:干跑默认仍以 `cost_bps=5–10` 的压力档 + `cost_bps=0` 的
  零费档**两条曲线并列**(与 RAW/VT 双曲线同法),避免把「零手续费活动」
  当成永久结构假设 —— 活动可取消,策略必须在有费世界仍不崩。
- 容量诚实化不变:`critical_aum = min(冲击-夏普容量, 参与率上限 10%)`;
  BTC 中位 ADV ~$43.5B,现货趋势容量远高于既有策略,但仍按未校准区间带报。

---

## 8. 实盘毕业路径(MEXC 无 testnet → 三段式)

```
阶段1 干跑(默认,L2)
  btc_spot_trend 全历史 + 前向滚动干跑(**主口径 = MEXC PIT 数据**,CoinGecko 保留 _cg 对照)
  → 门批准率、对账残差≈0、RAW/VT 双曲线、零费/有费双档
  通过标准:与 crowd_contra 同级的执行现实检验 + paper-track 前向 IC 存证
  (已接线:tools/dry_run_btc_spot_multi 落 dry_run.db + paper_track.db 前向持久层)

阶段2 小额真实(人工 mandate)
  → validation attestation(策略/数据/成本假设逐项签认)
  → 人工 mandate(AUTO 一律拒绝;多方批准 verify_multi_party_grant)
  → SafetyConfig.live_trading_armed 由真人显式置位(否则适配器构造即拒)
  → submit_live_batch: gate → filters → ledger 预记账 → place → confirm → audit
  → 金额上限:单笔/单日 mandate 内钉死(建议首单 ≤ $100 级)
  → kill-switch 随时可 ARM,latch 后全拒

阶段3 规模化(多轮小额 → 逐步抬 mandate 上限)
  → 每轮重签 attestation;上限提升本身是一次新的多方批准
  → live_ops_api (8440) 只读监控:持仓/成交/账本/lag/DLQ,绝不直接下单
```

任何阶段 kill-switch ARM、账本不一致、或对账残差超阈 → 立即回到阶段1。

---

## 9. 治理边界清单(机器钉死)

| 规则 | 执行者 |
|---|---|
| 网络 import 只允许 `live/exchange/` + `tools/`;`services/` 零网络 | governance scanner + `tests/governance/` |
| `services/` 永不 import `live/` | 同上 |
| 无交易所 SDK(ccxt/python-binance/…) | 同上 |
| 无提现/划转(`.withdraw(`、`create_withdrawal`、`sapiPost` 字面量) | 同上 |
| mainnet 适配器默认 SafetyConfig 下构造即 SafetyViolation | 适配器构造 + 治理测试 |
| 未知 base_url → ValueError | 适配器白名单 |
| 无硬编码密钥;`key/` gitignored,只写格式文档 | scanner + review |
| 下单必经 attestation + mandate(AUTO 拒绝)+ gate + kill-switch + ledger | `submit_live_batch` 单入口 |
| live_ops_api 只读,绝不直接下单 | `tools/live_ops_api.py` 边界测试 |
| AI agent 永不触实盘/凭证;非人 actor 到不了 APPROVED | `ResearchAgentLoop` + 多方批准机 |

**诚实边界(与全仓一致的注脚)**:以上均为运行时 chokepoint 防误用;单进程内
挡不住受信代码自行构造 HUMAN Actor 绕过 —— 该面由代码评审、governance
scanner 与 Phase 14 进程隔离治理(见 `docs/PHASE_14_PROCESS_ISOLATION.md`)。

---

## 10. 密钥文件格式(文档约定,绝不写真实值)

`key/` 已 gitignored。MEXC 凭证文件约定为 `key/mexc_spot.json`:

```json
{
  "api_key": "<MEXC spot API key>",
  "api_secret": "<MEXC spot API secret>",
  "base_url": "https://api.mexc.com",
  "permissions_note": "spot trade+read ONLY; withdraw/transfer DISABLED at MEXC console"
}
```

加载器只接受「提现权限已在交易所侧关闭」的 key;启动时以 `GET
/api/v3/account` 只读校验可达性,失败即 fail-closed。
**已实现(2026-07-23)**:`tools/mexc_credentials.py`(`load_mexc_credentials` +
`check_mexc_key_safety`,ungated 纯函数镜像,不实例化 gated 适配器);
`tools/verify_key_permissions.py --venue mexc`(MEXC_MAINNET_* env / 同一 key 文件);
驱动器 `--arm-live` 路径启动时强制过一次该校验,不 safe 即 SystemExit。
