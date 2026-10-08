# 工厂联合预检与失败复现

## 范围与原始结果

使用原 `scripts/validate.py` 的 python-quality、backend、live、frontend 四组选择阶段，在隔离状态目录运行；不是 canonical ALL，也不是发布验收。结果目录为本机 `/home/kerstker/quantos-dev-output/factory-integration-preflight-20260928-dmk3y7fa`。

Ruff、mypy、前端 typecheck、ESLint、测试和生产构建通过。后端阶段在 1800 秒超时，已出现失败但未完成，不能统计为通过。live/tools 阶段 5522 passed、5 failed、2 skipped。原驱动终止并退出 1。测试前后对 3775 个来源路径核对，已有文件内容无变化；所有后续修正均发生在本轮结束后，不能借用旧结果证明修正后的整体验收。

## 已复现并修正

- 健康接口的显式任务清单遗漏六个新黄金入口；补齐清单，并要求所有黄金入口仍为 DIAGNOSTIC_ONLY、无经济 authority。
- 无 CLI 包的 campaign 发布测试等待已移除的 sleep 钩子；改为在原生命周期内有界等待真实原子发布文件，保留禁止 tools 导入、准确摘要和退出行为检查。
- 本地 console 测试显式清理仅针对“默认未配置”场景的状态/发布变量；调度入口测试检查恢复调用前环境，而非假定调用前不存在变量。
- 跨日开平仓 fixture 使用原 functional SID 格式；组合误入 functional 入口仍拒绝，更新为实际更早的 FUNCTIONAL_SCOPE_BINDING 错误。未放宽任何生产校验。
- 按原生成器刷新服务依赖图；20 域、52 边、零循环，域间边无增删，仅源码数量与引用及摘要变化。

保留 QUANTOS_STATE_ROOT 的同环境复验：上述五组 92 passed；依赖图九项通过；改动测试 Ruff F821/E9 通过。

## 仍未通过

供应链失败已单项复现为 Python audit manifest digest mismatch：当前 pyproject.toml 与旧审计摘要不一致。尝试原审计生成器，未取得 JSON；进一步原 pip-audit 复现表明临时环境升级 pip/wheel/setuptools 失败。未改写旧报告摘要，也未将未执行的外部审计标为通过。其余依赖发布证据的失败仍需在一致冻结源码上处理，后端超时必须完成后续验收。

以上增量未部署、未执行真实 Demo，不证明经济准入或工厂持续运行。黄金未知事件指纹仍未匹配，现场持续采集未恢复。

## Python 外部审计续验

进一步提取扫描器异常原因，默认 pip 索引返回 `No matching distribution found for wheel`。仅对本次原生成器进程设置 `PIP_CONFIG_FILE=/dev/null`、`PIP_INDEX_URL=https://pypi.org/simple`，不修改用户/系统配置、不更改项目依赖，重试成功。原 pip-audit 2.10.1 实际外部查询 74 个依赖，已知漏洞 0；新报告观察时间 1790605150，经原 `--check` 核对清单摘要通过后更新 `evidence/vulnerability/python_audit_latest.json`。原失败日志保留，不能将新报告倒填为前一轮已通过。供应链派生物及冻结源码/完整验收仍待继续处理。

Python审计/供应链两组随后复验为14 passed、5 failed；五项均明确受 `_check_clean_boundary` 拒绝，首个未允许脏路径为 `.gitattributes`，已不再是审计清单摘要问题。不能放宽源码边界或仅重算摘要制造通过；下一步需保存现有工作树、审查并冻结完整源码，在固定版本上刷新派生证据及完整验收。
