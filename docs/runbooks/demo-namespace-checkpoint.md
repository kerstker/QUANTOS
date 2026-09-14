# Demo 命名空间快照操作

当前运行与恢复证据见[2026-09-14恢复交付记录](../engineering/specs/2026-09-14-demo-recovery-completion.md)。
研究、执行、签发和工作流四类周期备份已安装并完成实际加密与隔离恢复核验。
下文命令说明操作合同，实际安装版本、范围和自然周期结果以该交付记录为准。

对应[实现、盘点和范围](../engineering/specs/2026-09-13-demo-namespace-checkpoint.md)。
原夜间备份不会自动覆盖这些新目录。使用已核对的 Python 3.12 环境，保留每次命令、
退出状态和 JSON 结果；不从“生成了文件”推断恢复成功。失败中的 `reason_code`
给出固定检查原因；`CHECKPOINT_UNCLASSIFIED` 不包含外部异常原文，需保留该次材料再诊断。

## 捕获与加密分开运行

捕获进程使用原目录所有者，输出到为该身份准备的私有目录：

```bash
python -P -m tools.demo_namespace_checkpoint capture \
  --namespace executor \
  --source-dir /var/lib/quantos-demo-executor \
  --source-commit <本次使用的完整源码提交> \
  --output <私有目录中的全新checkpoint.zip路径>
```

该步骤不读取密钥，返回 `CAPTURED_PRIVATE_NAMESPACE_ARCHIVE` 和明文归档摘要。
`encrypted=false`，不是备份验收成功。研究数据使用 `--namespace research`，
`--source-dir` 指向实际研究 state 的 `data/cache`；issuer/controller 分别使用原专用目录。
来源没有安装时会拒绝，不创建占位状态。原驻留 `resident-recovery/cycle.lock` 等确切
零字节进程锁不会写入归档；非空锁或未知文件仍拒绝，不能删除它们强行通过检查。

研究观察持续追加时，可以为 `--namespace research` 明确追加 `--observation-prefix`。
它固定本次所列文件和索引，下一批新增观察留待下次归档，已经列入的历史仍检查未改写。
V2 结果必须标明 `RESEARCH_FROZEN_FILE_INVENTORY`；不表示所有文件来自同一原子时点。
该选项不能用于执行/签发/控制器，不应拿它跳过订单或领取数据库的一致性检查。

由单独的备份身份使用现有密钥来源进行加密：

```bash
python -P -m tools.demo_namespace_checkpoint seal \
  --archive <刚才的私有checkpoint.zip路径> \
  --expected-sha256 <捕获结果中的sha256> \
  --output <备份目录中的全新checkpoint.enc路径>
```

密钥来源沿用原配置：systemd `LoadCredential=l2-backup-passphrase:...`、
`QUANTOS_BACKUP_PASSPHRASE_FILE` 或原环境变量，必须且只能解析到一个来源。
不要把密钥写进命令参数、源码、日志或研究页面；原数据进程不需要获得密钥。
封装进程不打开源数据库，只读取经摘要核对的私有中间归档。

`VERIFIED_ENCRYPTED_NAMESPACE_CHECKPOINT` 表示本份归档完成加密和一次完整隔离恢复。
保存其加密归档 SHA-256 和结果记录，供恢复时独立指定。失败不删除原捕获、原日志或旧归档。
中间明文材料应留在对应私有目录，按明确的运维保留方案处理；本工具不自动删除历史。

仅在单一身份已经合法持有来源和备份密钥的隔离环境，可用同参数的 `create` 直接完成
捕获与加密；默认要求密钥，没有隐式明文回退。

## 隔离恢复

```bash
python -P -m tools.demo_namespace_checkpoint restore \
  --archive <checkpoint.enc路径> \
  --expected-sha256 <原加密归档sha256> \
  --output <全新的隔离恢复目录>
```

目标必须不存在，且不能是原生产/执行/签发/控制器目录。核对文件数、原账户摘要、日志链头、
预留、未完成恢复、订单和成交计数以及停机历史。原私有流是否正在连接，是另一项实时条件；
恢复结果中的 `stream_current_observation_verified=false` 不应改成当前行情可用。

恢复目录中的所有记录仍按原编号、时间和状态保留。它不恢复进程内批准，不重发不确定订单，
也不初始化停机状态或授予签名权限。多命名空间共同截止点和实际恢复接管需要分别核验。

## 周期运行接线

[周期工作者和单元渲染](../engineering/specs/2026-09-13-demo-periodic-checkpoints.md)
按命名空间运行。原数据 UID 使用独立、已建立的 0700 私有 spool：

```bash
python -P -m tools.demo_checkpoint_cycle capture \
  --namespace research --source-dir <原state/data/cache> \
  --source-commit <确切已验收发行提交> --spool <原身份私有spool> --observation-prefix
```

