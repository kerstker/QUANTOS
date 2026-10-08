# Gold 研究证据持久化与完整归档恢复增量

状态：本地开发增量，未现场安装；完整系统交付仍未闭合。独立工作目录为
`/home/kerstker/quantos-dev-output/gold-delivery-gates-20261003-26xi7ijf/source`，
基线为 `2c72782f184d33822cc261b93852aecc76565348`。
本记录承接 [9 月 20 日研究优先决策](2026-09-20-dgx-research-first.md)
及 [终端观察恢复](2026-10-03-gold-closure-reobservation.md)。

## 原问题与交付边界

父版 `1a6908c66e649f363b5ed285918c7d9386eeba32` 的 canonical ALL
为 31 PASS、1 TIMEOUT、overall FAIL。原执行区 3600 秒超时及六个失败均保留，
没有重启原任务或给新版本继承旧 PASS。六个失败在确切父版独立复现，定位为
测试没有接原角色路径隔离；联合顺序另发现 closure 惰性导入残留上一测试路径。
两文件夹具修复后同父版覆盖 98 PASS/101.89 秒，生产授权边界未变。
同一独立全执行区诊断已于 3600.339 秒 TIMEOUT/124，6154 PASS、2 SKIP、
失败报告 0；2071 未启动，1 被中断。源码前后相同，原实例已回收。
单独尾部诊断先完成 1368 项后，外置计时插件与原测试的 time_ns monkeypatch 冲突，
保留 INTERNALERROR。只修外置插件后，剩余 704 项通过/26.61 秒；各次源码前后
相同、全部进程回收，无新产品 FAILED 节点。三次分段诊断不能拼成一次 canonical PASS。
完整交接位于 `gold-1a-execution-diagnostic-20261003-bigh881c/diagnostic-handoff.json`。

既有普通 namespace 归档遇到真实 Gold enrollment 会拒绝未知目录。
新增能力必须保留完整旧 BTC/Gold 历史、原现金与执行账本、双侧留存、历史源码、
试验记录和原永久身份承诺；提高全局限额或只放行目录不构成恢复证明。

## 研究证据链

中立合同层复用原运行身份、Decimal、定频统计及 trial 链算法；旧模块保留
调用兼容，不改旧摘要、账务公式或权威 False 门。新增类型化身份运输有深度、
节点、字节和数值文本边界。原 archive 的 Gold definition 摘要也抽为共享函数，
ASCII JSON、排序、分隔符及原三处生产调用保持一致。

固定读者从可信组合根接收原件路径、完整原 TrialLedger、源码目录及信任配置。
逐项无跟随读取、原文件/目录身份重核、完整 Python 源码枚举，SQLite 只打开
DB/WAL 私有副本。保留原计划/失败/取消记录；选定 archive 的 ledger 不等于
全项目 trial 人口。签名的 SOURCE/RESULT 域分离，当前 key、撤销、期限及原件
在消费末端重新核对；历史 checked_at 不成为永久权限。

原生 producer → 原 ledger → 双签 → 固定读者 → 新鲜重验与投影的
16 组联合 384 PASS/32.03 秒；所选 23 文件前后摘要一致。不可与其他重叠
套件数字相加。准确源码副本及摘要在输出根
`root-research-selected-snapshot-0744/handoff.json`。
审查发现隐藏目录 Python 遗漏及共享大字符串/Decimal 预算过晚，已原样先红后绿。
当前 risk adapter 尚缺，资格仍为 INSUFFICIENT/REJECTED。

原 registry 的版本化持久化另包在实现：普通未 sealed 库显式本地升级，保留
原七列、旧 payload 与原 trial 前缀；默认新库版本不改，不暗升。真实
family seal/authority 库拒绝 plain 升级。新评估、原 search 关联及 audit outbox
使用原事务；冷读只证明历史完整性，准入必须当前重验。最终 9 组联合
264 PASS/99.22 秒，6 文件及此前 C4 摘要已独立核对；交接为
`c-registry/handoff.json`。全体原 search_candidates
及原 search 身份、时间、状态纳入人口核对，v4 同时核原 provenance session/config。
这是有明确一对一映射的首次接入；跨项目多 ledger 的全历史适配仍未闭合。

