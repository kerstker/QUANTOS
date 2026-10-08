# 分块结果的读取与 SQLite 存储边界

本批修复固定 [F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md) 的发布验收阻塞。
父源码 `2ac55474c3cf573d029155a7732c6490baf78db2`；独立开发目录为
`/home/kerstker/quantos-dev-output/gold-project-closure-20261003-3pr5mrly/gold-result-persistence-boundary-20261005/source`。
专项通过，完整同版发布验收及现场交付仍待完成。

精确父版验收在 persistence preflight 失败，未进入 ARM64 或 canonical。
失败目录 `gold-release-acceptance-20261005` 保留，源码未变、未安装。
不得将旧版构建结果或其他版本的全门结果绑定到该失败尝试。

`perpetual_chunked_result_reader.py` 原来同时解析、验证原件与建立 SQLite
索引。现将索引 schema、全部 SQL、连接生命周期、节点/磁盘预算和惰性
对象/数组视图移至专用 `perpetual_chunked_result_store.py`。原存储类逐字
保持；读取器继续执行原 manifest/blob/result hash、权限及消费前后切面
核验。该 adapter 只写私有可丢弃索引，不写原件、账户余额或试验记录。

治理清单仅登记这个专用存储 owner；读取器没有 SQLite import 或 SQL。
registry/paper core 和叶子 ports 的隔离检查保持。依赖图重新生成，仍为
20 域、52 边、零环；旧图漂移拒绝记录保留。没有放宽原门、预算、签名、
来源、经济准入或下单权限。

独立目录保留原有红例：1 failed / 2 passed，driver 0.776402 秒。
修后四文件边界/完整结果/材料读取检查 40 passed / 16.45 秒，driver
16.777972 秒；正式登记、完整报告、队列与冷恢复及图检查另 26 passed /
18.86 秒，driver 19.164324 秒。各批 3116 Python 文件前后保持，范围
不同，不将两批数字拼成 canonical 成绩。三个改动 Python 完整 Ruff 与
全仓 F821/E9 通过。索引类原字节摘要为
`e9b3e2f335aa50d454c3b52e7981249e5a7d5d54744486d4003443e0b771493f`。

本批仅修固定交付清单中的边界缺陷。长期观察使用原独立只读 2ac 源码，
不被此修复改写或重启。盈利策略随后单独研发；多市场分别验收，机构异机
设施保持延期，不计 PASS。完整交付仍需现有真实输入、独立核验、同 cut
恢复、同版发布门、获准安装及实际 Demo—现金—再研究证据。
