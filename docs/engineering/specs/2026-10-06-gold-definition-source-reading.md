# 黄金研究定义的来源读取开销与连续性

范围：既定 [F1–F5 收尾计划](../plans/2026-09-28-ai-factory-closed-loop.md)
中的完整验收阻断，不新增交付条件。父源码为
`4cf5c1561bee9af5b06974b82724c5e6e4e9eacf`；独立副本只修改
`tools/gold_bar_definition.py`、其原测试文件及本记录。

## 实际问题与修改

原 dbc22390 完整门以 30 PASS / 2 TIMEOUT 结束；执行侧最后观察到的案例为
`test_native_pilot_original_store_and_prefix_reach_each_reader_without_writes[targeted]`。
这不说明该案例单独导致整组超时，也不把未执行案例计通过。

该案例在父源码下带 profiler 实际通过，driver 18.833721552 秒；24 次
`verify_source` 累计 6.374172862 秒。原函数为每个 Python 文件逐次解析整条
父路径。新回归使用 93 个实际临时 Python 文件，先得到 1,030 次 lstat，
超出预先写明的 600 次操作上限；另一个回归在读取期间以相同字节替换 inode，
原函数未拒绝。原联合 2 FAIL / 5 PASS 及加强触发确认后的单项 FAIL 保留。

`verify_source` 现复用已存在的 `_python_originals`：持有目录句柄，无符号链接
逐层打开；每次读取全部当前 Python 文件，核对读取前后文件/目录/路径身份，
按原路径排序和 `relative_name + NUL + raw_bytes + NUL` 计算同一摘要。
不减少调用次数，不缓存源码、结果或核验结论。

裸定义核验沿用 PreparedBarDefinition 已有的完整三目录要求与读取预算：
10,000 节点、256 层、原 MAX_BYTES 单文件上限和 256 MiB 总源码上限。
缺目录、非普通文件、符号链接、超限或读取期间更换均拒绝；已知读取错误映射到
`GOLD_BAR_DEFINITION_SOURCE_*`，原 SOURCE_IDENTITY 与 SEMANTICS_CHANGED 保持。
Git HEAD、锁文件、Python 版本、架构及三个语义文件的原比较继续执行。

## 验证与交付边界

新回归涵盖当前成员/同长字节变化、所有来源身份字段、匹配来源摘要但语义改变、
读取期间同字节换 inode、符号链接、FIFO、文件预算及句柄释放。
初步来源/Prepared 原两组 30 PASS；原读者及运行证据组、无 profiler 同例对照、
全字节读取算法对照、质量门和主区整合结果见外部原件：
`/home/kerstker/quantos-dev-output/gold-project-closure-20261003-3pr5mrly/gold-execution-profile-20261006`。
最终冻结与主区结果由 PROJECT_STATE 记录，不把初步通过替代最终结果。

原读者案例保留实际留存原件和原消费者，但账户、交易路径与部分保护边界为明确
夹具；不是现场 Demo、writer 停写、完整现金对账或经济准入证明。
在途父版完整门与公开月观察源码不修改，不将其证据重绑给本增量。
新候选仍需自身完整发布验收；没有部署、服务切换、保护状态写入、实际信任安装、
凭据访问或下单。
