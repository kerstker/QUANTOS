# 受保护市场分区接入原执行与反馈链

## 范围与当前证据

本增量以已冻结的分区安装版 `6a686b3a329780b8ee189ee5b3a03419f0e734e3`
为父版本，接续[分区候选](2026-10-01-demo-market-partition.md)和
[保护安装工具](2026-10-01-demo-market-partition-install.md)。目标是在保留整个旧
BTCUSDC 执行历史和 USDC 事实的前提下，让原执行入口读取独立的 XAUUSDT/USDT
分区。它不安装分区、生成黄金授权、晋升候选、修改停机状态或启动服务。

相关十四组回归 **189 项通过，145.30 秒**。其中真实本地管道完成原独立签发、
验签和 reservation 留存；隔离合成账户的旧 BTC 与新黄金 receipt/OMS 历史由
原 V2 replayer 重建。角色/属主、账户来源和 HTTP 结果使用隔离夹具；没有实际
Demo 下单、真实 24 小时运行或经济有效性证据。该结果不是本版本 canonical PASS。

## 最后持久提交与角色隔离

安装工具仍先排他持久保存 intent、候选、旧 registry、操作员与复核材料，再
依次写 issuer 和 executor 的同字节私有镜像。两镜像写出并不代表安装完成。
两次读回和原材料末次复核通过、`PARTITION_READY_FOR_ACTIVATION` 结果持久保存后，
最后才排他写 `/etc/quantos/demo-market-partition-activation.json`。

这份 root:root、0644 的共同记录只包含源码与材料摘要、完成时间和技术标记；
不公开余额、密钥或操作员批准。两角色各读自己的 0640 私有镜像和这份共同记录，
绑定镜像摘要、候选、源码与完成时间。缺本地镜像、缺最后记录、任一摘要/权限/
源码不符均拒绝。已有 intent 或任何部分写入拒绝重复安装，不能自动覆盖恢复。
最后写入的 I/O 失败可能留下完整文件；调用者保留 UNKNOWN，不推断一定未提交。
运行消费者只接受实际可验证的完整记录，黄金执行仍需另外的有效授权。

## 原账本与新账户观察

`demo_market_partition_runtime` 绑定实际不可变发布目录与执行 UID；原入口的
已验收源码、独立签名、mandate、风险和当前停机核验继续存在。旧 registry 的
全部非 key 字段及整个旧 key 前缀必须保持一致；原 key 已过期，新 key 不得
早于安装时点生效。安装记录中的 `submission_enabled` 和 `economic_authority`
始终 false；记录和新 key 本身均不是交易批准。

每次分区读取先核对整个旧 reservation/wire/recovery/OMS 前缀，再将各市场交给
原同一 `_load_context_unpartitioned` 重建。旧 context 的初始权益、损失、峰值、
容量事实不变。没有黄金历史时返回 None，不构造新的空账本；首个黄金观察必须
全账户空仓且 USDT 钱包等于封存的独立金额。已有黄金历史时走原 `validate_origin`。
其他 collateral 仍须自由平坦且与封存 inventory 完全一致；新增资产、外部变化
或不归属的仓位拒绝。不得将 USDC 折算成 USDT 或过滤旧历史。

## 原入口与五个后续消费者

原提案、账户风险输入及 pilot 登记准备调用同一 resolver；内部 proof.state
加入分区摘要并参与原摘要绑定，不改变 console/frontend payload。原 prepare
再次核验。实际独立 issuer 在签名前及交付前各复读自身镜像、共同完成记录、
新授权与账户观察；实际发送器在取凭据前和取凭据后沿原 preflight 再核验。
前半段拒绝不产生发送，后半段失败保留原 UNKNOWN 处理，禁止盲目重发。

原跨日核对、账户现金、functional feedback、gold strategy feedback 与 gold bar
feedback 原来将全 reservation 与单市场 context.sources 直接 zip。混合历史中，
首个黄金来源因此错误配到旧 BTC reservation。五处改为按原 source 的 reservation
摘要配对、检查唯一性和顺序；较短来源列表必须先重新读取保护分区并重建整个
book，不能借此接受未安装账户的历史过滤。现金守恒、反馈身份与原编号校验保留。

## 回归与限制

保留缺接口、安装最后记录缺失、夹具使用错误、来源错配等原始失败日志。
新增回归覆盖两角色镜像、缺完成记录、源码/旧 registry 改变、新 key、全旧前缀
篡改、初始黄金钱包改变、未归属仓位、签发与发送间记录变化及混合来源配对。
旧 spent client ID 拒绝保持；测试使用新的黄金 client ID，不放宽原去重门。

混合 receipt 回归沿原 reported recovery、OMS 投影和 V2 账本得到两份独立 context；
黄金平仓含合成手动事件并保留 gap，不作为自动策略平仓或收益证据。跨日工具
验证到公开采集回调的正确黄金 proof/order 选择为止，未完成实际跨日采集或
24 小时运行。已有 held/day 回归通过不替代新市场真实持仓长跑验收。

截至本增量检查，前版 `df0c3f5f0f5b6f620c9ef6418a9c06a8cb0e80a4` 自身
canonical ALL 为 32/32 PASS、失败/错误/超时 0、保留 21 skip，证据留存 exit 0。
分区候选 `9b8124dc20ce64fa3a09701349ed8b9fad46d8a1` 的实际验收已终结
INCOMPLETE：冷构建 A 获取固定公共基础镜像的 IPv6 下载连接被重置，canonical
尚未开始。原失败与材料不改写，不继承 df0 的 PASS。

本增量自身完整验收、独立黄金登记/shared-budget 批准、停机切分链来源认证、
部分安装恢复交接、未知账户事件诊断、现场安装/升级/回滚、实际连续运行与
成交/现金/再研究反馈仍待完成。V1 历史失效声明、V2 TECHNICAL_VALIDATION_ONLY、
候选经济晋升拒绝和原实盘授权边界不变；盈利策略及多资产范围分别评价。
