# RF3日线信号观察（尚未启动）

本入口只采集BTCUSDC公共日线并保存固定90收盘趋势目标，不访问账户、不下单、不计算收益。
规则见[预登记规格](../engineering/plans/2026-09-20-rf3-daily-forward.md)，候选来源见[RF2结果](../engineering/specs/2026-09-20-rf2-first-results.md)。

## 开发与复核

Claude Code会话 `144ecb64-d869-4cb7-9035-34d21d9029ea` 完成纯合同、单次CLI及49项合成测试。Codex另补5项发布验收，修复预算预占、最终写入时钟、持久写入完成后的迟到判定，以及已保存信号的原始回执复算验证。相关RF3及RF2信号联合134项通过（1.96秒），CLI help与仓库卫生检查通过，未运行完整验收。

一次真实公开连通性诊断返回完整90根日线，请求/接收毫秒为1789852479414/1789852480100；没有保存为前向预测、没有账户访问。它不能证明下一次请求必然成功，更不是已经持续观察的证据。

每次网络请求前先持久写入claim，进程中断也消耗该窗口预算。完整原文保存在attempt文件。signal落盘并同步后再测时钟，publication回执记录结果；迟到或没有publication的signal不计为有效发布，旧文件不覆盖。状态读取按原始回执重算目标，不能只凭signal文件存在就计数。

## 授权后操作

在项目根目录，用已验证Python3.12。以下是操作说明，**本批没有执行真实登记、没有安装调度器或常驻服务**。状态根必须全新；不得指向旧实验、`/srv`或`/opt`。

```bash
PYTHONDONTWRITEBYTECODE=1 /home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python -m tools.rf3_daily_forward --state-root /home/kerstker/quantos-dev-output/rf3-daily-forward-20260920 --register --research-result /home/kerstker/quantos-dev-output/rf2-log-slope-20260920-attempt1/case-return_sign90-5.json
PYTHONDONTWRITEBYTECODE=1 /home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python -m tools.rf3_daily_forward --state-root /home/kerstker/quantos-dev-output/rf3-daily-forward-20260920 --once
PYTHONDONTWRITEBYTECODE=1 /home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python -m tools.rf3_daily_forward --state-root /home/kerstker/quantos-dev-output/rf3-daily-forward-20260920 --status
```

登记之后的下一个UTC日开始14个窗口。每日00:00–00:05 UTC即北京时间08:00–08:05；单次调用若不在窗口内不请求、不补写。自动每日调用须单独授权安排，登记本身不启动后台进程。失败最多3次，已成功的重复调用不再请求。

采集绑定组件变化则停止原窗口；无关UI文件变化不会停止采集。不编辑已登记plan或改哈希来绕过检查。不完整发布保留给人工核查，不自动删掉重来。

## 尚未完成

- 没有自然成熟的真实前向窗口或成本后收益。14天观察不等于14天盈利，更不证明统计稳定性。
- 日度目标到V2账户的纯适配器和离线CLI已实现，相关118项联合验证通过，见[账户增量](../engineering/specs/2026-09-20-rf3-forward-account.md)。这不是自然前向评估已经运行的证明。
- 测试网场所待用户选择。`tools/testnet_execution_service.py` 的authority/mandate及版本化限额仍需人工提供；`tools/testnet_mandate_proposal.py` 只生成待审提案，不签发批准。
- 未启动真实前向窗口、未部署、未访问交易凭证、未下测试网或主网订单。

## 成熟归档的离线账户评价

工具`python -m tools.rf3_forward_account --state-root ABS_STATE --market-root ABS_MARKET --output ABS_NEW_OUTPUT`只消费原观察器和`tools.profitability_forward_market.capture_market`已经生成的归档，不自行联网。命令中的三个参数须替换为互不重叠的绝对路径，输出目录必须不存在；仍使用上文已验证解释器。

完整14天信号和最后一分钟行情成熟之后才可评价。行情目录须保留原request-plan与全部编号JSON（含原result_hash），不能重排响应或删掉失败记录伪装完整输入。九组案例先登记再运行，失败留存；成功摘要为模拟净收益，不是测试网账户收益。

输出包括`registration.json`、`trials.db`、`cases/`以及成功时的`summary.json`或失败时的`failure.json`。PASSED只指该案例计算完成；存在整批失败时，旧案例不能作为完整评价通过。不得覆盖旧输出重跑到盈利，也不要从本说明推断已获持续观察或交易授权。
