# 外部模型提供方治理接入规格

状态：APPROVED / L2 research-only

## 问题

机器矩阵中的 `AI.MODEL.REGISTRY.REDTEAM` 仍停在 M2。现有
`GovernedModelRuntime` 已经约束模型、提示词、工具和调用哈希链，但只执行调用方提供的
离线函数；没有任何真实外部模型提供方经过同一承重边界。用户已明确提供一个
OpenAI-compatible 服务：`https://vip.sushuo.biz/v1/chat/completions`，模型
`gpt-5.6-sol`，凭据位于仓库外的 KEY 目录。

## 决策

1. 新增版本化 provider 注册表。默认 provider 保持
   `offline_registered`；外部 provider 必须由 CLI 参数或
   `QUANTOS_MODEL_PROVIDER` 显式选择。
2. `services/` 与 `quantos/` 保持零网络 import。HTTPS 客户端只存在于
   `tools/external_model_provider.py`，并使用系统 CA、主机名校验、固定 HTTPS endpoint、
   超时和响应大小上限。
3. API key 只允许来自注册的环境变量 `NEW_API_KEY` 或调用方显式传入的文件路径。
   key、key 路径、原始响应和模型正文不得进入运行时事件或版本化证据。
4. 外部模型使用独立的 `ModelCard`、`PromptTemplateCard` 和 `ToolManifest`，仍由
   `GovernedModelRuntime` 校验并记录。唯一允许动作是 `EVALUATE`；审批、kill-switch、
   凭据、订单和 live trading 全部显式禁止。
5. 首个真实调用是无业务数据的 echo probe。证据只保存 challenge、请求/响应正文哈希、
   返回模型、finish reason、token 用量、TLS leaf certificate SHA-256、运行时事件和源代码/
   测试哈希。
6. HTTPS 只认证传输对端，OpenAI-compatible 响应本身没有 provider 数字签名。因此证据明确
   标注 `TLS_SERVER_AUTH_ONLY_UNSIGNED_BODY`；它证明真实受治理接入，不冒充不可否认的外部
   签名评审。
7. `AI.MODEL.REGISTRY.REDTEAM` 可在真实 provider probe、provider 红队、运行时哈希链和独立
   verifier 全部通过后达到 M3。`VALIDATION.CHALLENGER.MODEL_RISK` 仍保持 M2/PARTIAL，直到
   独立评审绑定强外部人类身份。

## 失败关闭条件

- provider 未注册、被禁用、选择值未知或 endpoint/model 与注册表不一致；
- endpoint 不是 HTTPS，带 userinfo/query/fragment，或路径不是固定 chat-completions；
- key 缺失、为空或被放进请求正文；
- HTTP 非 200、响应超限、JSON/choices/message/content 结构错误；
- 返回 model 不匹配、echo token 不精确、TLS leaf certificate 无法取得；
- 运行时 lineage、输入/输出/event hash、provider 注册哈希、源文件哈希或证据自哈希不一致；
- 证据出现 API key、原始 response、模型正文、订单/凭据字段或任何执行/晋级授权。

## 验收证据

- provider 选择、网络客户端、运行时桥和独立 verifier 的正负测试；
- 治理测试证明 `services/`/`quantos/` 仍无网络 import，工具不接触 trading/broker/live；
- 一个由用户 KEY 驱动的真实 HTTPS probe，版本化为脱敏证据；
- top-tier checker 重新计算后：AI provider 项 M3/PASS，challenger review 仍 M2/PARTIAL；
- `python scripts/validate.py` 完整 fail-closed gate 通过。
