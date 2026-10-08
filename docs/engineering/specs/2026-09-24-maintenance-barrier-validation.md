# Demo维护互斥与私有备份联合验收

固定源码：8f9b00cda9c1c41614fd26e5c6920975b8784d39，基于3836e83b的独立工作树，
只叠加18个明确文件。原开发工作树和索引未提交或重置。提交前手动运行真实
pre-commit钩子通过；没有使用no-verify。固定源码验证后工作树干净。

源码及证据目录：
/home/kerstker/quantos-dev-output/maintenance-barrier-integration-20260924。

使用已验证Python3.12、Node24、空白起始环境及关闭交易的环境开关运行：

```text
scripts/validate.py --stage live --stage python-quality:ruff --stage python-quality:mypy
```

结果：执行栈live/trading/tools及MFT集成5087通过、2跳过，耗时578.99秒；
Ruff和十四文件mypy通过，三阶段退出0。既有Starlette弃用警告保留。
integration.json绑定上述commit且selection_scope=SUBSET；integration-scope.json
记录报告、日志、提交钩子及源码清单的SHA256。

覆盖近期服务暂停/恢复与cgroup检查、私有备份自动隔离恢复、控制器共享/独占
目录锁，以及原执行工具回归。它不证明完整32阶段canonical、ARM64发布、
经济结果或实际停写。DGX现行服务未切换，没有生产归档或真实维护锁。

下一步仍需组合操作员维护入口：核对受控源码和当前部署、排空旧写入进程、
重新核查引用、备份、限定归档、失败回滚和原服务恢复。旧部署没有新控制器锁
协议，不能把该版本的测试结论用于宣称现场全局静止。
