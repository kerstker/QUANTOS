# DGX 引擎开发入口

2026-10-01 发布安全刷新候选使用新独立解释器：
`/home/kerstker/quantos-dev-output/system-security-delivery-20261001-u5b0_090/venv/bin/python`。
PyJWT 2.15.0、urllib3 2.8.0修复新公告，其他发行包及制品未换；四锁在线解析和31项安全/身份检查通过。原3db完整流程在最新漏洞扫描终结、6695后继未开始自身验收；旧PASS或WAITING不适用。新版本完整门单独核验，未部署。见[实现记录](../engineering/specs/2026-10-01-release-security-refresh.md)；不要使用下面历史2.14.0环境验证新锁。


2026-09-30 PyJWT 安全补丁候选62534ec7及后续包含该补丁的源码使用独立解释器：
`/home/kerstker/quantos-dev-output/gold-pyjwt-security-20260930-1p9xxfrg/venv/bin/python`。
该环境仅将PyJWT 2.13.0更新为核验wheel hash的2.14.0；其余发行包版本经比对未变，原quality环境继续服务旧研究进程。四锁在线解析、61项相关检查、固定锁审计及原requirements审计通过；完整ARM64/canonical验收仍在运行，不能以局部检查代替发行验收。不要用仍含2.13.0的原环境验证新依赖锁。

2026-09-05 基线：`aarch64`、Python `3.12.3`、Node `v24.16.0`。
源码起点 `1e324568b4bfd1e3b98eab7de5dde0632aa33103`；开发分支
`codex/dgx-engine-foundation-20260905`，目录
`/home/kerstker/QUANTOS-backtest-runtime-integration`。

只读核实 Binance resident 与 console 的进程命令行仍引用
`/opt/quantos/releases/cd7698f168c05d01127ede2dd8a60424d9816b7e`，
工作目录为 `/srv/quantos/state`。这是本次检查时的运行快照，未来须重新核实。
`/home/kerstker/src/QUANTOS` 的未提交证据和报告保留，不作为开发测试输出。

## 独立开发环境

2026-09-23 Python质量门增量使用独立环境
`/home/kerstker/quantos-dev-output/platform-completion-20260923/quality/venv`。
它由Python 3.12创建，通过新ARM64哈希锁安装76个制品，原71个制品未更换，
新增Ruff、mypy及其三个依赖。AMD64锁同时重算，移除只在Windows条件/extra下
使用的colorama，其他旧制品保持不变。旧运行环境未修改。

新源码的`validate.py`增加全仓F821/E9检查，以及安全配置、PIT时间、停机状态、
研究数据端口共5文件的mypy检查；尚不代表全仓严格类型覆盖。只运行这两个阶段：

```bash
/home/kerstker/quantos-dev-output/platform-completion-20260923/quality/venv/bin/python \
  scripts/validate.py --stage python-quality:ruff --stage python-quality:mypy
```

上述为增量检查，不替代canonical全量验收；不要用缺少新工具的旧环境运行新门禁。

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

## Alpine只读镜像依赖锁

`requirements-console-build.lock`用于构建工具，
`requirements-console-runtime.lock`用于完整运行依赖；两者由
`scripts/generate_console_dependency_lock.py`生成，只针对Dockerfile固定的
CPython3.12 Alpine ARM64镜像。修改基础镜像必须重新核对Python版本与musl兼容
标签，不可直接换用开发环境的manylinux锁。

普通`--check`核验锁结构、根依赖和源码摘要；
`--check --verify-resolution`查询公共索引重新解析固定版本并比较制品摘要，
不同即失败且不修改锁。更新锁后须独立冻结源码，运行两次原生冷构建、扫描、
只读冒烟和canonical全量门，不能把旧源码的构建证明绑定到新锁。
