# Canonical 验收补齐原验证域

状态：相关行为回归与真实收集完成；完整验收尚未启动。父提交
`73efd7c78ad851c5395b3a234d4c7d03ffe46677`，独立分支
`codex/canonical-validation-coverage-20261006`。输出目录为
`gold-release-acceptance-20261006-predeclared-window`。

## 漏收集与修复

核对[原完整门](../../../scripts/validate.py)发现：后端参数列表未包含
`tests/validation`，遗漏原件身份／投影、独立签名、登记人口、OOS／统计、
风险重放等 22 个现有验证模块。此前声称四个前向解包失败已经处于完整后端
范围不准确，实际调用和新增收集结果作为当前依据；旧报告保持原件，不补写
未执行测试或借用其 PASS 证明新的完整范围。

本增量只在原后端命令加入整个验证目录和说明。原阶段、执行顺序、backend／all
别名、其他参数、Phase 13 后置规则和 3600 秒发布阶段预算保持。
用目录选择器使今后新增验证模块自然进入完整门。

[行为回归](../../../tests/governance/test_validation_suite_coverage.py)使用原
命令和实际 `_run_check`，在隔离临时目录提供其他选择器的通过测试，另放入
必定失败的验证测试。修复前 backend 和 all 都报告 PASS，却没执行验证失败；
修复后该失败使原命令返回 1／FAIL，JUnit 确认失败数为 1，原子进程输出保留
明确失败标记。另检查当前每个验证模块是否被原选择器覆盖。

## 实际验证

- 原 `validation-coverage-red`：**3 FAIL / 0.38 秒**，driver
  0.510949253 秒，3134 Python 前后保持。包含两次实际假绿色及当前模块遗漏。
- 初次 `validation-coverage-green`：**48 PASS / 2 FAIL / 3.18 秒**，
  driver 3.309925974 秒。原命令已经正确 FAIL；新增回归错误读取了该实时
  pytest 路径不提供的 `child_output_tail`。修正为捕获实际子进程 fd 输出，
  失败数、退出码和原失败标记断言保留。初次错误日志不删。
- 最终五组 **50 PASS / 3.26 秒**，driver 3.386527333 秒；3134 Python
  前后相同。覆盖原 gate contract、runner、假绿色和统一门登记，不算完整门。
- 真实原后端选择器 `--collect-only`：**10962 nodes / 22 验证模块 /
  462 验证 nodes**，22 个预期模块全部收集，11.60 秒，driver 12.455897236 秒。
  完整 nodeids 与摘要保留，3134 Python 保持。仅证明真实收集无冲突，未执行
  测试体，不能称 10962 项通过或完整验收通过。

两份 Python 完整 Ruff、全 E9/F821、原服务图 `--check`（20 域／52 边／零环）
和 diff 检查通过。提交前仍须实际卫生／治理钩子、干净冻结与选择整合。
DGX 精确环境记录为 `/tmp/quantos-dgx-development-_1ukcznk/baseline.json`，
父 73efd、Python 3.12.3 ARM64、原哈希锁和显式 Node，仅环境检查，未运行 `--test`。

## 旧候选停止与下一步

父 df3 保留了已经实际复现的前向投影四／五项解包缺陷；新版本也要补齐完整
验证域。本机 df3 验收因此被新候选替代。先持久化停止意图，再仅 SIGTERM
本轮实际 docker-buildx 构建客户端，复验 PID/start_ticks／父链／cwd／本轮
参数归属；未停止现场服务。原协调器自行报告 INCOMPLETE／returncode 1，
2695.956578817 秒，3133 Python 前后相同。五个已知本轮进程都已消失；canonical
尚未启动，原日志、源文件与驱动保持。停止原因是已确认缺陷和候选替代，
不是观察超时、增加预算或宣称父版已经执行了遗漏的测试。

后继须从本次干净提交执行自身原 ARM64→canonical ALL→证据保留；不复制
父候选的阶段 PASS。固定 F1–F5／四组、五秒链和实际 24 小时要求保持。
Gold 独立 OOS 适配、真实预封存／访问记录、实际研究风险成本、用户 PC 核验
和现场交付仍待完成；经济、晋级、下单与实际 trust 未开启。

## 冻结、整合与启动

独立提交 `6b227509db9bc0734255cc2dd7cebd6eedb26b46` 仅三路径。原钩子664，
显式验证解释器PATH执行实际sh钩子，790文件零违规通过；未chmod或绕过。
1531个未改运行模块／原脚本父像与Main一致，备份后只选择整合三路径。
Main全部Python8001→8002；五组 **50 PASS / 3.80 秒**，driver3.952743490秒，
排除Node后的8001 Python保持。HEAD913325cc及index95b9d9f1保持，不暂存/提交Main。

冻结Source3135 Python（Node一项），应用3134与最终回归一致；原前端锁不变，
离线npm ci 456包退出0。六驱动原模板仅换提交及实际公开IPv4，归一AST一致；
原TLS主机、ALL、3600秒保留。启动free450843246592大于原研究预留与28GiB余量。
本版原ARM64→canonical ALL→保留已启动，session85969，coordinator2001305／
start35241617；图、持久化、源码、扫描器、基础镜像及构建器预检通过，冷构建A
在途。暂无完整门终态。最终精确环境基线/tmp/quantos-dgx-development-yyyuvr4r，
仅环境验证。未部署或完成真实独立核验／现场交付。
