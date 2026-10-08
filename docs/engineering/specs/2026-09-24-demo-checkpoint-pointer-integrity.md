# Demo容量维护前的备份指针完整性

承接[只读引用检查](2026-09-24-demo-retention-reference-review.md)。现有命名空间备份
可保存完整文件和数据库，但执行侧四个容量触限目录的latest指针未作目标存在性
检查；研究侧observation-prefix已有该检查。文件字节保全本身不能证明恢复后
latest可读取，因此本批在既有备份/恢复路径补齐相同的指针约束。

## 范围与实现

`tools/demo_namespace_checkpoint.py`对executor的以下目录识别内容寻址指针：

- observation-views
- resident-recovery/views
- execution-origins/BTCUSDC
- execution-origins/BTCUSDT

存在latest.json时必须是唯一sha256字段、64位小写十六进制摘要，目标必须在
同目录的备份清单中且字节摘要匹配。create和restore共用verify_files，因此旧备份
恢复也执行校验。没有改变指针格式、记录数据、运行容量门、密钥/签发或执行权限。
缺少整个目录仍不凭空创建；没有声称跨命名空间原子一致性或全引用证明。

## 验证

证据目录：`/home/kerstker/quantos-dev-output/demo-checkpoint-pointers-20260924`。
十二项反例覆盖四目录×目标缺失/非法指针/目标摘要错误，修改前全部失败；
合法加密恢复对照通过。修改后checkpoint模块48项通过。新增两项恢复验证也通过（63.95秒）：旧悬空指针备份拒绝恢复；10001份历史
记录加latest指针在超过运行读取上限时仍能加密备份并隔离恢复，逐文件及指针
字节一致，源文件保留。
关联备份周期、日/组合检查点和观测工具44项通过。Ruff通过；治理扫描695文件、
0违规。本批没有执行完整canonical或发布验收，也没有使用受保护账户记录测试。

本修复只是维护准备的一部分，不是归档执行器。实际生产归档、维护窗口、
全引用边界核对与服务恢复仍未完成；没有停服、移动或删除生产文件。
