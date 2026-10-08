# 研究角色内核交接与原件持有协议

这是[项目统计与原生恢复](2026-10-03-project-statistics-and-research-recovery.md)
之后的组件增量。最终提交和主区整合状态分别记于
[PROJECT_STATE](../../PROJECT_STATE.md)，不是完整系统、发布或现场完成证明。

## 行为

`demo_gold_research_handoff`只接受实际捕获的固定清单和公共上下文句柄。
Root逐一选择原research UID、根和layout，绑定原cut、清单摘要及源码附件声明；
没有首UID替代全部研究角色的路径。声明只允许尝试只读捕获，实际bundle和
原生研究数据仍由原验证器核对。缺少实际源码证明不会变成经济权限。

请求使用root:root 0600、无文件链接、完全密封的memfd。
UNIX SEQPACKET携带SCM_RIGHTS，逐消息SCM_CREDENTIALS绑定实际发送PID/UID/GID，
避免将fork前socketpair的静态peer身份当作降权后进程身份。
Root先核对受控worker的实际PID/start_ticks及UID；接收端核对选定UID/GID三元组、
附加组、root协调进程、boot和有限monotonic期限。pidfd发现协调进程退出即拒绝。
描述符同字节替换也拒绝；截断、额外控制消息/描述符会关闭已接收描述符。

`demo_gold_research_capture_worker`沿原ResearchCaptureInventory和多卷transport捕获。
私有spool按operation/UID固定派生，原研究目录不写入。PREPARED后仍持有完整
原件读取集合；Root的RECHECK和CLOSE均先重验原件、源码、身份及租约。
数据库/WAL、试验人口、账本、费用和收益公式沿用原实现。
最终确认前变化不能借用已生成的归档获得成功；失败的归档和原件保留。
此CLI仅接收继承的控制socket编号，不接受任意根、shell或凭据路径。

writer_barrier_verified、whole_set_verified、submission_enabled、economic_authority
保持false。最后一次原件检查没有建立全局停写屏障；整组协调器须另行完成该条件。

## 实际验证范围

普通UID专项验证原清单选择、密封与角色拒绝、FD替换及畸形消息的描述符回收。
同字节FD替换先真实失败，再加入原FD身份与字节复核；缺失接口的红灯日志另保留。
原生产者专项验证实际六次尝试和bundle，PREPARED后改字节、同字节换inode、
新增成员均阻断后续确认；独立恢复仍沿原人口和身份。

离线只读容器使用已缓存ARM64镜像及原Python3.12.3环境，网络关闭，只挂载
公开runtime、只读隔离源码和任务输出。真实Root/UID五项通过，覆盖两个原研究UID、
错误UID、附加组、原根替换和实际root协调进程退出。固定公共release/签发边界
沿现有合成夹具，研究根所有权、PID、组、FD和pidfd均为真实内核事实。

另一真实进程组两项通过：原UID生产者生成原试验和实际Git bundle，再经密封
Root请求、研究worker、原多卷归档、Root异路径原生恢复、最终控制协议。
改动原输入后拒绝最终确认并保留归档。该组Root清单/源码声明是测试边界，
没有冒充已安装fixed-public权威；Root跳过的导出用例仅在两个角色子进程执行。
两组不能相加推出所有角色同cut或现场停写已经通过。

逐次日志、XML、失败原件及源码前后摘要保存在独立输出
gold-project-closure-20261003-3pr5mrly/root-research-handoff。
冻结组合四组75通过/111.56秒，2999个Python文件前后字节相同，原driver和pytest
已退出并回收exit0；输入及精确结果在该目录combined-result.json。尚须完成实际提交钩子、
精确提交开发基线和主区原件核对后才能记已整合。

## 下一步及完整目标

Root受控生产launcher、全部UID/签发/执行/控制同cut协调、实际writer停写、
完整原生归档与整组恢复/维护入口尚未闭合。研究统计/OOS/基准/风险的受签消费、
最终候选准入、最终同版ARM64/canonical完整门、经授权的现场安装/升级/恢复、
五秒执行链及实际24小时成交—现金—再研究仍需完成。
最新已终态完整门30 PASS/1 FAIL/1 TIMEOUT保持，不用本批专项覆盖它。
本批未部署、切服务、写保护运行状态、安装trust、触碰真实凭据或下单。
机构异机设施继续延期，策略盈利研发单列；完整系统目标继续进行。
