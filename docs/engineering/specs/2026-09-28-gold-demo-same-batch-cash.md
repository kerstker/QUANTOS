# 黄金同一执行批次的现金交接验证

## 本次证明的链路

使用原黄金 issuer fixture 的临时密钥、实际提案/风险门/SQLite派发存储和模拟 HTTP。时间轴保留现金端点保护区：账户原点在先、现金起点在后、订单及私有成交在现金区间内部，末端观察与两轮收入读取随后完成。没有缩小5000ms现金端点保护区或放宽执行来源新鲜度。

同一原签名批次的 client ID、venue ID 123、成交ID 7、费用USDT 0.01进入原回执导入日志。该日志直接传给 project_cash_chain，钱包减少0.01、残差为零；报告 manifest_hash 必须等于本批导入 manifest_hash。随后原编号查询与同一私有流通过 reconcile_reserved 更新原 OMS，一笔成交、无待恢复订单，持仓等于原数量。再读原现金日志报告不变。

此前黄金OMS和现金连续性是两项独立场景；本次把开仓批次串在一起，不新增生产代码或重复账本。仍未验证真实交易所或同批开平仓完整回合。

## 拒绝与验证

换账户命名空间、换手续费对应成交ID、修改费用或钱包余额均不能得到 MATCHED_REPORTED_INPUTS。所有对账只读原派发/回执数据，不修改现金输入。53项相关联合测试通过；改进只读断言位置后黄金专项5项通过。Ruff E9/F821、仓库卫生通过；未完整canonical、未部署。

原 OMS 成功结果仍 cash_reconciled=false；独立现金报告是合成、未认证来源的技术匹配，不自动改变执行授权或经济资格。source_verified=false、authoritative=false、strategy_pnl=null保持。

## 下一交接缺口

已阅读 tools/demo_functional_worker.py::reconcile：运行端已有通过位置上下文来源读取原日志并保留现金报告的主干，应复用。services/research/ai_factory_lifecycle.py::research_trigger 当前输入是成熟市场标签；黄金 campaign 尚无执行/现金反馈消费者。后续应通过原策略与执行批次引用、摘要和实际可用时间接入研究材料，不能将技术现金匹配伪装成模型标签、收益合格或策略准入。真实连续采集、黄金部署配置、候选到执行意图及经济验证仍未闭合。
