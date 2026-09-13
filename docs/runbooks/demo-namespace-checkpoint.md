# Demo 命名空间快照操作

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

目前该工具没有安装定时器或轮转日志。实际接线时先验证同范围数据、源记录/进程保全、
完整加密往返、失败投影和容量计划，再接入常驻备份；原 8 小时全规模演练保持独立。
