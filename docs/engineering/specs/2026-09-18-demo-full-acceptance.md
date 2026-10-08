# Demo 跨日闭环完整验收

承接[跨日开发断点](2026-09-18-demo-day-continuation.md)。用户要求按顺序完成系统闭环，
本阶段先冻结源码、完成完整软件验收，再准备运行侧审查；不据此直接部署、登记或下单。

## 源码及隔离

- 冻结源码：`4c0b61a25a446fabdfe922b5736a42f3b9a68504`，25个文件，含两批断点实现。
- 本仓库 `scripts/git-hooks/pre-commit` 无执行位；提交前使用已验证Python环境显式运行
  原钩子，仓库卫生、治理及暂存秘密扫描通过，没有使用 `--no-verify`。
- 4份用户未跟踪报告未纳入提交。验收使用独立干净工作树，不删除或临时移动用户文件。
- 工作树：`/home/kerstker/quantos-dev-output/demo-day-acceptance-20260918/source-validation-4c0b61a25`。
- 输出根：`/home/kerstker/quantos-dev-output/demo-day-acceptance-20260918`。
  驱动、日志、临时产物及匿名Docker配置都在该开发目录，没有使用运行数据库作测试夹具。

## 已完成的ARM64验收

复用仓库 `scripts/capture_arm64_console_evidence.py` 的固定协议，外层驱动仅记录命令诊断。
构建网络沿用本机已审查的回环TLS CONNECT辅助协议，不拦截TLS，不修改构建配方。

- 两个独立BuildKit构建器进行ARM64冷构建，分别608.800秒、577.562秒。
- 可复现性、固定Trivy离线扫描和 `network=none` 只读容器启动通过。
- 三类证据已在 `arm64-capture-4c0b61a25/arm64-console-evidence-bundle` 留存，
  再复制到独立验收工作树的对应允许生成文件；没有复制旧版本的验收结论。
- 本次临时构建器、测试容器、临时镜像和回环辅助进程已清理，运行服务未切换。
- 三个Python依赖文件与已安装 `5bb37730f8e33d0b4c15a4c7be2a1e844095881c` 逐字节相等，
  详见输出根 `dependency-comparison.json`。此检查不替代运行环境安装验证。

## 完整canonical

独立工作树的 `scripts/validate.py --timeout-seconds 3600` 已完整 **28/28 PASS**。
输出根 `result.json` 为 PASS/returncode 0，总耗时4380.843秒（含ARM64构建）。
`canonical-report.json` 为 ALL、28项通过、失败/错误/超时均0，源码仍为上述 `4c0b61a25`。
后端8337通过/19跳过，执行与工具4381通过/2跳过，前端453通过；跳过及警告保留。

已在独立分支 `codex/demo-day-accepted-20260918` 留存15份允许生成的验收文件，
提交前显式原钩子通过。证据提交为 `27479d73a96fb215b6578b48850c5eb9a7a6aa62`。
干净交付工作树为输出根 `accepted-source-4c0b61a25`，manifest 绑定通过，记录在
`accepted-checkout-4c0b61a25-binding.json`；摘要
`b856031dbf2984cc36d095825d3ea5a676f1bb71d97efd69303cbecfaa903582`。

该交付工作树前端生产构建通过，104份应用文件与完整验收产物逐字节一致，仅版本合同
绑定新证据提交，详情见 `accepted-frontend-comparison.json`。开发分支仍独立继续。
这些结论不覆盖后续 `9c62ab01c` 组合任务接线及其后增量。

## 运行侧限制

只读systemd日志仍显示旧版封装预算耗尽、账户起点采集返回通用失败码；不能把采集失败
直接归因于网络。受保护目录无普通用户读取权限，`sudo -n /usr/bin/python3` 返回需要密码，
因此尚未取得当前私有目录的文件数量/体积证据。没有读取凭证、扩大权限或删除原历史。

后续顺序：准备具体发布审查 → 授权部署与真实备份恢复验证 →
账户采集/跨日来源核对 → 本机操作员有限登记与实际往返。实际资产配置仍需原有研究、
组合、自然成本及独立经济证据，不能由这批软件验收替代。
