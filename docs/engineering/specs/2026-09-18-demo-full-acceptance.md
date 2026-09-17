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

正在独立工作树执行 `scripts/validate.py --timeout-seconds 3600`。
最终状态以输出根 `result.json` 及本源码的 `canonical-report.json` 为准；
进行中的日志和历史28/28不能作为本轮完整通过证据。

## 运行侧限制

只读systemd日志仍显示旧版封装预算耗尽、账户起点采集返回通用失败码；不能把采集失败
直接归因于网络。受保护目录无普通用户读取权限，`sudo -n /usr/bin/python3` 返回需要密码，
因此尚未取得当前私有目录的文件数量/体积证据。没有读取凭证、扩大权限或删除原历史。

后续顺序：本版本完整验收及证据绑定 → 准备具体发布审查 → 授权部署与真实备份恢复验证 →
账户采集/跨日来源核对 → 本机操作员有限登记与实际往返。实际资产配置仍需原有研究、
组合、自然成本及独立经济证据，不能由这批软件验收替代。