V1 授权 ENABLE 同次持锁操作实测重复完整构建原研究定义 25 次。原样回归
先失败，再限定同次操作共享 PreparedBarDefinition；仍至少 25 次读取完整原快照，
保留所有策略、账本、权限与末端变化检查。最终 64 PASS/349.73 秒，8 选定文件
前后摘要相同，清单为 `root-definition-session-handoff/handoff.json`；不构成跨操作
缓存，也未声称 V2 全路径或新版完整门已达标。

## Gold 归档运输

原 namespace 模块增加显式 Gold V3 入口，旧 V1/V2 行为保留。
受控 role capture 的原 DB/WAL/SHM 均以同一稳定字节切片存档，不能把
逻辑 SQLite backup 与旧 WAL 拼接。语义验证只开私有副本，展开原件前后重核。
每卷仍受原 100000 payload 成员、20 GiB 及 32 MiB 元数据界限约束；
index 与源码 bundle 同样占卷配额。提取前由原组件合同推导总预算，
不相信归档自行声明的新配额。

归档包含 current 及原 runtime 所需 archive commit 的确切源码 bundle，
最多两份、同 commit 去重，拒绝无关附件。持久身份绑定源码内容与 bundle，
不绑定原机器地址；不把任意路径或自报 verified 字段作为信任。
真实跨地址、删除原源码/bundle后的恢复、非空 WAL、加密与多卷覆盖已验证。
运输在首个原状态 FILE 之前先验证实际源码树，再推导精确组件限额；压缩输入
在限内但源码展开过大的两项原样反例先失败，修后运输 21 项通过。
源码证明范围只是 Git bundle 与原生 identity；未证明新 release 验收。

独立封装入口读取固定预期 UID 的已完成私有 capture，逐份稳定复制后先实际
恢复，再用原加密合同封装全部卷，最后再次完整恢复。捕获者无需备份口令，
封装者无需 live role 目录。运输/封装/原 namespace/cycle 联合 114 PASS/40.53 秒，
该阶段之后末次 fsync 的原件替换反例又实际 1 FAIL；已把最后身份重核移到
fsync 后、发布前，原反例 1 PASS/10.47 秒，永久封装 9 项通过。此前弱口令
测试误捕 ValueError 的失败保留并改为原 BackupPassphraseTooWeak 类型。
最终 4 组运输/封装/原 Gold 身份与 archive 联合 40 PASS/227.15 秒，整棵
services/quantos/tools/scripts/tests Python 前后摘要相同；原生组件及兼容
10 组联合 304 PASS/306.96 秒，7 选定文件前后相同。数字有重叠，不相加。
新完整源码提交、整合及验收记录绑定在输出根的相应工程工件中。

源码恢复另以实际 Git blob 头和原始内容核对 SHA1 与 SHA256，拒绝 checkout
属性导致的同长度转码；反例先失败。clone、列树、checkout 及直接调用的原
verify_source 在固定环境的受限子进程执行，保留 pack、边读边限输出，超时
终止并回收进程组。新增防护为每进程 AS 2 GiB/CPU 120 秒、每文件 20 GiB，
墙钟 180 秒；不构成宿主总资源保证，也不扩大原组件额度。真实 128 MiB 历史
对象压缩包在低 AS 下于 clone 阶段拒绝，同限制小包正控通过。

维护切片说明、完整 public 材料、inventory、role receipt 的交叉摘要全部核对。
外部审查两条原样反例先 2 FAIL，修复后 2 PASS，已加入永久回归。
恢复输出明确 `production_resumable=false`、
`complete_gold_set_verified=false`，不能冒称整组或生产恢复。

## 尚待闭合

- 完整 BAR/legacy/双侧留存和跨角色因果关系、research publication 的归档与恢复。
- 原维护流程中的真实 writer barrier、受控 role launcher、固定受验源码 provider、
  完整 set 的运输/复核，以及安装、升级、恢复演练。
- 原 registry 完整持久化/重验消费与真实风险、锁定 OOS、paper/human 门衔接；
  不替代独立人员和原授权证据，也不提前授予经济资格。
- 新冻结提交自己的 hooks、DGX、ARM64、canonical ALL 与留存验收。
- 现场完整五秒链及获准 24 小时成交—现金—再研究闭环；多资产分别验收。
  24 小时是验收窗口，不是剩余工期；机构异机事项仍按研究优先决定延期。

本批没有部署、切服务、写保护运行状态、访问真实凭据或下单。完整系统目标
继续进行；盈利策略研发另列，不把技术证据改标为策略盈利或可交易资格。
