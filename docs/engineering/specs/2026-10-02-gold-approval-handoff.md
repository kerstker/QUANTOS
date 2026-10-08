# 黄金待批通知与人工安装租约交接

承接[执行证据与轮换](2026-10-02-gold-executor-evidence-and-rotation.md)。本增量处理真实pilot的待批请求不可见及人工安装时轮询错误退出；不代表五秒人工批准、现场交易或完整系统已验收。

## 原请求及时通知

原controller已留存决策和独立批准请求，但丢弃请求返回值；pilot直到派发等待结束才输出普通进度，操作员不能及时获得安装入口所需的stored_request_sha256。

通知使用独立事件，不向worker原result添加字段或更改其摘要。stored_request_sha256是完整留存request的_digest值，不能与其内部request_sha256混用。事件引用source、结果、精确constraints、共享预算和原决策期限，批准、下单与经济权限均false。

只在最终帧worker成功结束、controller退出共享租约后发出，再进入on_decision/dispatch等待。pilot沿原on_progress立即输出并flush，落入原system journal；不发送外部消息、不自动运行人工入口。中间帧仍可能持有共享租约，不发布可安装通知，也不宣称多帧人工交接已闭环。

## 维护租约忙的有界等待

人工policy安装器需要原controller排他维护租约，派发器轮询申请非阻塞共享租约；原代码将CONTROLLER_MAINTENANCE_BUSY变成INPUTS_UNRESOLVED并使pilot退出。

新增WAITING_CONTROLLER_MAINTENANCE仅用于进入controller_lease前的精确ValueError。此时本次调用尚未读取安装配置或材料化任务，返回原source/result引用、task_sha256=None及false权限/重试标志。成功进入租约后立即切换phase；配置、材料化、原task或claim后出现相同文本错误仍停止，绝不因名字相同重试。

tick只对原待独立批准或完整绑定的专用租约等待态轮询；仍限制在原30秒进程预算与决策deadline的较小值内，每次重新读取原安装状态。首次调用前也检查deadline；到期不再派发、不续时间。任何未知状态、错误绑定、已有任务、claim后丢响应均停止本次等待；原恢复/QUERY_ONLY继续负责已有任务。

## 验证与隔离

原版真实两进程目录租约回归5失败/4通过；dispatcher单独修复后5相关项通过。子进程实际使用原controller_lease/flock，任务材料化和claim保持原生；controller权限、策略policy、市场前缀和账户事实是明确隔离替身，无实际发送。

独立审阅发现测试握手readline无界，现复用原read_frame，明确64字节与5秒deadline；stderr保存在隔离临时文件避免管道堵塞，失败时最多输出16KiB，进程结束/清理也有期限。修正后5项通过/1.61秒。

通知首次回归11失败/7通过，复现原pilot CLI直到等待之后才输出以及初次派发没有检查deadline；失败日志保留。通知独立八组165通过/11.97秒。合并真实dispatcher与两进程租约后，同版十三组229通过/57.90秒，覆盖成功交接、保持锁至到期、释放后policy/runtime改变、假等待或claim后错误、失败worker/尾部异常/留存失败不通知、通知回调失败不派发，以及原Scope/证据/轮换边界。上述数字互相重叠，不累加。测试时钟、操作员、密钥及账户事实仍为隔离替身，不证明真实人能在五秒内完成审批。

独立只读审阅未发现新增冻结阻断；Ruff E9/F821/F811、无环服务依赖图及diff检查通过。本增量独立开发根为`/home/kerstker/quantos-dev-output/gold-approval-handoff-20261002-2l_vtehv`，父提交5279da0203d51ec9324dfbe7658211bb93850130。完整验收只认本版自己的终态，不继承父版PASS。

## 尚未完成的交付

即时通知与等待只修复交接，不为人工逐笔确认提供五秒时延保证。下一架构见[有限人工执行委托设计](2026-10-02-gold-bounded-execution-delegation-design.md)：明确新的人控合同和独立issuer逐信号核验，旧Scope不获得新权限。32个episode与24小时观察的容量冲突、反馈配置预检、真实期初与收尾、独立经济来源、安装启动及现场运行/资金闭环、多资产和运维验收继续待完成。未部署、切服务、修改实际授权或使用凭据下单，策略研发保持单列。
