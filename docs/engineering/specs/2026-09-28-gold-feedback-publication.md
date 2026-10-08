# 黄金执行反馈到研究目录的受控交付

## 实现

`tools/demo_feedback_publish.py` 接受固定版本、周期摘要、执行侧完整文件摘要和反馈内容摘要。源目录只能是 `/var/lib/quantos-demo-executor/resident-recovery/functional-cycles/<cycle>/research-feedback/<artifact>.json`，不接受任意文件路径。复用原 protected publication 的逐组件 O_NOFOLLOW、所有权、非组/其他可写、单硬链接、读取前后元数据一致性和大小限制。原文件摘要与反馈 result_hash 分别核对，不能混用。

只有完整整轮黄金反馈、正确源码版本、原身份及已成熟的原研究消费者校验通过后，才调用已有永久降权函数，清空附加组并移除保存的 root UID/GID。研究目录首次访问发生在降权之后。目的地固定为配置研究根下 `data/cache/ai_factory/execution-feedback/<result_hash>.json`；逐组件不跟随符号链接，临时完整写入/fsync 后以不覆盖硬链接插入，目录 fsync。重复相同内容幂等，冲突拒绝，不覆盖原证据。

交付使用单独的 root-owned `/etc/quantos/demo-observation/research-feedback.json`，schema 为 `DEMO_RESEARCH_FEEDBACK_PUBLICATION_V1`，确切字段为 schema、source_commit、executor_uid、research_uid、research_gid、research_state_root。已有 display profile 不能作为这项交付的登记。本次未生成或安装实际配置。

输出 `RESEARCH_FEEDBACK_DELIVERED` 只表示固定技术材料到达；不代表来源认证、现金新鲜度复查、经济准入、自动创建 campaign 或执行授权。控制器应先调用原 exporter 重建当时可接受的反馈，再交付明确摘要。后到状态变化不会改写已交付历史文件。

## 验证

新增入口测试先因缺少模块失败；实现后验证源摘要/路径/符号链接、独立配置、降权顺序、目的地不可覆盖及目录链接拒绝。原黄金两批及三批完整隔离场景改用实际 read_export/validate_export/publish，而非测试直接复制 JSON。重复交付后原 campaign/JobQueue 消费同一文件，完成下一轮候选；错误源码、额外顶层字段、错误摘要和未来材料拒绝。

五组联合 54 passed，保留一条 Starlette/httpx 弃用警告；Ruff E9/F821 通过，治理扫描700文件0违规。身份系统调用顺序有原降权测试覆盖，新增 run 顺序测试使用替身；未启动实际 root→研究 UID 交付进程、未接触真实密钥或生产目录。

## 剩余闭环缺口

生产控制器仍未调用 feedback 导出或本交付入口；原 setup 身份仍需与正式研究身份绑定，登记/安装材料和有界后续 campaign 策略仍需衔接。未部署、未完整 canonical、未真实 Demo 持续验收，F4/F5及工厂整体不标完成。
