# 黄金维护窗口、完整研究 UID 与在途归档恢复

承接[受控研究启动](2026-10-03-research-controlled-launcher.md)和
[原件内核读租约](2026-10-03-original-file-read-leases.md)。旧维护集合没有黄金
pilot 和研究 runner，进程观察只覆盖执行/签发 UID；另一个没有打开状态文件的
研究进程会被遗漏。原恢复入口也不知道新增研究捕获是否还在运行，不能在此
基础上恢复写入服务。本增量把这些条件接入原维护与原研究捕获流程。

## 固定范围与原日志兼容

[服务维护模块](../../../tools/demo_maintenance_services.py)新增显式 Gold 后端：
原 Demo 服务/定时器、固定 feedback 对、quantos-gold-pilot.service 和
quantos-research-runner.service。完整声明集合才能形成
GOLD_SCOPED_SERVICE_MAINTENANCE_V1；省略、任意补充或把旧日志改标签均拒绝。
旧 V1/V2 范围和后端保留，不能从旧暂停记录推导新增服务已停止。

沿原 root-owned unit 配置摘要、有效启动抑制、排空及恢复顺序验证，不降低原
条件。抑制安装/删除仍先核对整组路径，不删除其他 drop-in。黄金 pilot 和
runner 属于常驻服务，启动后仍 inactive 不能按 oneshot 规则报告恢复成功。
新服务缺失同样拒绝，没有增加可选缺失放行。

[进程观察](../../../tools/demo_maintenance_processes.py)从实际
VerifiedResearchLayout 和原固定角色根取得全部研究 UID、执行/签发 UID 与
研究/控制等原件目录。调用者不能提供 UID、路径或豁免名单。仍只读 /proc
元数据，不读命令行、环境、映射内存、原状态内容或凭据，不按 UID 杀进程。
两个稳定扫描和原清单前后复核均通过才能报告当前未观察到相关进程。

研究 UID 一旦匹配就已经阻断流程，不需要再读取该 UID 的 fd/maps；实际身份
仍前后核对。未知、权限不足、PID/成员变化均为 INCOMPLETE。Root 的研究目录
句柄、映射、工作目录和 Gold/研究服务 cgroup 也在当前观察范围内。

## 原维护窗口与研究捕获交接

[原窗口模块](../../../tools/demo_maintenance_window.py)新增
GOLD_INHIBITED_SERVICE_WINDOW_V1，绑定原 operation、boot、源码、完整 layout
摘要和窗口 inode。暂停、观察与恢复沿原维护实现，服务范围由日志版本决定。
黄金窗口拒绝旧两 UID 检查与旧 executor retention 操作，避免把旧材料升级为
完整黄金归档证据。恢复不依赖已经失效的旧研究句柄来清理原服务。

capture_gold_research 沿原 operator.exclusive 锁执行：准备固定服务记录→
抑制/暂停→完整 UID 检查→取得原件实际读租约→原受控研究归档、原生恢复与
实际退出→完整 UID 再查→释放租约→恢复原活动服务。所有实际服务操作均是
独立的 Root 操作员动作，本次只运行隔离测试，没有在宿主调用这些操作。

研究启动前持久化 GOLD_MAINTENANCE_RESEARCH_CAPTURE_V1 的 RUNNING 记录。
原生成功且 worker 真正退出后才记 COMPLETE；失败路径必须重新证实全部
研究 UID 已退出，才能记 FAILED_CLEANED。RUNNING、错窗口、坏绑定或不明
记录阻断恢复，不能只依赖 finally 启动原服务。原中断材料和归档保留。

恢复入口 recover_gold_research 只在当前完整 UID 检查为空时清除在途阻断，
记录中断任务清理；不造归档成功、worker 成功退出或历史结果。仍有进程时
保持暂停，不触碰该进程或用旧空闲报告代替新检查。

## 验证范围与完整目标

固定源码为 4c538077 父版加精确七个 Python 文件 overlay。最终十五组普通
联合 191 PASS/21.25 秒，三项内核测试 3 PASS/87.85 秒，各自全部 3011 个
Python 文件前后相同。原 driver/pytest 已退出并回收 exit0。原 Scope 缺口
先 15 FAIL/1 PASS，修后四组 75 PASS；在途恢复反例修前失败，修后拒绝恢复。
首次扩展内核组的注入类名错误为 1 FAIL/2 PASS，纠正后最终三项通过。
隔离 fixture 的旧分区人口、Root custody/私有父目录/纳秒元数据和未改变值的
损坏断言修正只发生在测试输入，原错误日志全部保留，没有降低生产检查。

三项内核场景分别完成两个真实研究 UID 的完整原归档/恢复与退出；实际空闲
研究 UID 仍存活时拒绝恢复，实际退出后才记中断清理；原生 PREPARED 后注入
异常，真实 worker 回收后记 FAILED_CLEANED 并恢复原活动服务。最后两项都
不制造归档成功或 worker 成功退出记录。

原失败、每版独立 Git 对象副本、全部 Python 前后摘要、日志/XML 与终态位于
gold-project-closure-20261003-3pr5mrly/root-full-maintenance。普通测试的
Root custody 和服务管理器为明确隔离输入；内核测试另外执行真实 Root 锁、
实际抑制文件、完整研究 UID、原件租约、原生产者/试验、原归档/异路径恢复和
真实 worker 退出。服务管理器、公件/accepted-release 仍是夹具边界；其他
三个角色原件根是实际属主夹具文件，本轮不证明其原生归档恢复完成。

该服务集合仅覆盖声明的固定单元，不声称穷尽其他调度或未来手工作业。当前
元数据扫描、协作操作员锁和现有文件租约均不能排除未来 Root、目录改名或
所有 namespace 别名。quiescence/manual exclusion、writer_barrier、whole_set、
经济与下单权限继续 false。原 cut 在暂停与租约之前已存在，不回溯生成同 cut
停写证明；现场完整 writer 范围和正式捕获切面仍须闭合。

完整目标仍包括四角色同切面原生归档/恢复及维护、统计/锁定 OOS/基准/实际
风险受签消费与候选准入、最终同版 ARM64/canonical 完整门、获准现场安装/
升级/恢复、五秒链及实际 24 小时成交—现金—再研究。最新完整门保持旧冻结
版 30 PASS/1 FAIL/1 TIMEOUT，不以本批专项替代。机构异机设施延期，多资产
逐市场验收，策略盈利研发单列。
