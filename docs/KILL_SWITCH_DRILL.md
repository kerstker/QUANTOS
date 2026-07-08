# Kill-Switch 演练手册(Rank #27)

> 目的:定期演练熔断链路,证明"停机—拒单—人工复位—留痕"整条安全链**可执行、非纸面**。
> 本手册的每一步都由端到端测试 `tests/monitoring/test_kill_switch_drill.py::test_kill_switch_drill_end_to_end`
> 机器验证——手册与测试同步,不会漂移。演练在 L2(纯干跑)下进行,不碰任何实盘路径。

## 何时演练

- 每次动到 `KillSwitch` / `PreTradeRiskGate` / 多方批准(`services/governance/approval.py`)后;
- 定期(建议每迭代一次)确认链路未退化;
- 任何真实告警触发搬闸后的复盘。

## 演练链路(四步,与测试一一对应)

1. **ARM(搬闸)** — 任意 actor(含非人 SYSTEM/AGENT)都可停机(停机是安全方向,越容易越好)。
   `ks.arm(actor, reason, at=t, audit_sink=audit)` 落一条 `kill_switch.arm` 到 durable 审计链;
   同时向 `IncidentLedger.open(...)` 开一条 CRITICAL 事故。`ks.is_active()` 恒 True。

2. **HALT(拒单)** — 闩锁期间,预交易门 `screen_intents(intents, gate, ctx)` **拒绝每一笔意图**,
   拒绝理由含 `KILL_SWITCH`。这是 fail-closed:latched → 全拒、零成交。

3. **RESET(复位)** — **只有 HUMAN + 绑定到本事件的多方 grant** 能复位(非人 actor / 单人 / 未绑定
   grant 一律拒)。构造:`open_request → review(第二人) → approve(第三人) → consume(发起人)`
   得到三个两两不同真人的 grant;`ks.reset_with_grant(human, grant, at=t, audit_sink=audit)` 落一条
   `kill_switch.reset`;`IncidentLedger.resolve(...)` 关事故。

4. **TRAIL(留痕)** — durable 审计链 `events == [kill_switch.arm, kill_switch.reset]` 且 `chain_valid()`;
   事故 `open_count()==0`;复位后同一门放行正常意图(交易干净恢复)。

## 硬不变量(测试钉死)

- 搬闸是安全方向:任意 actor 可停;停机即全拒。
- 复位是危险方向:**HUMAN + 三个两两不同真人的一次性 grant**,绑定本事件;非人 actor 永不可复位
  (`test_agent_cannot_reset_the_drill`)。
- 两个治理决策(搬闸/复位)100% 落防篡改哈希链(Rank #20),可跨会话验证。
- L2 边界一寸不让:复位不解锁 LIVE(readiness pin + LIVE flags 结构性挡死),演练全程干跑。

## 边界(post-L2,本演练不覆盖)

真 RBAC / 评审者实名身份 / 委员会签名投票 需要身份层 + 出进程签名(Phase 14),是显式非目标;
当前 operator/actor 是自述式标签,演练验证的是**批准的构造路径与留痕**,不是进程内受信代码的信任边界。

## 运行演练

```bash
python -m pytest tests/monitoring/test_kill_switch_drill.py -v
```
全绿即演练通过。纳入 `scripts/validate.py` 全套,所以每次门禁都跑一遍。
