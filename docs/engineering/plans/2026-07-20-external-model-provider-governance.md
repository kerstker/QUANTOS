# 外部模型提供方治理接入实施计划

1. 先写 provider 注册、选择、凭据和响应解析失败测试。
2. 增加纯合同与无网络的 provider selection/runtime bridge；增加只位于 `tools/` 的 stdlib
   HTTPS 客户端。
3. 写证据 verifier 的篡改、降级、秘密泄露和运行时 lineage 负测试，再实现 verifier。
4. 写 top-tier 评分测试：AI 项达到 M3，但 challenger review 不因 provider 接入而假升 M3。
5. 用仓库外显式 key 文件执行一次真实、无业务数据的 echo probe，生成脱敏证据；绝不输出或
   提交 key、个人绝对路径或响应正文。
6. 更新项目状态、能力矩阵、供应链/服务图和 top-tier 评估；运行聚焦测试与完整
   `python scripts/validate.py`。
7. 审查 diff 与秘密扫描，提交、推送、等待 GitHub CI 全绿后合并。

## 本轮执行结果

- 合同、选择器、运行时桥、HTTPS adapter、证据生成器/独立 verifier 与评分升级路径已实现。
- Python stdlib HTTPS 与用户原始 Node `fetch` 两种方式均收到 HTTP 401；没有写入成功证据，
  机器成熟度保持 M2。有效凭据到位后重跑第 5 步即可，不需要改代码或降低 verifier。
