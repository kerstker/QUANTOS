# DGX 引擎开发入口

2026-09-05 基线：`aarch64`、Python `3.12.3`、Node `v24.16.0`。
源码起点 `1e324568b4bfd1e3b98eab7de5dde0632aa33103`；开发分支
`codex/dgx-engine-foundation-20260905`，目录
`/home/kerstker/QUANTOS-backtest-runtime-integration`。

只读核实 Binance resident 与 console 的进程命令行仍引用
`/opt/quantos/releases/cd7698f168c05d01127ede2dd8a60424d9816b7e`，
工作目录为 `/srv/quantos/state`。这是本次检查时的运行快照，未来须重新核实。
`/home/kerstker/src/QUANTOS` 的未提交证据和报告保留，不作为开发测试输出。

## 独立开发环境

2026-09-19 Kraken整合验收发现 AnyIO 4.14.1 的新披露漏洞后，安全补丁测试环境为
`/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv`。
它是原开发环境的独立副本，仅通过哈希校验安装 AnyIO 4.14.2；原环境保留4.14.1，
用于尚在运行的旧源码验收。包含本次安全补丁的新源码应显式使用新环境的
`bin/python`，不能用旧环境宣称依赖与新锁文件匹配。其余依赖版本不变，运行服务环境未修改。

开发虚拟环境位于
`/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv`，
由 Python 3.12 创建，已使用
`pip install --require-hashes -r requirements-ci-linux-aarch64.lock` 安装。
没有修改 `/opt/quantos/venvs` 中运行服务使用的环境。锁文件中的全部发行包版本
经 `importlib.metadata` 核验；版本核验不替代安装时 wheel 的 hash 检查。

在任意工作目录、非交互 shell 中执行：

```bash
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python \
  /home/kerstker/QUANTOS-backtest-runtime-integration/scripts/dgx_develop.py \
  --expected-head 1e324568b4bfd1e3b98eab7de5dde0632aa33103 \
  --node /home/kerstker/.nvm/versions/node/v24.16.0/bin/node --test
```

示例 HEAD 是开发起点；提交后必须将它替换为实际审查的完整提交号。
不应在无人值守调用中用动态 `git rev-parse HEAD` 代替预先约定的版本。

入口会检查架构、Python、HEAD、锁文件与依赖清单、已安装版本和显式 Node 路径。
源码根由脚本自身位置确定；未提交状态及 Python 源码树摘要进入基线记录。
每次新建 `/tmp/quantos-dgx-development-*`，保存 `baseline.json`、
`tests.log`、`tests.xml`，临时夹具和 pytest 缓存也位于该目录。
测试子进程使用限定的环境变量、单线程数值库和显式测试白名单。
不继承调用 shell 的 Python 注入配置、额外 pytest 参数或运行状态配置。
这不是操作系统沙箱；白名单目前仅包括执行、风控与入口自身的离线测试。

## 四类目录与提交顺序

| 用途 | 路径与写入规则 |
|---|---|
| 不可变运行代码 | `/opt/quantos/releases/<commit>`；开发时不改 |
| 运行状态 | `/srv/quantos/state`；不得用作可写测试夹具 |
| 冻结研究数据 | D1 尚未交付；需独立清单与摘要后只读消费 |
| 开发环境和测试输出 | 上述独立 venv 与每次生成的 `/tmp/quantos-dgx-development-*` |

当前分支是此路线首批代码的合并入口；不覆盖旧主目录修改。每个增量先通过相关
失败回归、专项与治理检查。M1 完成后按 R1 冻结源码，再由既有生成器处理 ARM64
构建、扫描、冒烟和派生证据，最后执行完整 canonical gate。源码变更不能靠复制
旧 release 证据来取得通过；本开发入口不生成发布准入或经济准入证据。

恢复与发布操作保留在 O1/R1；本说明没有启动或切换运行服务。
