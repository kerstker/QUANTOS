# 黄金成本试验的独立核验材料交接

新增 `tools/gold_cost_review.py::review_request`，由现有黄金成本任务在保存终态manifest后自动调用。成功与失败试验都生成 `independent-request.json`，状态固定为 INDEPENDENT_REVIEW_REQUIRED、authoritative=false、economic_eligible=false。

交接前核对原输入/报告字节摘要、manifest定义身份、技术用途和回溯性质，正式TrialLedger只读打开并验证链及本试验的 PLANNED→RUNNING→PASSED/FAILED 状态。成功报告额外核对输入源码身份、信号输入摘要、外层报告摘要及原模拟器结果摘要。包中保留该试验的事件链、三个原文件摘要和源码身份；请求文件若已存在必须逐字节一致，不用新材料覆盖旧请求。

这只是本地结构/身份核对，不证明计算正确、源码字节可重建、来源真实性、全局试验完整性或锁定OOS。包明确要求独立检查上述事项以及实际账户成本与场所规则，不自签收据、不生成批准、不连接候选提升或下单路径。单试验材料不能冒充全局完整试验历史。

## 验证与实际材料

新接口先因缺失失败，八个材料交接回归覆盖正常/失败包、输入/报告修改、重算文件摘要却保留错误内部摘要、状态伪装、不可覆写请求、缺完成manifest。与队列、冻结输入、回放、时间轴及Runner边界联合60 passed；完整Ruff和治理检查通过。未跑完整canonical或部署。

此前真实公开数据 NO_SIGNAL 运行也已生成材料包：本机 `quantos-dev-output/gold-paired-inputs-20260928/1790576967861326126/state/data/cache/gold_cost_replay/results/1/independent-request.json`，SHA-256 `75bc37cc31591bda7b3302b42c06b051091e07bf3ad84c785c5799c9b36f1556`。仅追加请求，原输入/报告/试验记录未修改，也未用当前源码冒充原源码重新执行该历史结果。

尚未取得独立核验收据或经济资格。材料导出不是独立验证完成；全工厂F1仍未关闭。
