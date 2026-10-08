# Demo 反馈配置升级与原维护窗口恢复

状态：独立开发已与首次 Scope 源码组合，专项联合验收通过，尚未部署。初始父源码为
`dee3d59a1973f446b8873133e54dc9a1a70ad364`，工作目录为
`/home/kerstker/quantos-dev-output/gold-feedback-upgrade-20261003-s42qzg8l/source`。
已快进至[首次 Scope 增量](2026-10-03-gold-first-scope-delivery.md)
`e6fb746b42f08bfe286c9938750018cadaa8519e`，40 个继承文件与本批 22 文件
逐份核对无冲突、无丢失；未修改原首装验收目录。
本记录延续[完整工厂交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)。

## 原问题与范围

原维护窗口绑定当时完整的服务范围、配置指纹和活动状态。直接安装反馈
配置会将范围由 42 项变为 44 项；更新已有 44 项的定义同样会改变指纹。
原 `UNIT_SCOPE_CHANGED` / `CONFIGURATION_CHANGED` 拒绝正确，不能改写旧
journal 或放宽普通 restore 来假装已经批准新配置。

本增量增加独立反馈配置转换合同。仅处理两个固定 systemd 定义和两个
固定反馈 JSON 配置；原 issuer、claims、现金、试验及研究历史不在写入范围。
新旧配置、完整原活动集合、明确批准的新活动集合、源码、原 operation、
原窗口及消费者原件共同绑定到不可变 review。

## 文件与维护事务

四目标的原字节、所有者、模式、inode 及父目录身份在第一次目标写入前
持久化。每次替换都重新核对原件或本操作已登记的新 inode，并在写入前
调用维护 guard；固定文件逐项替换、fsync 和读回。首次发布使用原子
不可覆盖 rename，不以 link/unlink 的中间双链接状态充当可恢复完成。
回滚一旦开始便不能转回 apply；恢复原字节、UID/GID 和模式，不声称恢复
旧 inode。无法验证的半写 intent 或暂存残片保持拒绝，不猜测或删除。

转换沿原全局操作锁、controller 维护租约和窗口锁执行。原 42 项已停止、
排空且受抑制后，先为新增 pair 建立同一 operation 的抑制，再发布配置。
完整 44 项加载、指纹、`NeedDaemonReload=no`、进程排空和有效抑制通过后，
才允许按明确活动集合恢复。每次释放抑制前复核四文件完整 CAS。
旧 services/window journal 保持原件；新转换存在时普通 restore 明确拒绝。

无批准取消仅允许目标仍为原件的准备态；此时解除新事务阻挡，沿原窗口
恢复。已批准中断沿同一 review/approval 续做或单向回滚。成功后的显式
回滚也使用原 review 中展示的配置和原活动集合，不自动回退成功结果。

## 消费者合同与人控入口

现有 renderer 唯一消费者是 `demo_functional_cycle --feedback-pending`。
纯预检复用原完成记录与反馈回执选择逻辑；仅支持同源、exact policy、
`INSTALLED_PENDING_CYCLE` 且已完成的 Gold functional。普通 BTC、portfolio、
Scope 以及持续 Gold strategy/bar 均不能冒充该消费者。bar/strategy 原发布
合同即使有效，也只说明直接发布相容，`timer_compatible` 仍为 false。

受保护捕获只读固定两侧 registry、policy、真实 active setup、原 spec/result、
task、claim、完整 recovery 和 receipt 目录。源身份、属主、nofollow、长度、
数量和摘要限制保留；两遍读取检查变化，不宣称原子快照。人工确认前后
严格复核所有原件和 inode。仅在维护事务持久记录允许恢复服务之后，才可
接受同源、同已完成 cycle 的合法新 receipt；旧记录及其他原件仍须完整。
这使 start 后合法反馈追加不会阻止中断收尾，也不能掩盖旧历史替换。

每次修改流程观察到的新回执必须进入持久见证，后续复核以最新见证为锚，
不能反复退回人工确认前的快照。见证只追加原回执及读取身份的差量，绑定
原 review、序号与前项摘要；独立 head 指向最后完整项。已写完整唯一下一项、
但 head 尚未更新的崩溃可以沿同一操作续完；截断、缺中间项、分叉或原件
冲突继续拒绝。只读 query 不推进见证，也不把未持久记录的新观察称为已留存。
初始空目录只可在尚未允许启动且原件完整时补同一确定的初始见证；head
在事务目录原子更新，其崩溃暂存文件保留，不混入严格枚举的见证序列。
完成回滚后的旧恢复入口也须只读检查最新见证，新增观察须回原批准流程留存。

`tools.demo_feedback_upgrade_operator` 复用真实 local sudo/TTY 和已接受
release 检查。它先展示具体 review 与活动集合，要求单独输入
`UPGRADE FEEDBACK <review_sha256>`，持久化批准后才调用转换。
默认只恢复原活动服务，新增 timer 必须显式选择。操作编号先于服务变化
输出；恢复只读取原 source/operator/review/approval，禁止用新参数替换原配置。
ARCHIVE 确认不能批准此操作，未知状态不显示恢复成功。

准备失败、人工取消和它们的恢复分支，均须在原维护锁内复核当前加载的
配置指纹、范围及活动集合后才报告恢复完成。旧 window/services journal 的
`RESTORED` 不替代本次检查；已停止的原活动 timer 或常驻 stream 不得被报为
恢复成功。外层 prepared 回执丢失时，只能从原事务 review 恢复，不另建批准。
已批准升级或回滚的终态重试同样复核实际服务；终态后的服务停止或配置漂移
明确拒绝，不凭旧状态自动重新启动服务。

## 验证边界与剩余工作

原始红绿、各文件交接及审核在工作目录的父目录保存。已有真实临时文件、
原维护状态机、固定捕获、实际文件 CAS 和 fork 中断证据；systemd/DBus、
sudo/TTY、UID/属主及 accepted source 是明确隔离输入，未运行现场安装入口。
不同局部测试集合互有重叠，最终同版联合数字以冻结记录为准。

组合后的 31 个相关测试文件同版联合 **527 通过 / 211.34 秒**，无失败或跳过，
测试前后全部 Python 源码摘要一致。覆盖新增升级合同/CAS/消费者/原生人控入口、
原维护窗口/launcher/worker/发布器、首装及原 Scope 操作员交接和持久化边界。
21 个变更 Python 的 Ruff E9/F821/F811、diff 检查、20 域 52 边无环服务图、
仓库卫生与本文相对链接检查通过。独立审查的原失败探针与其修后结果单独留存，
不与 527 累加；联合原件为父目录的 `joint-tests.log`、`joint-exit.json`、
`joint-source-before.json` 和 `joint-source-after.json`。

此为开发增量证据。本版本自己的 ARM64 / canonical ALL / 留存尚未完成；
与后续持续反馈恢复组合后须按最终源码执行完整交付门禁，不借用父源码的 PASS。

修复前已复现：已观察的新回执被删或同字节换 inode 后仍通过；取消流程的
services 已完成但 window 未记终态时错误报告成功；无升级事务的准备失败
恢复也会信任旧终态。对应原生回归及真实 fork 中断已加入测试，失败日志保留。

本增量不安装源码或切换研究服务，不建立订单授权、经济权威或真实反馈。
持续 Gold bar/strategy 的重试消费者、人控现场升级、整套恢复、真实获准
24 小时成交—费用/资金费—现金—再研究及多资产验收仍在完整目标内。
