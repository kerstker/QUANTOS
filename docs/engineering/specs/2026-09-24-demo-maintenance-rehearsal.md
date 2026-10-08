# 可续作的维护隔离演练入口

tools/demo_maintenance_rehearsal.py将已有备份恢复、引用报告绑定、保留被引用记录、
候选移动、账本核验及完整回滚组合为一个可续作流程。只操作新的隔离工作目录，
原始备份/报告/生产数据不改，不调用systemd、网络、账户凭证或发单接口。

prepare读取私有capture ZIP、留存计划及V2引用报告，并要求调用者给出备份和
报告的预期SHA256；冻结输入副本和摘要后自动resume。resume只需工作目录。
输入格式沿用现有capture_only、DEMO_RETENTION_REVIEW_PLAN_V1及
DEMO_RETENTION_REFERENCE_REVIEW_V2，不另造权威记录或将SCOPED_REVIEW_ONLY升级。

```text
python -m tools.demo_maintenance_rehearsal prepare --workspace NEW_PRIVATE_DIRECTORY \
  --capture CAPTURE_ZIP --plan PLAN_JSON --review REVIEW_JSON \
  --expected-capture-sha256 CAPTURE_SHA256 --expected-review-sha256 REVIEW_SHA256
python -m tools.demo_maintenance_rehearsal resume --workspace EXISTING_PRIVATE_DIRECTORY
```

这些是开发入口格式，不是要求用户现在运行的现场命令。私有文件必须属于当前
执行用户，目录0700、文件0600。受保护生产目录继续被isolated守卫拒绝。

## 状态与中断

RESTORING → PREPARING → APPLYING → VERIFYING → ROLLING_BACK → REHEARSAL_COMPLETE。
每个阶段持久化后才执行下一步；工作目录独占锁避免两次续作交错。进程在移动
中死亡时，原事务按源/归档位置恢复。恢复阶段检查备份的完整清单、文件摘要及
账本状态，归档后核对所有未移动文件和归档字节，回滚后核对完整原清单。

普通异常尝试回滚；失败保留RECOVERY_REQUIRED，之后只继续恢复，成功后仍记
REHEARSAL_FAILED_ROLLED_BACK。已完成流程重放也重新验证源副本、事务清单和
证据，不凭旧状态字段直接返回成功。准备阶段写坏或缺失的事务清单会拒绝继续，
保留所有材料供检查，不删除文件或猜测遗漏的归档范围。

摘要中的selected_files是计划选中数量，不代表失败时实际移动数量。所有结果
保持production_changes=false、all_references_proven=false、apply_available=false。
成功只证明该冻结副本的演练，不证明现场引用、停写、服务恢复或生产维护完成。

## 验证与剩余集成

使用实际executor备份和含待恢复订单的账本测试，不以mock替代备份或移动实现。
覆盖原源文件不变、逐字节回滚、真实子进程os._exit后续作、移动异常自动回滚、
回滚异常续作仍保留失败、输入篡改拒绝、完成后内容/事务证据损坏拒绝及CLI摘要。
与留存和服务维护联合50项通过（10.16秒），Ruff、治理及仓库卫生通过。结果位于：
/home/kerstker/quantos-dev-output/demo-maintenance-services-20260924/workflow-final.log。
入口缺失及事务证据检查缺失的红色日志均保留。

该增量晚于8f9b00cda，不继承其5087项通过结论。尚需把现场停写、当前引用复查、
原身份备份和生产归档事务接入同一受控操作员入口；本工具无生产apply开关。

## 现场只读单入口已准备

源码固定为15c2803acdc37f63bd5e3249d31dcd87d2d3d90f，实际提交钩子通过、工作树
干净。输出根/home/kerstker/quantos-dev-output/maintenance-rehearsal-20260924包含
operator_rehearsal.py、operator-bundle.zip及OPERATOR.md。入口绑定ZIP摘要，root
只重新做限定引用复查；短时worker以原执行器UID、禁网、原目录只读挂载、配置
凭证路径不可见的环境完成私有备份及隔离演练，安全摘要直接落到可读取目录。
不停止现有服务，不移动生产记录，不配置sudo或常驻任务。

已安装c34解释器（Python3.12.3）上的8项流程测试通过；辅助入口测试覆盖包篡改、
路径/类型限制、摘要白名单、非root拒绝及真实包+解释器的隔离端到端运行。
现场sudo -n明确返回需要密码，所以需要操作员启动一次此只读入口；不要把这项
真实备份验证包装成生产容量已恢复，也不要再让操作员手工复制完整JSON。
