# 黄金请求构建与原执行会话联通

原 demo_request_inputs.order_scope 使用默认BTC精确订单验证，导致黄金即便已有
独立策略、风险提案和签名支持，也无法通过运行期请求构建器。
现按已验证issuer policy选择原精确批次协议；任务源版本、时窗、profile、
market source、原批准摘要、每单及批次上限继续由原函数验证。

新增七项隔离集成回归，合法流程修改前因DEMO_BATCH_ORDER_SCOPE失败。
修复后：合成账户/黄金行情→原build→原decode与ExecutionSession.propose→
原issuer重放/签名→session.submit→原prepare/execute→一次模拟ACK。
查询/现金状态仍QUERY_RECOVERY_REQUIRED，cash_reconciled=false，OMS无成交。
重复session.submit拒绝。旧策略、变更风险限额、错误来源、过期策略和
过期行情不能构建请求；原task_check同时绑定黄金订单、profile和release。

五组联合64 passed：gold_demo_request_session、demo_request_inputs、
demo_execution_session、gold_demo_issuer、demo_operator_setup。
新测试完整Ruff、实现E9/F821通过，治理698文件零违规。
沿用原profile/task/session通用结构，其具体市场由黄金policy和订单摘要绑定；
不复制会话状态机或增加新的任务持久化表。

全部为隔离临时密钥/合成账户和模拟HTTP，部署custody替换为fixture。
没有读取真实密钥、修改实际批准或安装配置、没有真实下单或服务部署。
tools/demo_operator_setup.py仍为BTC固定模板：黄金部署配置生成与审批输入、
撤单及剩余成交恢复、研究候选到黄金执行意图和资金反馈仍需接齐。
不是完整canonical或真实Demo端到端验收。
