# 有限 Demo 执行会话

本入口解决同一个执行进程等待独立签名的问题，不能安装信任根或签发批准。
代码与验证范围见[实现记录](../engineering/specs/2026-09-12-demo-execution-session.md)。
现有 Demo 测试授权继续有效，不重复索要交易所 API 密钥。

## 运行条件

独立部署固定 `/etc/quantos/demo-submission/authority.json` 后，只有其指定执行 UID
可运行；研究 UID 与签发 UID 均不能冒用执行身份。固定状态目录为
`/var/lib/quantos-demo-executor`，所需数据库与原[驻留恢复手册](demo-resident-recovery.md)
一致，另需已存在且可验证的 `kill-switch.sqlite3`。只读会话预检不创建这些数据库，
不初始化或复位停机状态。受保护凭证只由原 Demo 适配器在最后检查后读取。

必须使用已验收且与独立部署身份对应的源码/解释器。下列只是协议入口示意，
`<release>` 与 `<source>` 必须替换为实际验收身份；本次没有安装或启动此服务。

```bash
/opt/quantos/venvs/<release>/bin/python -P -m tools.demo_execution_session \
  --source-commit <source>
```

源码参数是来源说明，不能替代签名或独立部署绑定。没有 `--submit` 绕过开关、
主网/凭证路径参数、风险默认值、网络监听或守护进程自动重发选项。

## 两帧协议

1. 向标准输入写一行完整 `DEMO_EXECUTION_SESSION_INPUT_V1` JSON，最大 16,000,000
   字节：`schema / orders / origin / risk / mandate / attestations`。
   `risk` 必须包含 `account / market / native_limits / strategy / kill_switch /
   plan_turnover`；上限为原 RiskLimits 的全部字段，声明时间由执行进程取当前值。
   不把任意历史回顾材料包装为当时可用输入。
2. 读取一行 `DEMO_EXECUTION_SESSION_PROPOSAL_V1`。完整 `proposal`、摘要和本地
   不可变文件均可供独立签发方检查；此时尚无下单权限或订单预留。
3. 独立签发方验证原部署/账户/市场/风险/范围后，在本进程仍存活且材料仍新鲜时
   返回原 `DEMO_SIGNED_SUBMISSION_V1` 信封（内含 `DEMO_SUBMISSION_GRANT_V1`）。外层一行字段严格为
   `schema="DEMO_EXECUTION_SESSION_GRANT_V1" / proposal_sha256 / envelope`。
   信任公钥只能来自固定注册表，不能将测试签名材料安装为真实来源。
4. 读取结果并交回原编号恢复。签名收到后的预留、意图、发送标记、响应都走原
   执行代码；`QUERY_RECOVERY_REQUIRED` 不是成交确认或现金闭合。

每次读帧最多等待 30 秒；实际提案/行情、签名与风险可能更早过期。超时不要延长
旧批准或改时间戳：新会话重新观察、重新生成提案，已预留订单必须先沿原 ID 查清。
运行中只接受一个提案/一个交接，不接受捆绑两帧或旧会话恢复命令。

会话结果写到固定状态目录的 `session-results`，原提案写到 `evidence`；全部只追加，
EOF/错误/不明阶段保存安全的摘要而不回显原异常或敏感输入。若磁盘无法记录，输出
明确 `stop_record_persisted=false`，既有发送/恢复数据库仍是原订单事实来源。
退出 0 表示此次交接返回了执行尝试结果；退出 3 表示输入或执行未解决。任何退出
状态都不能作为自动补单、来源认证、现金匹配或盈利证明。

当前支持原空仓起点、原完成成交的同日单合约减仓，以及已核对平仓后的下一固定 LIMIT 批次。
后续提案自动绑定原精确成本、累计现金、日盈亏和换手，不能用新空仓快照清除旧持仓；
来源未齐、未解释现金变化和跨日仍停止。见[连续持仓实现及范围](../engineering/specs/2026-09-13-demo-position-continuation.md)。
独立签发与受保护执行侧安装仍在继续。
回执、取消与现金不能因命令可运行就提前标为实际验收完成。

## 有限撤单会话

同一入口可使用 `--purpose cancellation`。必须已有原预留/发送/新鲜原编号查询，
以及原受保护 `cancellations.sqlite3`；不从用户输入创建或复制原订单。

首帧严格为 `schema="DEMO_CANCELLATION_SESSION_INPUT_V1"`、
`reservation_sha256`、`client_order_ids` 三项。原编号列表须明确提供、1–20 个；
只允许同一原批次的 ID，不接受路径或场所参数。执行器返回
`DEMO_CANCELLATION_SESSION_PROPOSAL_V1`，等待独立签发方检查确切范围。

第二帧严格为 `schema="DEMO_CANCELLATION_SESSION_GRANT_V1"`、
`proposal_sha256`、`envelope`。信封必须是原撤单用途签名，不能使用提交用途批准。
返回 `DEMO_CANCELLATION_SESSION_RESULT_V1` 后继续按原编号查询；超时/失败则保存
`DEMO_CANCELLATION_SESSION_STOP_V1`，不能重发或自动更换批准编号。

原查询的 5 秒新鲜度和批准期限持续检查，不因帧允许等待 30 秒而延长。
已经建立撤单发送标记的原订单拒绝新的批准或第二次发送标记；重启后同样生效。
详见[实现与回归证据](../engineering/specs/2026-09-13-demo-cancellation-session.md)。
