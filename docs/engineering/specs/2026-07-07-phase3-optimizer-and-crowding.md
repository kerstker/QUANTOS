# Phase 3 剩余两项 —— 诚实裁定 + 落地(组合优化器接 sizing / 拥挤门校准)

> 2026-07-07 · ultracode 双 agent 并行调查(均亲跑真数据)+ 主审 TDD 落地 · IMPLEMENTATION_ROADMAP.md Phase 3
> 核心结论:**两项都不该"硬接线新数字"**——一个是恒等 no-op(硬接会造回归),一个用错了统计量(默认阈值
> 结构性失效)。正解都是"机制/文档 + fail-closed + 待用户签数字/拍板行为",这与 firm-gate 同一哲学。

## 子项1 · firm 敞口硬门(P1-6)—— ✅ 已完成(上一轮)

用户签署 gross 20000 / net 10000 / 单名 20%,`services/portfolio/firm_gate.py::evaluate_firm_gate` +
哨兵算门写状态 + 驱动器读状态 BLOCK 停止新增(自清除)。见 RISK_REGISTER P1-6 条。

## 子项2 · 组合优化器接实盘 sizing —— 裁定:不硬接线,文档化等价物 + 默认关闭 opt-in hook

**为什么不硬接线(三条,均有 file:line/数据支撑):**
1. `services/portfolio/optimizer.py::risk_parity_weights` 是**跨 alpha sleeve(策略)**的风险预算分配器
   (docstring:1-9 "sizes RISK across streams";n≤1 时直接返回 [1.0])。每条 alpha 内部已美元中性。
2. live 是**单策略** crowd_contra(`tools/live_track_advance.py` `_STRATEGIES` 仅 1 条)。单策略下 sleeve=1,
   风险平衡是**恒等空操作**;唯一"非 no-op"的接法是把 `min_variance_weights` 下沉到 14 个 symbol 做 per-name
   min-variance——但那会用一个**未经验证的 min-variance book 替换已被干跑验证的等权 `_legs` book**
   (`tools/crypto_oi_research.py:60`),偏向低波动名字、扭曲截面信号,对唯一 live 策略造回归、换零验证收益。
3. 干跑反向证据:carry+crowd 经 risk-parity 合并 VT 1.14–1.21 **< crowd_contra 单条 VT 1.38**(CLAUDE.md)。

**等价关系(= roadmap 子项2 明写的"文档化 per-strategy 隔离的风险预算等价物"):**
组合优化器在账户级会给的风险预算分配,本平台以三层**硬门**等价实现:
- **sleeve 内部 sizing** = per-strategy 裸 `_legs` 等权美元中性权重;
- **sleeve 风险预算** = per-strategy mandate 上限(`live/governance/mandate.py`,gross/net/单名 cap);
- **账户级跨 sleeve 预算** = firm 硬门(`services/portfolio/firm_gate.py`,跨所 gross/net/单名集中,已接线)。

**已落地(纯加法、默认关闭、live 默认路径永不调用):**
`services/portfolio/sleeve_sizing.py::size_strategy_sleeves(sleeve_cov, strategy_ids, mode='off')` —
mode='off'(默认)恒返回全 1.0;**单 sleeve 恒返回 {id:1.0}(证明单策略下优化器是恒等 no-op)**;≥2 sleeve
且非 off 才复用 optimizer 在 sleeve 收益协方差上算标量;非有限 cov fail-closed raise。`apply_sleeve_scalar`
把 book 权重同乘标量(保持美元中性)。备**多策略 live 上线**时显式启用(驱动器 `--sleeve-sizing` 接线是
未来一步,单策略下无价值故本轮不接,避免动唯一 live book)。7 tests。

⚠ **多策略启用前置**:sleeve hook 若启用第二条策略共用同一 mandate,会撞 2026-07-06 记录在案未修的
shared-mandate cap 缺口——必须先落 per-strategy 子限额(见 RISK_REGISTER)。

## 子项3 · 拥挤门阈值校准(P2-7)—— 裁定:不硬编新阈值,记录敏感性发现 + 待用户拍板行为

**实测发现(233 周真 global_ls,exact live crowding 定义):**
crowd_contra 这类**美元中性 fade-the-crowd** book 的**账户级 gross 加权平均**拥挤分**结构性钉死在 ≈0.50**
(长腿拥挤 ≈0.135 + 短腿拥挤 ≈0.869,美元中性下相消到 ~0.50)。后果:
- 默认 `warn_threshold=0.45` → WARN **恒触发(~100%)= 告警疲劳**;
- 默认 `threshold=0.60`(BLOCK)→ 触发率 **~0.9%(近死)**;
- **平均分对本 book 类别是错误统计量**;真正有信息的是 **concentration 判据**(单名集中×拥挤,实测有充足
  余量 max conc ≈0.083 < 0.25)和**短腿(fade-target)拥挤的下降**(边变薄的信号)。

**已落地(值字节不变,只加诚实性 + 契约锁):**
`services/portfolio/crowding_gate.py` 暴露 `CALIBRATED_DEFAULTS`(= 现有 kwargs 默认逐字,provenance
`SENSITIVITY_SCANNED_2026_07_07_DEFAULTS_PENDING_RECALIBRATION`)+ docstring 去掉"UNCALIBRATED, R-1"、
指向本文档;契约测试钉死默认不漂移 + provenance 记录。**行为逐字节不变。**

**待用户拍板(有意行为变更,firm-gate 式"机制我建、数字/行为人签",本轮不代做):**
- (a) 保留平均分口径,从实测分布设阈值(warn 抬到 ~0.51 停止恒触发);或
- (b)【推荐】给 `evaluate_crowding_gate` 加 `score_mode`("average" 默认字节不变 / "short_leg"),平均分
  路径改 **WARN-only**(BLOCK 只由 concentration / 短腿口径触发)——但改 BLOCK 语义需你确认;或
- (c) 走 firm-gate 式签名流程,用户签校准数字。
- 一个只读离线敏感性扫描器 `tools/crowding_gate_calibration.py`(复现上述分布)= 建议后续(reproducibility)。

## 诚实边界总结

Phase 3 的诚实答案是:**该做的不是"把两个原语硬塞进 live 路径换个数字",而是证明其一为恒等、其一用错统计
量,把等价关系与实测发现文档化,机制默认关闭/字节不变,风险数字与行为变更留给用户签。** 不制造假 chokepoint、
不用未验证 book 替换已验证 book、不在错误统计量上换绝对数——这与全库"诚实第一 + 机制建数字签"一致。