独立封装身份从原 `LoadCredential` 读取现有口令，对已完成的捕获处理积压：

```bash
python -P -m tools.demo_checkpoint_cycle seal --pending \
  --namespace research --spool <原身份私有spool> \
  --source-uid <来源UID> --backup <封装身份私有备份目录>
python -P -m tools.demo_checkpoint_cycle status \
  --namespace research --spool <原身份私有spool> \
  --source-uid <来源UID> --backup <封装身份私有备份目录> --rpo-seconds 86400
```

积压处理的 `PROCESSED` 仅表示这一轮处理完成；空队列不是备份通过。以各周期的完整
恢复结果与 `status` 恢复点年龄核对。失败和中断记录不得删除，密文后的中断必须重新恢复。
不要在命令参数中传口令。运行 `-P -m` 时应由已审阅单元设置确切 `PYTHONPATH`，不能
依赖当前目录自动加入模块搜索路径。

单元生成入口为 `scripts/render_demo_checkpoint_units.py`，提供发行提交、命名空间、
原目录和 UID 后只输出四份禁用单元及摘要。安装前核对真实身份、挂载、空间、原进程
保全和自然周期；原 8 小时全规模演练保持独立。自动归档/清理仍待进一步接线。

## 只读恢复点监控

对应[前后端实现与验证范围](../engineering/specs/2026-09-13-demo-backup-display.md)。
监控读取固定 `/var/lib/quantos-demo-checkpoint-captures/<namespace>` 与
`/mnt/quantos-backup/demo-namespaces/<namespace>`，不会读取任意一次性演练目录。
原一次性恢复成功不能据此自动填成周期运行通过。

先具备与发行版本绑定的原 `DEMO_DISPLAY_PUBLICATION_V1` 配置，字段见
[展示发布手册](demo-operating-installation.md#4-安装单向展示发布)。安装步骤需建立
root 所有、0700 的 `/var/lib/quantos-demo-checkpoints`，以及研究 UID/GID 所有、
0700 的 `<research-state-root>/data/cache/binance_demo_backup_status`；后者必须和
受保护配置里的研究路径一致。不要代替缺失的签发/工作流创建业务状态。

```bash
python scripts/render_demo_checkpoint_units.py \
  --release-commit <完整已验收发行身份> --monitor \
  --research-state-root <实际/home目录中的研究state路径> \
  --output-dir <全新审阅输出目录>
```

渲染只生成 `quantos-demo-checkpoint-monitor.service/.timer` 和摘要，不安装或启动。
单元调用 `tools.demo_backup_observe --source-commit <完整身份> --publish`，不装载
备份密钥；先保存 root 私有摘要，随后使用原永久降权发布器送到固定研究缓存。
默认启动一分钟后观察，以后每次结束十五分钟后再观察，单次两分钟上限。

安装后分别检查服务结果、页面的四类范围和实际定时器运行状态。监控即使在备份挂载
缺失时也应输出不可用；某一类捕获或加密目录缺失保持未知。超过三十分钟的摘要会在
页面标为历史。它不重新解密归档、不执行生产恢复，也不替代各周期的完整恢复证明。

DGX实际发布还需显式`AmbientCapabilities=CAP_SETUID`；bounding set声明不足以证明
进程实际获得该权限。原NoNewPrivileges和权限上限保留；以实际研究UID核对永久切换后
有效/许可/ambient权限归零、私有来源不可读且无法取回root。实际失败与修正见
[运行记录](../engineering/specs/2026-09-13-demo-backup-runtime.md)。捕获和封装不需要此能力。

## 公开行情日归档的兼容接入

公开研究采集 CLI 的 `--archive-observations` 将新的报告按原观察时间的UTC日写入
SQLite段，旧JSON保留。实施与实际恢复范围见[归档记录](../engineering/specs/2026-09-13-demo-observation-archive.md)。
仅在包含新存储、Console读取及快照恢复实现的同一源码完成验收后，为两币种公共研究
采集单元加入此参数。执行侧受保护采集和采集子进程不接受它。

启用前核对实际Console与捕获/加密恢复工作者都包含此格式支持；旧备份版本不能读取
新指针。首次采集后检查原时间、报告状态、指针目标及只读市场页面，并运行正常周期的
捕获和完整恢复，核对两币种的段数、记录数和清单摘要。未发生的自然周期保持未验收。
若需要回退程序版本，先确保回退版本仍支持已写入的格式；不能删除新段、覆盖历史
或重写观察时间来伪装兼容。这里的日段只降低该生产者的小文件增长，原快照总量和
文件上限、其他观察目录及备份留存仍需独立监测。
