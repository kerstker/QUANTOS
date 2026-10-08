# Codex 环境优化记录 — 2026-09-05

用户要求按 [GPT-6 Astra 官方指南](https://developers.openai.com/api/docs/guides/latest-model)
检查当前环境并直接优化。该指南建议检查旧指令冲突、明确授权范围、控制测试投入，
并在迁移时保留已有推理强度。本次据此优化项目指令；不调整 DGX GPU/CUDA 或运行服务。

## 实测问题与改动

| 项目 | 修改前 | 修改后 |
|---|---|---|
| 根 AGENTS.md | 96,395 字节，1,051 行 | 10,953 字节，174 行 |
| CLI 实际加载 | 指令中途截断，缺少 Commands 和 runner 入口 | 完整文件加载，runner 入口可见 |
| 历史能力记录 | 与执行规则混在自动加载文件中 | [独立归档](2026-09-05-agent-guide-history.md)，按需查阅 |
| 当前工作入口 | 历史完成度和命令占据主要篇幅 | September 5 路线、增量记录、DGX 独立环境手册 |
| 验证要求 | 全量门禁命令缺少迭代阶段说明 | 相关回归用于增量；里程碑/发布仍执行完整门禁 |

常驻文件缩小约 88.6%。原指南取自
`80ec46b42dccdb88f9513186772ede9ad123e5cf:AGENTS.md`；归档增加历史说明并修正相对链接，
正文历史信息保留。根文件保留 L2、V1 隔离、V2 技术验证限定、人工门和运行时威胁模型，
并明确不得改变 locked OOS、删除实验或绕过审计。

[官方 AGENTS.md 加载说明](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
规定项目指令默认限额为 32 KiB。当前用户配置未设置 `project_doc_max_bytes`。
通过本机 `codex debug prompt-input` 导出的提示输入确认上述截断；修改后再次导出，
确认完整 AGENTS.md 字符串包含于模型输入。这是上下文完整性验证，不是响应速度基准。

## 保持的有效配置

检查时 CLI 为 `0.153.4`，本机配置为 `gpt-6-astra`、
`model_reasoning_effort="high"`、`service_tier="default"`。
这些是读取到的配置值，不代表桌面端每次请求都没有覆盖参数。
没有证据支持为当前精确账本任务降低推理强度、升级模型或增加付费服务等级。
未修改全局 Codex 配置、托管技能缓存、凭据、生产环境或依赖锁文件。

## 验证

- 修改前后提示输入导出：`/tmp/quantos-codex-prompt-before.json` 和
  `/tmp/quantos-codex-prompt-after.json`；仅供本机排查，不提交原始提示内容。
- 根指南和历史归档的相对链接检查通过；仓库卫生检查通过。
- 研究 runner 边界测试：13 passed；日志位于
  `/tmp/quantos-codex-instructions-4n3l_aas/tests.log`。有一条既有 Starlette
  测试客户端依赖弃用警告，不涉及本次文档修改。
- 显式运行原有 pre-commit：仓库卫生、治理扫描和暂存密钥防呆均通过，
  日志位于 `/tmp/quantos-codex-instructions-pre-commit.log`。
- 历史归档逆转相对链接调整后与原提交逐字一致；Safety boundary 段原文保留。
- 本次仅修改文档，无需重跑引擎全套；不宣称完整 canonical gate 通过。

新启动的 Codex 任务将自动读取精简指南。本任务已读取并核验新文件；已经存在于
会话中的旧上下文不会因此被追溯删除。没有测量端到端延迟，不承诺固定加速倍数。
