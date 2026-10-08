# Gold 原 K 线摘要的不可变标量序列化

状态：DEVELOPMENT_VALIDATION_ONLY。沿用
[固定 F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)和
[原源码读取约束](2026-10-06-gold-source-relative-paths.md)；不改变完整门、
原五秒链或实际 24 小时验收。

## 证据与最小变更

原 438f 的 canonical ALL 终态为 31 PASS／1 TIMEOUT，执行阶段达到原
3600 秒限时，最后记录为完整 1001 片段队列案例。冻结 21eab 的原案例
另作有界诊断，全部片段、原测试和源码保持；不是新完整发布验收。
真实 120／240 秒线程栈均在 GoldBarStream → journal restore →
gold_bar_candidates.input_digest 的 dataclasses.asdict／deepcopy 路径。
这只识别已观察热点，不能单凭栈声明它是全部超时原因。

首个辅助诊断错误地启用多个 cProfile 实例；当前 Python 3.12 的真实线程
夹具复现 `Another profiling tool is already active`，原诊断在核对 PID／
start 后受控停止，276.183411711 秒，3132 Python 前后保持。错误和旧
栈保留，不算原案例失败或超时。单实例真实夹具确认包含线程目标后才
启动后继；原 900 秒诊断预算和 880 秒部分结果落盘边界保持。
该行为与 [CPython 3.12.3 原实现](https://github.com/python/cpython/blob/v3.12.3/Modules/_lsprof.c)
及 [解释器级监测](https://docs.python.org/3.12/library/sys.monitoring.html)一致。

先有 12 个实际失败回归：完整与增量读者、三个候选、连续与缺口输入均
已得到原正确摘要，但 402–404 根 K 线仍触发约 1600 次 Decimal 深拷贝。
原日志／JUnit 保留。操作次数用确定的不可变字段语义约束，不用机器
耗时阈值，也没有减少输入或改变过期、可用时间、缺口和完整历史要求。

Bar.validate 已要求三个字段为确切整数，四个价格为确切有限正 Decimal。
新私有序列化器只列出原七字段，四个价格仍用 str 保留原 Decimal 字面值。
原 json sort_keys／separators／allow_nan、SHA-256、完整与增量数组格式、
每根原 Bar 验证、全历史 EMA／指标、完整身份和各次源码／物理原件复核保持。
没有缓存来源证明或跳过历史。修改只消除对不可变标量的递归深拷贝。

## 当前验证与交付限制

摘要、原候选及流式读者三组 28 PASS／0.26 秒，新增操作回归不再深拷贝，
原 dataclasses.asdict 的独立完整 canonical 字节摘要一致。原增量数学、
日志恢复、分段回放及长预算短案例四组 29 PASS／1 deselected，34.97 秒，
driver 35.324785970 秒，3132 Python 前后保持。被暂未选择的正是原
1001 片段完整队列案例，必须另行完整运行；不计作该案例或完整门通过。
两个修改 Python 完整 Ruff、现有服务依赖图 check 和 diff 检查通过。
服务依赖图、研究持久化及经济 authority 三组另 21 PASS／2.30 秒，
driver 2.623788722 秒，3132 Python 保持；全 E9／F821 和仓库卫生通过。

独立目录为 `gold-project-closure-20261003-3pr5mrly/gold-long-queue-performance-20261006`，
父版 21eab2188a9ac956759f04f83151f476bcd5cd3b。精确 DGX 开发入口记录
`/tmp/quantos-dgx-development-et9gghji/baseline.json`，Python 3.12.3／ARM64、
原锁／Node 核对；该入口没有运行 --test，不当作完整验收。

最终同版完整发布、长期因果输入及独立来源／结果／时间／真实成本风险／
预锁 OOS、现场全部 writer 同 cut 现金恢复、获准 Demo→现金→再研究四组
满足即交付，盈利策略随后单列。旧完整门失败保留，新版尚未完整验收；
没有部署、切服务、保护状态写入、实际 trust 安装、私钥／交易凭据读取
或下单，不把诊断或纯组件绿色当作系统交付。

## 冻结后的实际复核

三路径独立冻结为 df3b5a8031cb758de7f13059959dade47db5f1d2。仓库钩子
不可执行，已在验证解释器 PATH 上显式执行原 sh 钩子，790 文件、零
治理违规通过后才提交；没有 chmod 或 --no-verify。

该提交的原 1001 片段完整队列案例实际 1 PASS／344.38 秒，driver
344.719124724 秒、3132 Python 前后相同。全部片段、原四账户各 1001
环境片段、期初权益 1000／零残差、完整项目读取和冷恢复断言均保留。
这是原完整案例的技术证据，不是实际长期采集或完整发布门。

旧冻结 21eab 的后继辅助 cProfile 诊断在 840.313195406 秒以 signal 11
终结，3132 Python 保持，未产出 pstats 或部分 flush。实际栈、异常和
全部报告保留；原因未证实，不据此推断系统案例或原 900 秒限时终态。
没有重复启动该分析器，也没有借它声明耗时改进。

备份后仅把上述三文件整合到 Main；1392 个未修改运行模块逐项相同，
7999→8000 Python 只新增一测试文件并修改一生产文件，其余内容保持。
Main 四组 37 PASS／8.38 秒，driver 8.958667037 秒，8000 Python 保持；
Main HEAD 913325cc／原 index 保持，不暂存或提交 Main。

同一 df3b5a80 的完整发行验收已在独立目录
`gold-release-acceptance-20261006-scalar-identity` 实际启动：session 5247，
coordinator PID 1893520／start 34812774。原六驱动只替换完整提交绑定和
实际解析的公开 IPv4，TLS 原主机、ALL 与原 3600 秒参数不改。
离线前端安装／原锁核对，3133 Python 冻结；free 451768795136 满足原
412451078144 数据预留加 28 GiB。仍无完整门终态，installed=false；
旧 438f 的 31 PASS／1 TIMEOUT 保留，不借其 ARM64 或局部成绩。
