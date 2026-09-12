# 原规模 SQLite 完整校验分析

2026-09-13。承接[合并路线的运行验收](../plans/2026-09-12-demo-execution-completion.md)。
本次是只读性能测量，结果为 `UNKNOWN_TIMEOUT`，没有形成新的完整恢复 PASS。

原验收范围保持 34 文件、139.9GB，完整恢复 932.389 秒仍未达到 300 秒目标。
使用原冻结最大 SQLite 文件 109,924,343,808 字节，继续运行完整 `PRAGMA integrity_check`，
没有换成 quick_check、缩小数据或提高验收目标。

本次由有界临时进程运行：Python 3.12.3，SQLite 3.45.1，4GiB 页缓存、mmap=0，
MemoryHigh 6GiB / MemoryMax 8GiB / SwapMax 0，310 秒进度预算、340 秒进程上限。
文件系统只读，唯一可写位置为独立结果目录；原驻留服务与冻结研究进程未切换。

| 实际观测 | 值 |
|---|---|
| 完整校验结果 | 315.428 秒后超时，未知 |
| 进程物理读取量 | 13,659,271,168 字节 |
| 最大 RSS | 4,367,624 KiB |
| 系统 CPU / 用户 CPU | 约 94.04 / 9.78 秒 |
| 写入字节 | 0 |
| 原冻结文件元数据 | 前后相同 |
| 临时单元 | 正常结束；脚本结束不代表校验通过 |
| 两个冻结研究进程 | PID、命令、目录、单元摘要及重启数均未变 |

材料目录：`/home/kerstker/quantos-dev-output/demo-restore-profile-20260912-b4ab79a29`。
原 `profile-result.json`、`unit-completion.json` 和 `frozen-workers-after.json` 保留。
此前 256MiB 缓存尝试的超时、原完整恢复失败及所有校验未完成记录均保留。

这个结果不足以判定机器无法达到目标，也不能证明扩大缓存有效。下一次改变必须有可解释的
瓶颈依据，并继续使用同规模和原完整检查。异机恢复按用户决定搁置；自然研究保持冻结运行。

## 后续只读元数据核对

最大库有 26,836,998 个 4KiB 页，空闲页数为零。结构为 `public_market_events`
原始事件表、`event_id` 唯一索引、场所/合约/频道/事件时间索引及频道/处理时间/序号索引，
另有 SQLite 自增序号表。此次只读取结构及页数，未读取市场事件行，0.0052 秒完成，
文件元数据未变；不产生新的完整性或恢复结论。

材料为 `/home/kerstker/quantos-dev-output/demo-restore-metadata-20260913/metadata.json`。
没有空闲页，因此当前证据不支持用清理空闲页或 VACUUM 作为主要容量优化方案。
