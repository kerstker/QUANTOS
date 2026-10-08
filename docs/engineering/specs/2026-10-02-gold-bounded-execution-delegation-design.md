# 黄金短时信号的有限人工执行委托设计

状态：有限声明的独立签名合同与原issuer退出预留预算已有开发实现，见[全天交接与委托基础](2026-10-02-gold-pilot-delivery-foundation.md)。受保护人工安装、与原账本/pilot的独立连续性核验、逐信号证明及消费者仍未实现；没有实际批准、安装或委托执行。承接[执行证据与轮换](2026-10-02-gold-executor-evidence-and-rotation.md)及[完整十环节目标](../plans/2026-09-28-ai-factory-closed-loop.md)。本设计保持完整交付范围；及时通知和维护锁等待不能单独证明五秒信号可持续执行。

## 当前约束与不能继承的权限

原gold_bar_definition的entry_timeout_ms为5000；gold_strategy_policy_install每次要求本机root/TTY读取候选并输入完整摘要，首次安装还有多次原证据复核。当前没有可重复的人工时延保证，信号迟到必须拒绝。

Mandate已有有效期、场所和敞口限制；Demo原prepare/issuer拒绝AUTO、AI、SYSTEM批准身份。现行issuer policy可覆盖有限多日窗口、单个grant仍短时有效，但strategy_constraints_check逐笔要求constraints全文摘要匹配已安装policy。长效mandate不能代替逐次精确批准。

现有DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V1明确不可执行；材料中的human_override也仍要求精确决策批准。不能改其字段、解释其签名或复用原确认，使其获得处理未来信号的权限。新能力只能由独立、明确的人工确认安装新的版本合同；本设计文档和开发测试均不构成该确认。

## 拟开发合同与原组件接线

1. **DEMO_GOLD_EXECUTION_DELEGATION_V1。** 人工事先确认有限策略委托：Demo环境、账户、获准release、策略definition/source/archive摘要、方向/订单类型、数量/单笔及累计名义额、开仓截止、退出截止与退出规则、最大持仓时间、pilot期初/停止记录、mandate/validation/native limits和共享预算身份。明确允许在这些固定规则内消费未来信号，不允许选策略、调参、扩大预算、重开停止或自动续期。
2. **独立issuer验证的派生权限。** 新版本delegated admission与旧精确policy分别验证；不改旧schema的含义。固定委托和真实决策确定性地产生精确授权材料，issuer核验其派生关系，不接受controller自造policy。每个签名context同时绑定委托、决策/前缀、精确constraints、订单与期限，禁止替换proposal拼接授权；不要求每次覆盖安装policy。
3. **DEMO_GOLD_DELEGATED_DECISION_PROOF_V1。** 引用executor原归档和冻结前缀，复用FreshEpisode/retained reader/restore_snapshot核验：信号由已批准definition在合法知识时间内生成，订单遵循已冻结的入退场规则。controller继续留存请求/结果；缺证据不是空仓，不补采、重写或续用过期信号。
4. **原预算、claim与恢复。** 复用IssuerStore.reserve的事务、先预留后签发、唯一proposal/order claim。新mandate/validation可能改变budget_group_binding，同group不能重绑定，新group也不能清零历史。必要的版本迁移须人工确认连续性记录，绑定前序账本head、已用与已预留额度，并在安装及首次消费时锁内复核；未知即拒绝。撤销、恢复或重启不回补预算、不重置pilot。
5. **开仓与退出预算。** 人工同时确认有限reduce-only/取消规则及单独预留退出额度。开仓截止早于总授权截止；pilot到期或亏损停止后不再开仓。原策略容量检查不等于issuer已预留累计退出额度。总授权过期、账户不明、减仓或核对失败保留EXIT_REQUIRES_ATTENTION，不续期强行退出。

## 时效与验收

信号前完成独立人工批准、静态源码/definition核验及启动绑定；信号后保留原前缀、最新账户与风险、预算预留/签名、executor最终核验和原发送入口。静态校验复用需显式会话身份及变更失效合同，不能直接省略原检查。逐笔期限取原信号、mandate、委托、policy/key及市场新鲜度的最小值；慢于五秒放弃本次开仓。

实现顺序：先纯离线委托/证明/预算连续性/撤销退出合同与渲染，再接原controller→issuer→executor。全部先用隔离账户和合成key验证，默认不可安装/发送。回归覆盖错账户/策略/release、过期、缺失/篡改原件、恶意orders、并发预算竞争、签名前后崩溃、UNKNOWN恢复、撤销竞态、退出额度不足及5000ms边界。记录逐段真实处理耗时，不能用固定测试时钟宣称现场时效通过。

同版本完整验收后才准备人工可审查安装材料。实际确认、安装和启动仍为独立步骤；原mainnet门、策略独立经济准入与真人mandate不因该设计改变。

## 24小时观察的独立容量缺口

5279源码中gold_bar_rotation.MAX_ROTATIONS=31；gold_bar_auto_history和FreshEpisode各最多32份目录，分别有256MiB总字节限制。持续无信号、每根15分钟bar推进一次时，32个episode约覆盖八小时，不能保证24小时约96个close；有持仓时消耗速度可能不同。超过原限额会失败关闭，不能记作成功运行24小时。

后续必须建立版本化的pilot episode/storage budget：由批准窗口和bar周期推导观察数，增加明确且有限的终止观察与恢复余量；三处限额同步，目录按pilot身份隔离，实测字节预算并保留全部旧历史。不能只把31改大、删除历史、重置episode或回补预算。此项与进程预算早于真实期初的问题分别验收。

本设计依据冻结5279da0203d51ec9324dfbe7658211bb93850130的原issuer policy/store、Scope、controller、pilot及bar输入实现。尚无新执行委托签名、实际账户操作或策略盈利结论。
