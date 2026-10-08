# 原收尾观察过期后的有界恢复

状态：开发接线已冻结，35 组最终同版联合验证通过；这是增量开发验收，
本版自身完整发行验收和现场运行验收尚未完成。
父提交为 `1a6908c66e649f363b5ed285918c7d9386eeba32`，独立目录
`/home/kerstker/quantos-dev-output/gold-closure-reobservation-20261003-wwmtdtfw/source`。
父版本自己的 ARM64 / canonical 验收独立继续，不修改其冻结源码。

## 已复现的缺口

原 closure intent 与账户 origin 已持久化，但停止记录尚未写入就中断。
在原 origin 完成时间后 31,001ms 重启，四条真实管道均无法完成收尾：
已成交 bar、无信号轮转后的历史 bar、保留旧 BTC 的本次无成交、legacy。
独立父源码的四项失败位于 `a-reobservation/native-parent-controller-red.log`，
耗时 20.36 秒；父生产代码无修改，输入摘要在 `baseline-inputs.json`。
当前开发树缺新接口的失败另行保留，不能替代上述真实缺口证据。

## 有限协议

- 保留旧 intent、claim、origin、原现金反馈和执行历史。原过期尝试明确保存为
  `EXPIRED_BEFORE_STOP`，不称停止、成交或当前账户已验证。
- 只允许一个后继观察。原生留存先补完整旧诊断，才由原 store 在真实当前时间
  分配唯一下一 claim。旧窗口、1 秒间隔、帧/记录/字节/物理预算保持。
- 新账户采集在原 writer lock 内、实际 GET 启动前分别持久化两份固定 capture
  witness，均 fsync/readback 后才采集，写入前后重查实际时间。任一件单独缺失
  或不一致都拒绝；两件齐全但无完整 origin 时保留 UNKNOWN，不自动再次 GET。
  FAILED origin 保留为真实不完整尾部。全部见证同时被可信代码删除仍无法与
  首次采集前中断区分，不声称覆盖完整历史的可信删除。
- 第二份 origin 再过期时停止自动恢复，不产生第三次尝试。已经真实写入且匹配
  origin 的 STOP，仍可按原实际停止时点补齐结果，不重采或改写时间。
- controller 和 executor 各自审查本角色实际存储余量。固定新链先通过完整原件
  核验，再镜像诊断；普通孤儿、半帧、丢原件或错误来源继续拒绝。
- 最终成功保持原 closure result 形状、原 base closure request 与原窗口，
  intent 摘要指向新链。控制端核验新链和双根留存后才报告完成。

冻结设计、接口及 39 条故障矩阵保存在父输出根的 `reobservation-design/`；
交叉审查在 `reobservation-review/`。双见证及后续边界修订已形成当前输出根的
`c-reobservation/design-amendment.json` 与 47 行 `fault-matrix-final.tsv`。
这些材料不是现场运行证明或操作授权。

## 分工与当前证据

C 负责严格新协议、原 closure/worker 接口、采集锁内回调和执行侧回归。
B 负责原生读者、同 owner 恢复、严格诊断镜像与控制端预算预检。
A 负责真实 private pipe 的四类恢复与二次过期/未知采集反例。
root 负责 legacy 读者、owner 和 controller 接线、整合与最终验收。

控制端新增六项回归先失败，随后与旧有限交接共 15 项通过。最终链的 JSON
布尔/数值别名另有两项真红，改用摘要核对后相关 18 项通过。旧格式读者接入
固定新文件、精确诊断尾部和双读核验；旧读者与控制交接 99 项通过，新增实际
legacy 预检 5 项通过，拒绝不足记录数/字节/单记录预算及改写请求，原件与
采集次数保持。完成态错误调用未来预算模型另已先复现红，改为仅缺镜像且无
实际原件时计算上界后，六项 legacy 检查通过 / 4.51 秒；完整终态恢复不再
依赖未来模型或新窗口。root 最后八组控制端与旧读者联合 142 项通过 /
10.21 秒，六份归属 Python 的 Ruff E9/F821/F811 及 diff 检查通过。
上述局部数字重叠，不累加为最终同版证据。

