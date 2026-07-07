# 1H(小时线)Cadence 转型 —— 决策简报 + 分阶段计划

> 2026-07-07 · 用户决定转中高频、先转 1 小时线 · 6-agent 并行调查(全部结论钉 file:line/快照)+ 主审 TDD 落地
> 这是"共同探讨"的制品:先给最诚实的裁定,再给分阶段计划,标清哪些已建、哪些等你拍板。

## 一句话裁定

**基础设施能安全建(纯加法、默认仍走周度、字节等价、不上真钱);但把现有 carry/crowd_contra 日频因子
直接端到 1H 大概率没有可交易的边——仓库自己的证据方向完全相反。** "能不能建"和"有没有边"是两个独立维度。
我已把基础设施建好,目的是让 IC 面板/OOS 门去**证伪或证实** 1H 边,而不是假定它存在。

## 为什么"现有信号类 1H 大概率无边"(证据钉在你自己的快照上)

| 证据 | 来源 | 含义 |
|---|---|---|
| crowd_contra 持有期 IC:**h1(=1天)t=0.62 → h7 2.91 → h14 3.35** | `data/cache/ic_panel_snapshot.json` | 边**随持有期变长而增强**;1H 比 h1 还短一个量级 → 更弱 |
| carry_composite:h1 t=0.21≈0 → h7 2.75 | 同上 | 第二条边同样在最短持有期塌成噪声 |
| reversal_7_wide 日频 t=16.4 但 10bps 成本即负 | `wide_factor_gauntlet.py` | 短持有期大 IC = 买卖价差反弹,"持有不过夜就没了",与 FX +0.70 幻象同型 |
| crypto 4h 因子 IS +2.29 → OOS −2.01(反号) | memory crypto-research-findings | 最接近日内的既有证据是 **null** |

两条可部署边的经济本质(散户拥挤度慢速均值回复、资金费现金流收割)都在**多日尺度**。1H 换手≈日频 24×,
每次往返吃买卖价差+冲击,成本门槛高一个量级,毛 IC 再大也大概率被成本吃光。

**诚实留白(不因悲观漏报):** 以上只否定**现有信号类**。一个**全新的、专为日内设计的微结构/订单流信号类**
(盘口失衡 / taker 净流 / 爆仓级联 / 跨所领先滞后 / 资金费结算时点)是真正的 **UNKNOWN 不是 NO**——但它需要
仓库现在完全没有的**逐笔/盘口数据类**(tick_service 是空壳、REST 只拉日频)+ 全新研究立项,不是"周改小时"。

## 分阶段计划

| Phase | 内容 | 安全 | 状态 |
|---|---|:-:|---|
| 1 数据基础设施 | 1H 抓取器 + 独立 PIT 库 + knowledge_lag=收盘防前视 + 丢活 bar | ✅纯加法 | **✅已建**(`tools/binance_klines_1h_fetch.py`) |
| 2 配置抽象 + 执行护栏 | CadenceConfig(WEEKLY 字节等价)+ rate-limit 单位对账/回填 + firm 门接线 | ✅纯加法默认周度 | **✅已建**(cadence.py / P1-5 / P1-6) |
| 3 在真 1H 面板证伪/证实边 | `compute_horizon_ic_series` 算 h=1/4/24 非重叠 IC + 净成本 gauntlet(成本按 24× 重估 + 真实价差 + RE-1 冲击)+ no-peek OOS(1H 自相关强必须 embargo+非重叠) | ✅只读研究 | ⏳待跑(Phase 1 数据就绪即可,**诚实预期:大概率被判 NO_EDGE**) |
| 4 全新 intraday 微结构信号类 | 与日频价量/持仓/carry 正交的执行/微结构 alpha + 逐笔/盘口新数据类 | ❌需拍板+新数据 | ⏳需你立项(可能触及新网络能力) |
| 5 1H 前向 paper-track → 真钱 | 过 validation_attestation + ≥8 周前向 + Phase 3A 人工门 | ❌不可协商人工门 | ⏳ |

## 本轮已落地(纯加法、默认周度、零回归、不上真钱)

- **CadenceConfig**(`services/research/cadence.py`):把散在 ~10 处的硬编码周度参数收敛成可切换 preset。
  WEEKLY 逐字复刻现值(parity 测试钉死字节等价);HOURLY_1H 备好数值(**默认不启用、未接进 live 执行路径**)。
- **1H 抓取器**(`tools/binance_klines_1h_fetch.py`):独立库 `crypto_binance_1h.db`,knowledge_lag=收盘防前视
  (端到端 as_of 无前视测试证明:as_of=开盘排除该 bar、as_of=收盘才读出)。
- **P1-5 频率引擎**:改名 `rate_window_s`→`rate_window_ms`(消除"秒当 ms"陷阱)+ 从持久账本回填最近提交时间戳
  + runaway-guard(24h 窗/上限 500,**工程占位待你确认**)。
- **P1-6 firm 硬门**:用你签的上限(gross 20000/net 10000/单名 20%)接线,越限停止新增(自清除)。

## 需要你拍板的(每个一句话)

1. **是否投入建 1H 数据**(窄宇宙~150MB / 全宇宙 3-4GB):认知前提 = 现有信号类 1H 大概率无边,建的目的是
   "能去证伪",不是"假定有边"。性价比你判断。(我已建好抓取器,跑不跑、跑多宽由你。)
2. **rate-limit 数字**:24h 窗 / max 500 是我按你"24h 中高频"给的占位。你的"中高频"具体多少单/天?据此定 runaway 上限。
3. **rebalance 真按 1h 还是 1h bar 上跑更慢的 4h/日内**(降换手/成本)?1H 换手 24×~168×,成本几乎必然把毛边吃负。
4. **年化因子历史 bug** 是否借机统一:crypto 24/7 正确日频 ppy=365,但代码里 252(股票)/52(周)/365 三套不一致。
   修正会移动历史 Sharpe 绝对值(相对比较不变)。CadenceConfig 保守做法是 WEEKLY 复刻现值不动。
5. **是否为"全新 intraday 微结构信号类"立项**(需逐笔/盘口新数据类,可能触及新网络能力)——与 cadence 转型不同性质。
6. **接受不可协商前置**:任何 1H 策略上真钱前必须过 no-peek OOS + 净成本 gauntlet + validation_attestation。
7. **若真启用 1H 执行**(调度/前向/驱动器转小时),必须**同步一起改**一组危险单点:`register_schedules.ps1`
   WEEKLY→HOURLY、`watchdog.py` 死人开关 9 天→小时级、`_MAX_STALENESS_MS` 7 天→~2h、跨源核价按天放大的容忍度、
   `forward.py` min_gap 6.5 天。**漏改任一都会让护栏语义反噬**(哨兵瞎 9 天 / 陈旧价交易一周 = 真钱级灾难)。
   这组必须整组一起改,或暂不启用 1H 执行。

## 关键不变量(转型全程不破)

L2 五旗帜结构性挡死 + readiness pin 不变;1H 基础设施不驱动任何自动晋级/下单、不改真钱路径;interval 字符串/
bar 分桶留在 tools 数据层(不进 services 纯模块的治理扫描面);任何 1H 策略被 `validation_attestation` fail-closed
挡死直到人工签署。
