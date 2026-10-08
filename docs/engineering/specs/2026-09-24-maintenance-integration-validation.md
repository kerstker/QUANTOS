# Demo维护开发增量：固定源码执行栈联合验证

将[共享计算类型覆盖](2026-09-24-analytics-type-coverage.md)、
[备份指针完整性](2026-09-24-demo-checkpoint-pointer-integrity.md)、
[隔离留存事务及引用接线](2026-09-24-demo-retention-transaction.md)和
[恢复展示锁边界](2026-09-24-demo-recovery-publication-lock.md)收拢到独立工作树。

## 源码与原工作区隔离

基线：`a99b5d3351558eef00da5f62b5dd4bf3d641e31a`。
本批提交：`3836e83b2137867363d067c8666a4fa550006c62`。
分支：`codex/maintenance-integration-20260924`。
工作树：`/home/kerstker/quantos-dev-output/maintenance-integration-20260924/source`。

只覆盖明确列出的24份源码/测试/文档；selected-source.json记录每份基线和选入字节
摘要。没有在原工作区提交、覆盖其他编辑或更改其索引。仓库提交钩子因文件不具
执行位会被git跳过，因此提交前显式用隔离Python的PATH运行实际pre-commit脚本，
卫生、治理和暂存密钥防呆检查均通过，precommit.log留存，没有使用no-verify。

## 本次实际验证范围

在固定源码上使用隔离Python3.12、单线程数值库、关闭交易标志的明确环境运行
scripts/validate.py的live、python-quality:mypy和python-quality:ruff三个阶段。

- Ruff通过。
- mypy十四个文件通过。
- 全部live/trading/tools及MFT执行集成：5052通过、2跳过，565.10秒。
- 保留一条既有Starlette/TestClient弃用警告，没有为消除警告升级依赖。
- 三阶段全部PASS，退出0；JSON明确selection_scope=SUBSET，commit绑定3836。
- 运行结束工作树干净；session3609已完成，不存在需要继续轮询的进程。

证据根为`/home/kerstker/quantos-dev-output/maintenance-integration-20260924`，包含
integration.log、integration.json、integration-scope.json、selected-source.json及
precommit.log。索引绑定报告/日志摘要，不冒充独立签发。

## 未完成项

这不是32阶段canonical、ARM64新构建或发布验收；旧a99完整验收不能继承给3836。
运行服务未切换，受保护记录未移动，原源码中新锁语义尚未实际部署。
真实备份、维护期间写入静止与引用复核、生产入口/原服务状态恢复、长期留存仍
需完成；当前保护目录拒绝不变，交易权限不变。路线图未完成。
