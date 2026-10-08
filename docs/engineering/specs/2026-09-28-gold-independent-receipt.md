# 黄金研究独立收据接收与后到复核

黄金成本实验结束时先核验留存材料，再读取既有公开核验方登记表和该试验的
`independent-envelope.json`。签名接收复用既有 Ed25519 收据协议；本增量没有签发器、
登记公钥接口或经济准入功能。核验方只取自仓库受治理的登记表，不接受随收据附带的信任根。

新增 `gold_cost_receipt` Runner 任务只接收 1–1000 的整数 attempt，从统一状态根下原
`data/cache/gold_cost_replay/results/<attempt>` 读取材料。后到收据可以通过原 JobQueue
再次核验，不重跑回测，不改变原试验终态。每次检查按检查时间、原材料摘要、登记表摘要和
收据摘要生成独立内容寻址记录；过期检查拒绝不能覆盖以前的检查历史。

状态包括 INDEPENDENT_AUTHORITY_UNCONFIGURED、REJECTED 和
VERIFIED_SIGNATURE_ENGINEERING_ONLY。队列 done 仅表示检查执行完毕，实际结果由
verification_status 表达；所有结果 authoritative/economic_eligible 均为 false。
后续消费者必须重新检查时效，不能将旧的成功检查当作永久批准。

## 验证

新增后到收据/时效变化和受限参数六个回归先失败，再实现入口。
收据、材料交接、成本任务、Runner 边界、Console API 合同五组联合
**134 passed，1 skipped**。包含临时测试公钥和测试签名；没有修改真实登记表或签发真实批准。
真实队列回归验证有效签名、过期拒绝与原 input/report/manifest/ledger/request 字节不变。

真实已留存 NO_SIGNAL 试验 `7d282ca4f657c534975b86acba4f35e9dcc0e9fd31e0d9db3293b8adac754280`
完成复核任务 `run_3d24367ce1fc14fcc8b9a6393859bb04`。原文件摘要全部不变；
请求 SHA-256 为 `75bc37cc31591bda7b3302b42c06b051091e07bf3ad84c785c5799c9b36f1556`。
真实登记表 keys 为空，收据不存在，返回 INDEPENDENT_AUTHORITY_UNCONFIGURED。
现场证据保存在本机 quantos-dev-output/gold-receipt-check-20260928/1790578422939344357/summary.json。

Ruff E9/F821 与仓库卫生通过。完整 Ruff 检查仍报告 run_kinds.py 原有两项 E701、
一项 E402，不将其隐藏或称作完整通过。本次不是 canonical 全量验收，也没有部署。

## 剩余交接

研究核验登记与 Demo 执行签发是独立机制。实际独立核验方、真实账户成本/历史规则、
锁定 OOS、全局试验历史及经济准入仍未闭合；有效工程签名也不能替代这些条件。
F1 尚未完成，下一阶段继续连接既有研究编排与黄金实验，保留上述真实缺口。
