# 长周期公开原件采集与冷读

延续[长期正式登记预算](2026-10-05-gold-registered-long-budget.md)和固定
[F1–F5交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)。本批补齐持续
采集的一天与512MiB限制；不是30天实际覆盖、完整研究输入、发布或经济准入。

## 原问题与最终行为

原报价与K线会话最长一天，原件各限512MiB；K线还限500000条消息。
旧入口和默认预算保留。显式 `GOLD_PUBLIC_COLLECTION_BUDGET_V1` 冻结时长、
报价字节/回执/段数、K线字节/消息/连接次数；最多32天、报价64GiB、K线16GiB、
8000000报价回执、4096报价段、16000000 K线消息和512次连接。
布尔、零、负数、非整数、缺失/额外字段、超限均拒绝。预算在调用网络或
创建输出前核对；新报价模式必须分段，不能隐式扩大旧入口。

新报价计划/保留段/会话摘要为V2，原单段仍为V1。采集预算贯穿原公开GET
生产者、原件、计划与摘要；使用既有 FRESH_CADENCE_V1 的5秒超时与有限重试。
原分段共享边界、跨UTC日容量更新、资金费实际接收回执、规则快照和缺口保持。
原件写入和fsync后追加私有 `originals.jsonl`，冻结顺序、文件名、字节数与摘要；
索引本身计入报价数据预算。失败/完成摘要单列最多65536字节保留额度，数据
容量耗尽仍可保存失败，不覆盖原件，不将部分采集发布为成功。

新K线计划/manifest/摘要为V2，仍调用原 `GoldBarStream` 校验全部盘中、
收盘及重复消息，不用仅收盘投影替代原件。有限连接预算复用原WebSocket
生产者和同一游标，旧默认四次保持。场所公开连接满24小时会断开，见
[Binance连接说明](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect)。
重连保留实际错误与时间，有限退避上限30秒，不补历史消息或改写缺口。
处理拒绝或资源耗尽时，单独保留客户端131072字节框架内的被拒原消息及摘要；
异常会话仍是INCOMPLETE。最多额外一个2MB拒绝诊断，不绕过消息或数据预算。

报价冷读分两次流式核对所有原件，包含错误、非选中段及完整共享边界；
检查原始样本计数、接收跨度、缺口、文件集合和硬链接别名。K线冷读复算原
决策、段证书、计数与首末实际接收时点，预算错配、降级和额外文件拒绝。
这些检查证明保留材料能重建，不证明独立来源、时间或账户经济事实。

## 配对入口

`tools.gold_public_session` 复用原 `collect_joint`。两侧使用同一冻结预算，
各有固定余量：报价目标时长加30秒、K线加120秒，以覆盖预热和结束回执。
实际首末报价跨度不足目标或最终K线水位不足，仍为INCOMPLETE；不是按进程
运行时间把不足跨度记满。启动前检查显式数据预算与128MiB附属文件余量的
可用磁盘，输出私有且不可覆盖；不预占磁盘，不承诺其他进程不会消耗空间。
失败或取消先停止并排空原报价线程，再保留终态。两侧原计划、预热原件与
摘要交叉绑定；完整冷读保持 replay_ready/economic_eligible/submission_enabled=false。

预算示例（须独立保存JSON，实际容量还需按原件速率验证）：

```json
{
  "schema": "GOLD_PUBLIC_COLLECTION_BUDGET_V1",
  "maximum_duration_seconds": 2764800,
  "maximum_quote_bytes": 68719476736,
  "maximum_quote_receipts": 8000000,
  "maximum_quote_segments": 4096,
  "maximum_bar_bytes": 17179869184,
  "maximum_bar_messages": 16000000,
  "maximum_bar_connections": 128
}
```

从冻结源码和已验证Python3.12环境执行（路径须为隔离开发输出）：

```bash
python -B -m tools.gold_public_session observe --history-dir HISTORY \
  --output-dir OUTPUT --budget-file BUDGET_JSON --seconds 2592000
python -B -m tools.gold_public_session review --directory OUTPUT
```

本命令不安装或切换服务，不登记候选，不读凭据、不下单。

## 验证与剩余工作

初始三个预算入口回归失败；修复后首批40通过。四次断线后第五连接的回归
先失败；处理拒绝/预算耗尽时原消息缺失的两个红例保留。最终13文件联合
167通过/74.64秒（driver74.959341秒），3109个Python文件前后相同，包含原
采集/重连、V1 prefix/native消费者、campaign、图和卫生；不累加重叠批次。
新配对冷读覆盖两份真实生产格式的合成原件及非选中损坏拒绝，时钟/网络/来源
为隔离夹具，不能当作自然覆盖或真实网络性能。

原精确提交2b1d4650c132d54fabbb8c355364a2d9b1866c38已完成实际短探针，原进程
3134817/session36653退出0；driver154.660882秒、3109 Python及完整源码身份保持。
新鲜1500根预热仅为历史输入，historical_pit_proven=false。真实7报价跨度62252ms、
最大缺口12289ms、零超30秒缺口；176盘中K线消息/93637字节/零收盘，完整冷读
RAW_ORIGINALS_RECONSTRUCTED。报价原件17094289字节，单份exchange原件最大
2834430字节。按10秒基础采样与UTC收盘密采上界估计345636样本/2905段，按本次
最大原件体积保留全部副本约32.53GiB，原显式16GiB不足。64GiB配置的红例先失败，
只调整本批新预算的显式报价容量上界，不改旧512MiB。该估算未包含延迟资金费
阶段反复预热，不保证未来响应大小；K线短速率推算约1.62GB，同样不是长期实测。
探针使用原16GiB预算，原件和失败记录均不重标为新预算；真实authority仍未安装。
容量修正后九文件联合124通过/15.84秒（driver16.156053秒），3109 Python前后保持。
此前精确2b提交三文件58通过/5.97秒及DGX基线通过，tests_returncode=null，仅基线。
修改Python与新文件均完整Ruff、全仓F821/E9通过；原修改范围零诊断，未新增。

完整30天观察尚未取得；V2长原件到正式native输入的有界编译与独立采集/执行
版本绑定仍需续接，
不能将本批RAW会话当作现有V1 native prefix直接使用。随后仍按固定范围完成
独立源/结果/时间、实际成本风险与锁定OOS/候选、全部writer同cut恢复、最终
同版ARM64/canonical全门、获准现场安装/恢复、五秒链及实际24小时Demo—现金—
再研究。最近完整门仍旧5ae版30 PASS/1 FAIL/1 TIMEOUT。目标ACTIVE，盈利策略
后续单列，机构异机延期不计PASS。审计位于隔离输出gold-long-collection-20261005。
