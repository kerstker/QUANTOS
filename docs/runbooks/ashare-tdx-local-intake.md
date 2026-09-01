# A股通达信本地数据接入

本流程只读取通达信客户端已经下载到本地的文件。原始目录不可修改；标准化库在同目录
临时文件中完成校验后原子替换，重复处理同一捕获日和同一清单不会重复写入。

## 当前可接入的数据

- `vipdoc/{sh,sz,bj}/lday/*.day`：未复权日线 OHLC、成交量、成交额。
- `shs.tnf`、`szs.tnf`、`bjs.tnf`：本次捕获的权威当前股票池、证券名称及由名称前缀
  识别的 ST 状态。
- `base.dbf`：本次捕获时的证券/财务快照和上市日期。
- `tdxhy.cfg` + `tdxzs*.cfg`：本次捕获时的通达信行业分类。
- `specgpext.txt`：本次捕获时的主营构成原始字段。
- `gbbq`：只验证容器结构、记录数和哈希。当前文件是加密的 29 字节记录，未获得可靠的
  事件时间和可知时间前，不会将其解码结果冒充 PIT 公司行为。

名称、ST、行业、主营和 `base.dbf` 均只从捕获日起作为快照使用，不能向过去回填。
多次捕获后，系统能从首次接入日开始形成真实的快照历史，但不能凭空补出此前历史。
`.day` 文件只代表本地历史日线覆盖，不代表证券当前仍在股票池；通达信可能保留已移除
标的的旧 `.day` 文件。每次捕获的 `universe_snapshots` 只由当次 TNF 主数据建立，并分别
报告 `daily_instruments`、`daily_only_instruments` 和 `current_universe_without_daily`。
日线覆盖的市场如果缺少对应 TNF，导入会直接失败，不会把残留日线伪装成当前股票池。
若某个支持市场既无 TNF 也无日线，报告会把它列入
`current_universe_missing_authority_markets`，并将 `current_universe_scope` 标成
`PARTIAL_MARKETS_EXPLICIT`；只有沪、深、北三份主数据均实际产出股票记录时才标为
`ALL_SUPPORTED_STOCK_MARKETS`。下游不得把部分市场证据解释成全 A 股覆盖。

## Windows 端准备不可变快照

先在通达信客户端完成日线和专业财务数据下载，再复制下列公开行情目录；不要复制账户、
凭证、token、日志、个人板块或插件：

```text
<snapshot>/vipdoc/sh/lday
<snapshot>/vipdoc/sz/lday
<snapshot>/vipdoc/bj/lday
<snapshot>/T0002/hq_cache
```

也可以把 `T0002/hq_cache` 复制为 `<snapshot>/metadata/hq_cache`。快照目录名应包含捕获日，
例如 `tdx-windows-20260902`。在 PowerShell 中生成完整 SHA-256 清单：

```powershell
$root = (Resolve-Path "D:\tdx-windows-20260902").Path
Get-ChildItem $root -Recurse -File |
  Where-Object Name -ne "SHA256SUMS" |
  Sort-Object FullName |
  ForEach-Object {
    $relative = $_.FullName.Substring($root.Length + 1).Replace("\", "/")
    $digest = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
    "$digest  $relative"
  } | Set-Content "$root\SHA256SUMS" -Encoding ascii
```

然后用 `rsync --partial --append-verify` 或 `scp` 传入 DGX 的隔离接收区。导入器要求清单既
不能漏文件，也不能多列、重复或越出源目录。导入开始时和临时数据库提交前会各做一次完整
清单校验；两次校验的清单哈希、文件数和总字节数必须完全一致，否则视为接收目录在导入期间
发生变化，临时数据库会被丢弃且不会发布。上传任务必须先完成，再启动导入。

## DGX 发现与增量导入

发现本机接收区、挂载盘上的通达信目录：

```bash
python tools/ashare_tdx_intake.py --discover-only \
  --discover-root /home/kerstker/quantos-data-intake \
  --discover-root /mnt
```

把新捕获原子增量到稳定的研究库路径：

```bash
python tools/ashare_tdx_intake.py --incremental \
  --source /home/kerstker/quantos-data-intake/ashare-pit/incoming/tdx-windows-20260902 \
  --output /home/kerstker/quantos-data-intake/ashare-pit/staging/tdx-windows-20260830/ashare_tdx_pit.sqlite \
  --report /home/kerstker/quantos-data-intake/ashare-pit/staging/tdx-windows-20260830/intake_report.json
```

目录名没有日期时必须增加 `--capture-date YYYYMMDD`。同一捕获日如出现不同清单会被拒绝；
捕获日必须严格晚于库内最新捕获，乱序补传会被拒绝，避免当前名称/ST/基本面与 metadata
倒退；既有历史 bar 的任意数值变化也会被拒绝，不会静默覆写。确需处理供应商历史修订时，
应另建版本化捕获并先评审修订语义。重复导入同一捕获并不是仅凭一条数据库记录直接返回：
导入器会以只读方式先执行 SQLite `quick_check`，再核对 schema v2 必需表/字段、捕获契约、
快照数量和日线核心一致性；任一项异常都拒绝复用，且不会改写现有数据库。

增量导入会在同一临时数据库事务中先把所有 `instruments.current_master_present` 归零并清空
`current_fundamental_snapshot`，再依据当次 TNF 股票池和其中可匹配的 `base.dbf` 重新建立。
因此，已从 TNF 移除的标的仍可保留历史日线，但不会残留为当前成分或继续暴露旧的“当前”
基本面；历次 `fundamental_snapshots` 仍按捕获日保留，供审计而非向过去回填。

成功输出同时报告源行数、本次新增行数、数据库总行数、名称/ST/行业/基本面覆盖和公司行为
容器状态。`TECHNICAL_VALIDATION_ONLY` 表示数据可用于技术研究，不代表已补齐历史基本面、
复权因子、退市、历史 ST、停复牌、涨跌停、融资融券、北向或逐单资金流。
