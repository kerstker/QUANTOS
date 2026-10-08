# 中文操作记录名称：浏览器到研究存储

2026-09-08，基于 `32ef1839ed267987e18011346456557e5a29f56e` 的开发增量。

中文名称曾直接进入 `X-Operator`，浏览器在构造 HTTP Headers 时就拒绝发送。
普通研究提交/取消、AI 工厂操作与持续研究计划现在复用同一客户端编码函数，
代理验证后保留编码头转发，Runner 在四个入口统一解码后保存原始 Unicode 标签。

协议：可打印 ASCII 标签仍使用原有 `X-Operator`，不猜测或解码百分号、前缀。
其他有效 Unicode 使用标准、规范 Base64 UTF-8，并显式声明
`X-Operator-Encoding: base64-utf8-v1`。最多 128 个 Unicode 码点，最多 684 个编码字符。
拒绝未知编码、重复头、非规范 Base64、无效 UTF-8、控制字符和超长名称。
操作标签是自填记录名称，不是身份认证；原有研究令牌仍独立验证。
研究、Paper、Live 权限及经济准入未改变。

## 验证

材料目录：`/home/kerstker/quantos-dev-output/operator-workflow-20260908`。

- 修复前保留 `backend-red.log`（11 失败）与 `frontend-red.log`（真实 Headers 中文失败）。
- `backend-isolated-final.log`：相关 Runner、工厂 API、代理、研究边界 82 通过；
  包括四入口解码、原始标签入库、重复头/错误编码、长度边界与令牌不可替代。
  一条既有 Starlette/httpx 弃用警告保留。
- 直接执行代理脚本的导入路径遗漏已复现并修复（`launcher-red.log`）；
  相关回归纳入上述 82 项。`stack-final.log`：另有启动器 10 项通过。
  最初合跑时隔离状态变量触发了启动器的发布身份保护，2 项失败保留在
  `backend-final.log`；启动器测试按其环境合同独立运行，未修改或放宽保护。
- `frontend-full.log`：47 文件、312 测试通过；`build.log` 通过。原报告对 `eslint.log` 的 PASS 表述有误，进程最终报
  `no-control-regex`；2026-09-08 后续校准增量改为码点校验，重新确认 ESLint 退出 0。
  覆盖四种调用、ASCII 兼容、Unicode 长度及无效字符在发送前拒绝。
- `http-qa-final.log`：真实 Chromium 加载当前客户端模块，经实际同源代理到
  FastAPI Runner，在临时状态库创建、暂停、恢复、停止计划，状态依次
  RUNNING / PAUSED / RUNNING / STOPPED；中文与 emoji 名称保留，错误令牌返回 401。
  此项验证客户端模块和 HTTP 链路，不声称本轮重验全部界面。调度关闭，不调用模型。
  测试服务已关闭，未写常驻研究状态；早期 QA 未正确读取状态字段的日志仍保留。

## 交付边界

本次为软件增量，未跑新的完整 canonical、ARM64 发布验收或切换服务。
已运行的首期版本仍是 `db676c2e30526d3fdbf631327c82d607741938d5`，
其完整 28/28 PASS 只覆盖该源码。新版本发布后才能在常驻界面使用中文操作名称。
同步纠正[开发路线](../plans/2026-09-08-alpha-factory-lifecycle.md)中的首期验收状态，
保留外部经济证据、自然前向成熟样本、真实场所与生产恢复缺口。
