# Demo 首次安装与展示发布

本手册对应[实现记录](../engineering/specs/2026-09-13-demo-operating-installation.md)。
开发和 Demo 测试已获授权，原 API 密钥不需要重复提供。安装入口不会替操作者生成
HUMAN、验证结论、签发键或停机历史；实际受独立控制的材料必须存在，缺失保持未安装。
本手册不是主网交易、候选晋升或盈利声明。

## 1. 准备已有材料

包目录放在 root 所有、不可由组或其他用户写入的路径下；文件必须 root:root 0600、
单链接常规文件。不要放到源码、研究工作区或聊天附件中。包内包含以下确切文件：

| 文件 | 内容及绑定 |
|---|---|
| `manifest.json` | `DEMO_OPERATING_BUNDLE_V1`：`schema`、`source_commit`、`expected_registry_sha256`、`files`、`tasks` |
| `registry.json` | 在原固定注册表上追加一把提供的签发公钥；部署、UID 分离及旧键不变 |
| `policy.json` | `DEMO_ISSUER_POLICY_V1`，绑定同一账户、部署、源码、策略、授权、验证、风险及有限任务预算 |
| `profile.json` | `DEMO_REQUEST_PROFILE_V1`，已有初始声明、授权、验证材料和当前采集源码摘要 |
| `signing-key.json` | 原 `DEMO_ISSUER_KEY_V1`，仅供专用签发进程使用；不是交易所 API 密钥 |
| `kill-switch.sqlite3` | 已有原格式停机历史的完整、自包含数据库镜像，不导入 WAL/SHM，也不生成默认停机状态 |
| `<task_sha256>.json` | 1–20 个已有 `DEMO_REQUEST_TASK_V1`；内容的规范 JSON 摘要必须与文件名相同 |

`files` 是除清单自身以外上述每个文件的 SHA-256 字节摘要映射，不能有额外条目。
`tasks` 是唯一任务摘要列表。JSON 单文件上限 64 KiB，停机库上限 64 MiB。
`expected_registry_sha256` 是原注册表按现有 `registry_sha256` 算法的规范摘要；
命令参数的清单摘要则对应实际 `manifest.json` 文件字节，两者口径不同。

`source_commit` 必须与实际 `/opt/quantos/releases/<commit>` 的受保护安装身份一致。
该位置和证据由原发布验证器核对，不能用另一个已通过的源码版本代替本工具的完整验收。
所有任务在安装时须处于各自及政策有效时窗内，订单 ID 不重复，价格和数量均明确提供。
这一步不自动产生价位、策略优劣或批准人的声明。

## 2. 核对再首次导入

在相应已验收发行版及其 Python 环境中，以 root 运行：

```bash
python -P -m tools.demo_operating_bundle \
  --source-commit <已安装的完整提交身份> \
  --bundle-dir /etc/quantos/demo-operator-input \
  --manifest-sha256 <清单原始文件的SHA256>
```

无 `--apply` 时仅返回 `REVIEWED_ONLY`，不创建安装状态。完成核对后对同一命令追加
`--apply`。工具重新读取和核验，不信任之前的终端输出。`INSTALLED` 只表示材料已落盘，
`services_started=false`、`orders_submitted=false`；结果还写入 root 私有控制器的
`installation.json`。输出不含私钥、账户原文或 API 凭证。

已存在任一目标目录/停机库/撤单库时会拒绝首次安装。`INSPECT_PARTIAL_INSTALL` 表示
安装阶段可能发生写入，需检查现场；不要通过删除数据库或重复清空目录使检查通过。
激活注册表之前的失败保留旧注册表，激活后的结果写入失败也不会撤销历史或假定未激活。

## 3. 连接原执行工作流

先在实际 UID 下检查签发进程可遍历配置、仅自身可写追加库，执行进程不能读取私钥，
原私有流/订单/恢复库身份与内容前缀不变。任务使用原 `tools.demo_task` 入口，确切命令
见[任务流程](../engineering/specs/2026-09-13-demo-observed-request-workflow.md)。
每个任务只认原身份；中断后查看原 task-claim、request、执行预留和回执，不新造编号重发。
有限任务运行后逐单核对场所接收、撤单、成交、查询恢复及原 V2 现金链，再形成 D4 结果。

## 4. 安装单向展示发布

受保护配置固定为 `/etc/quantos/demo-observation/publication.json`，字段如下：

| 字段 | 含义 |
|---|---|
| `schema` | `DEMO_DISPLAY_PUBLICATION_V1` |
| `source_commit` | 相应已安装发行版完整身份 |
| `executor_uid` / `issuer_uid` | 实际独立执行与签发 UID |
| `research_uid` / `research_gid` | 研究缓存写入身份，不能是 root 或执行/签发 UID |
| `research_state_root` | 当前研究运行的绝对 state 路径 |

在独立 root oneshot 单元中运行 `python -P -m tools.demo_protected_publish
--source-commit <完整身份> --kind <种类>`；`market` 必须额外指定
`--symbol BTCUSDC` 或 `--symbol BTCUSDT`。可选种类：`market`、`executor-observation`、
`issuer`、`oms`、`cancellation`、`resident`。来源和各 Console 缓存目录固定映射，
不接受自选私有输入路径。分别调度，不以缺失 OMS 阻塞行情发布。

单元沿用 PrivateNetwork、PrivateDevices、ProtectSystem=strict、ProtectHome=read-only、
NoNewPrivileges、UMask=0077；只为对应显示缓存开放写目录，凭证目录不可见。
进程初始需要 CAP_DAC_OVERRIDE、CAP_SETUID、CAP_SETGID 读取私有显示来源并降权；
真实写入前须确认 real/effective/saved UID/GID 全部切换，附加组为空。
源服务、目录、发布配置、发行版与单元应在安装前核对，不能把本手册当成已安装证明。

最后检查桌面/手机的两种计价行情及独立状态面板、失败/过期显示和无越权网络写入。
显示成功不代表实际提交、现金闭合或盈利。新私有命名空间另外纳入加密备份、追加历史
归档和恢复验证，沿用原日志，不能以滚动删除记录实现“持续运行”。
