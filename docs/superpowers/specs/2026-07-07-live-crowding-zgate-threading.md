# live 拥挤 z-gate 接线(注册表 4→5 tuple)+ ADV/vol 证据(诚实延后门控)

> 2026-07-07 · ultracode 2-agent 调查(数据流 + 回归/fail-closed 证明)+ 主审 TDD 落地 + 真实 testnet preview 联调
> 目标:把 signal→gate 的每腿证据穿线,激活两个延后项——(a) live 拥挤 z-gate,(b) 真实 ADV/vol 门。

## 已落地:live 拥挤 z-gate(挤压保护)

crowd_contra 干跑驱动器已有的绝对 z-score 门(校准见 `2026-07-07-crowding-gate-calibration.md`)现在也接进
**live 路径**。`_latest_weights_and_prices` 从**同一决策行 r**(零 row-drift)算每腿 evidence
`{adv_units, inst_vol, crowding_z}`,signal 返回 4-tuple → **5-tuple**(blast radius = main() 解包 + 4 个测试
+ 注册表 doc,全枚举更新)。main() 拥挤门接 `average_warn_only=True`(rank 平均分是结构性常数,降为诊断 WARN)
+ `crowding_z_by_symbol` + `max_crowding_z=3.0`(**用户签**);被 short 名字 global_ls 时序 z > 3.0 → 整批 BLOCK
(跳本周调仓、现有仓位不动 = 挤压保护,fail-safe 方向)。`crowding_zscore` 提升到 `live/execution/crowding_score.py`
共用(干跑驱动器去重)。真实 preview:本周所有 z≪3.0 → 门正确 inert(WARN,与改前逐字节相同)。

## 诚实延后:ADV/vol 门控(preview 联调抓到的 exit-order 回归)

**教训**:真实 testnet preview 抓到了单测 + fail-closed 证明都漏掉的真实回归。原计划把真实 ADV/vol 喂进
`make_ctx_factory`(激活 `check_liquidity`/`check_volatility`)。preview 显示 **n_rejected_by_gate=6**——不是
预期的 0。诊断:14 个信号腿证据全干净(participation ~1e-6、vol ~0.5、z ~-1.5,全远在阈内),**被拒的是
"平仓单"**——退出**不在本期信号里的遗留仓位**(测试网遗留仓)。这些名字不在 evidence(只覆盖 14 腿)→
ADV/vol `.get()`→None → **fail-closed 拒了平仓单**。

这是 **fail-safe 反方向的错**:平仓/减仓单永远应该能过(必须能退出/去风险),但缺证据把它们挡了。而 ADV 门在
$5k AUM 下真实参与率 ~1e-7 **本就从不绑定**(RE-1:carry ~$2M/crowd ~$7M 才绑),接线净价值≈0。

**裁定**:撤回 ADV/vol 门接线(`make_ctx_factory` 仍传占位符 = 字节等价旧行为);evidence 里的 adv/vol **已算好
可用**,留待真正接线时——需先给平仓/减仓单设计 **reduce-only 豁免**(缺流动性数据也永远允许退出)+ 规模到位。
vol-annualize 口径已钉死匹配 `max_instrument_vol=5.0`(源码测试锁),ADV/vol 窗口是工程占位。

顺带改进:`result.rejected_by_gate` 现在告警可见(此前静默少执行);源码级接线钉子锁死 z-gate 接线 + ADV/vol
**未**接线(防误接导致挡平仓单)。

## 诚实边界

live 现有挤压保护(z-gate,与干跑同一签署 z*=3.0);ADV/vol 门=证据已算、门控延后(需 reduce-only 豁免设计 +
规模)。**门禁三检全绿;真实 testnet preview 联调确认改动干净(5 个遗留仓平仓单的拒是预先存在的 parity 问题、
非本改动,且现在告警可见)。** 10 新测试。核心教训:**testnet preview 联调不可省——它抓到了单测漏掉的 exit-order
fail-closed 回归,先验证再提交。**