A 的原生组合首批 5 项通过 / 35.03 秒，legacy 已成交路径另 1 项通过 /
2.40 秒；各次九份关键生产源码前后摘要一致。覆盖真实原账户 origin 写后
退出、31 秒后唯一追加观察、真实 STOP、两侧留存及冷 supervisor；未知采集
不重复 GET、第二次过期不创建第三 claim。原 issuer 消耗、回执原件、执行
状态和旧 claim/origin/结果保持。网络、宿主身份与源码接受仍为明确隔离输入，
不是现场账户运行。最终七项实际管道已在共同版本通过 / 36.47 秒，包含新增
legacy 无成交路径；16 份本批 Python 前后摘要一致。最终交接为
`a-reobservation/a-final-seven-handoff.json`，旧阶段清单保留。

另一个真实边界已在 `a-review/legacy-idle-red.log` 复现：旧格式本次无成交时
原 `strategy-decisions` 目录不存在，追加首条过期诊断后新建目录，原快照比较
误拒绝 `REOBSERVATION_LEGACY_CHANGED`。现仅接受固定原 absence 加该链精确
新增成员，已有目录身份继续核验；实际 controller 管道先红后绿 1 项 / 1.49秒。

审查另在原生采集实际 16 GET 返回后、origin 未落盘时强制退出。保留单见证时
重启正确拒绝，但删除唯一见证后会重复 16 GET；原反例 1 项失败 / 1.20 秒。
双见证修复后分别删除主件和第二件的实际反例均拒绝且新增 GET=0、无 STOP，
两项通过 / 1.26 秒。审查原件及摘要见 `a-review/review-final.json`。

原生读者两文件已冻结，最终新增 55 项通过 / 28.83 秒；此前兼容联合 124 项
通过 / 73.65 秒是较早阶段，不能冒充当前同版联合。原 store、配额及 DDL
保持。native public inspect/preflight 沿用原 owner 恢复语义，可能补齐已认证
的既有控制端镜像；容量计算本身只读，不创建新的执行 claim 或发起 GET。
legacy inspect/preflight 保持只读双快照。最大历史下 30 秒恢复时效尚未证明。

执行侧六文件最终七组联合 124 项通过 / 203.74 秒，前后摘要一致；STOP 已
持久化后的时钟回退先红后绿，已有 legacy 目录被替换仍拒绝。控制侧、执行侧
与实际管道合并的 35 组验证最终 695 项通过 / 596.19 秒，退出0、完整 Python
树前后摘要一致。结果留存在 `root-final-joint.log` / `root-final-joint-exit.json`，
各阶段数字与此重叠，不相加。原失败及夹具错误日志均保留。
16 份本批 Python 静态检查、20 域 52 边无环服务图及卫生检查通过。
17 个待整合路径与主工作区父前像均匹配；提交和整合的实际结果分别以
`final-source-commit.json` 与 `integration-summary.json` 为准，不能由预检推断。

父 1a6908c6 自己的 canonical 执行测试已出现六项失败，原任务尚未终结；
新批开发证据不替代父结果，也不继承其他版本的完整门结论。

## 完成条件

必须验证正常恢复、所有持久化边界中断、错误或缺失原件、原额度不足、锁等待
跨期限、第二次过期及匹配 STOP 历史补写；原原件、钱包、账本、反馈与窗口不变。
必须经过实际 worker 管道、原生 store、原停止记录、双根或 legacy 镜像以及
tick / supervisor 完成核验。阶段测试不能替代最终冻结源码的完整门。

本增量不改变订单授权、Scope、mandate、停机门或经济资格；不部署、切服务、
读取真实凭据或操作真实账户。系统完整交付、现场运行验收及多资产范围保持
原目标，策略研发单列。

## 整合与回退

仅将本批17路径整合到开发工作区：先核对父原字节与主 HEAD/index，备份到
输出根 `main-before/`，逐份再次核对后原子替换。主工作区不提交、不暂存其余
修改；每份前后摘要写入 `publication-preimages.json`，补丁与独立源码提交
留作审查。任何后续回退都须先确认目标文件仍等于本批后像，再恢复原备份；
本批新建文件只有确认无后续修改时才可移除。不能据此回退账户、账本、实验、
签发历史或受保护运行状态。

相邻交付审查已真实复现原维护 checkpoint 不覆盖 Gold enrollment 的问题；
普通 namespace 正控制成功，登记 Gold 后 capture 被固定清单拒绝。此缺口及
独立经济来源软件接线另行开发，不在本批 695 项通过结论内。
