# 原信号判断原因进入下一轮AI研究

原记忆传递NO_SIGNAL等试验终态及报告摘要，模型无法区分区域失效、条件未满足、
等待确认或确认超时。原报告已包含逐窗判断，本增量把这些既有事实接到原研究记忆，
没有重跑旧实验或重写报告。

原gold_factory_research完成后校验report.json摘要，提取evaluated_windows、
固定枚举reason_counts和final_state，写入原FactoryStore会话结果。
原结果文件和run_scoped返回协议不变，历史档案恢复继续使用原合同。
后续reserve_campaign将这些字段冻结进GOLD_RESEARCH_MEMORY_V3；V1/V2仍可读，
没有诊断的旧会话不补造数据。失败记录仍使用原RESEARCH_FAILED字段。
模型调用前verify_memory重读原报告，必须重算得到同一诊断；篡改有效格式的摘要也拒绝。
诊断不包含价格、收益、评分或自由文本，不代表独立准入。

先在原HTTP→调度→队列双轮测试增加诊断断言，复现两项KeyError。
补齐后，下一轮真实队列收到原SELL_PRESSURE_WITH_RECLAIM/LATER_WINDOW_CONFIRMED计数，
恢复不重复模型调用；篡改摘要后第二轮不调用模型。另用实际成本回放构造NO_SIGNAL，
确认CONDITIONS_NOT_MET计数进入模型请求；禁止收益标签、非法枚举及错误计数。
记忆/调度/崩溃/失败/双批/反馈七组58项通过，Starlette弃用警告保留；
已有AI归档恢复/结果投影/AI研究三组19项通过。修改文件E9/F821和diff检查通过。
原依赖图生成器复核20域/52边/无环，图合同9项通过；没有运行本增量全量验收。

## 真实本地模型交接

独立目录`/home/kerstker/quantos-dev-output/gold-signal-memory-real-qlvc40d1`，
使用此前留存输入双轮任务第一轮的原报告，逐项验证原trial和报告摘要，
以新时间生成V3记忆并调用真实qwen3.8-27b。原报告未变，模型返回候选3，
research_memory_hash为00a5246cac39538c0831e5067819a688398c48fde1d3a69a7c8c24d6530c1c5b。
诊断为ZONE_EXPIRED_OR_BREACHED一次、TERMINAL_ZONE一次、最终EXPIRED。
这是独立真实模型交接，没有执行新成本实验，不能替代整轮或策略改进验证。

六类固定候选仅调整卖压阈值和确认窗口，不能解决区域已经失效的问题。
下一功能缺口是更丰富的、采集前冻结的策略/价格区域设计，以及对应成本实验和独立验证。
不能通过事后修改旧价格区域来让历史NO_SIGNAL产生交易。

本增量只在主开发工作区；a247新行情双批和完整验收独立源码均保持不变。
尚未冻结交付、完整验收或部署，不涉及订单和账户状态。
