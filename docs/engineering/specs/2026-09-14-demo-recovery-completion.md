# Demo 恢复交付：固定账户采集、V2 算术核对与备份修复

2026-09-14。承接[历史回执恢复与首次安装](2026-09-14-demo-historical-recovery.md)。
本记录区分软件验收、实际数据核验和自动交易周期，不将人工平仓改记成自动完成。

## 冻结源码与交付范围

源码 `1010858ab5df6a3e5495cb7c287b2e6e66e09bb5` 包含逐笔 REST/私有回执/费用比较、
固定执行账户 statement 父子进程及历史来源兼容、两个会话结果目录的备份修复。
网页仍为此前验收安装的334版本，Console/runner为8d61；本次备份安装不切换它们。

任务材料目录：`/home/kerstker/quantos-dev-output/demo-recovery-completion-20260914`。
完整验收在独立101源码工作树运行，23:20最终**28/28 PASS**，两轮ARM64构建、扫描与
断网启动均通过。后端8302通过/19跳过，执行与工具4276通过/2跳过，前端447通过，
Phase13专项26通过；类型检查、静态检查和生产构建通过。全流程约68分43秒，
其中canonical约45分58秒；不将测试通过视为交易或盈利验收。

原实际pre-commit检查通过后，证据版本固定为
`50071224ee29422e9576450c0c451bc54169dbe2`，清单摘要
`8a4122ace0a3364cc2dcef197e1ba2c01eef0771c3556660a14d618025fe2b14`。
`result.json`、`canonical-report.json`、`accepted-evidence-precommit.log`和
`accepted-checkout-1010858ab-binding.json`保留完整结果。开发分支与用户的四份未跟踪文档保留。

## 原账户正式采集入口的实际验证

冻结101源码归档SHA256为`ce7bff73d75d0d18b2b687b044f74b4e55dcb3707dbc41c0c1ba4353f6b7abaa`。
归档经校验后置于root所有的隔离诊断目录，调用原执行UID1001的固定账户采集父进程。
本次不是安装驻留服务，也没有读出或更换凭证。

父进程通过原凭证边界完成六类GET，40.458秒内取得原窗口的2笔BTCUSDC成交，
并核对账户引用、子进程来源、窗口、时间及正文摘要。正式结果进入原受保护范围
`receipt-reconciliation/statements/26b4029c31310084170788c839e41a5b6bb0db35334996c79704ae1bd09753e3.json`。
原dispatch快照前后相同，普通下单次数未增加。详见`fixed-parent-result.json`和
`fixed-parent-statement.json`；本次验证覆盖父进程和真实留存，不只覆盖capture-worker。

实际statement经现行验收版本的原发布函数写入研究流水缓存，旧报告字节保留，
旧latest指针另存。新报告摘要为`6b4a0ae7bc493e71165a0e1c1c9d6ea0fe50a4c555a82552885a8fbff4ed8bb9`。
网页API返回BTCUSDC、2笔成交，现金核对UNKNOWN、策略P&L为空；观察过期仍显示过期，
没有改写时间来模拟实时采集。发布和API检查见任务目录中的`statement-publication.json`
及`api-observation-before-install.json`。

## 唯一V2计算引擎的实际交易算术诊断

隔离脚本`diagnose_original_cash.py`使用原提案的合约、保证金与容量输入，逐笔核对
原私有成交、上述固定账户REST成交和COMMISSION。两单都保留原拒收缺口；人工平仓的
reduceOnly字段来自原私有消息，没有构造新的交易意图。

在现有`LinearPerpetualLedger`内重放两笔已见成交，未另外实现现金算法，结果为：

| 项目 | USDC |
| --- | ---: |
| 原观察起始钱包 | 5000 |
| 报告已实现价格盈亏 | -0.3528 |
| 两笔手续费 | 0.14008463 |
| 扣成交费后变化 | -0.49288463 |
| V2算术重放钱包 | 4999.50711537 |
| 与窗口内对应收入流水的算术差额 | 0 |

重放末持仓为0。费用只扣一次，其他USDT交易没有计入本轮USDC。
`original-cash-math-diagnostic.json`保留输入摘要、合约投影、V2状态、逐笔比较及原GAP。
这是现有引擎对已见输入的算术诊断：`canonical_book_reconciled=false`、
`automatic_cycle_completed=false`、`coverage_verified=false`。没有修改OMS、原现金账本或恢复状态。

## 真实执行目录的隔离加密恢复

新目录声明先前已完成2个失败复现及联合88项测试。本次在冻结101源码上，以原执行身份
实际捕获**5459个文件**，含原`session-results/`及新`receipt-reconciliation/statements/`。
捕获后由单独的离线工作者使用既有备份凭证，加密并完整隔离恢复，最终
VERIFIED_ENCRYPTED_NAMESPACE_CHECKPOINT，5459文件和全部状态与捕获清单一致。

恢复验证保留9339条私有流记录、195条wire记录、1个原预留、1个待恢复订单、0个OMS成交，
停机记录摘要仍为`e03b735c0344a636d7b39868511996d7493da1452b09200d226aa23233ac9cb5`。
没有将恢复结果覆盖回执行目录。原先的失败捕获结果没有删除。

捕获摘要`ae67eb8ea760e55912869cc0425425d4e92a385a931238620a2c527761f4a141`，
加密归档摘要`24373c7150bf6a42a7a08418bd44e7d414c690542f13b18816ba2445d0923398`；
归档位于`/mnt/quantos-backup/demo-recovery-diagnostic-1010858ab/checkpoint.enc`。
证据为`diagnostic-backup-result.json`、`diagnostic-checkpoint-manifest.json`及捕获/封装日志。
此隔离验证不等于现行定时器已修好；必须再检查正式安装后的捕获和加密恢复结果。

## 首次测试之后新增的两类业务备份

22:56实际API仍只有研究和执行两类定时备份；签发和工作流两类为NOT_INSTALLED/NO_SPOOL。
最初安装备份时这两个业务目录尚不存在，此次首次登记后已产生真实记录，应补齐覆盖。
实际预检确认签发目录由UID1002保管，2个文件、2条签发记录；工作流目录由root保管，
21个文件，任务、请求和各自claim均各1条。原目录清单、库与claim核验通过，
见`additional-backup-preflight.json`。

复用现有已冻结的`render_demo_checkpoint_units`和`demo_checkpoint_cycle`，为这两类准备
各自捕获、加密完整隔离恢复与定时器；不创建替代业务库，不复制签名凭证，不改原政策。
沿用捕获每12小时、待封装检查每15分钟、恢复点目标24小时。

23:22安装这两类的8个新单元，启用4个定时器；23:23首次自然捕获成功，23:25自然封装
及完整隔离恢复成功。签发恢复保留2条完整签发链记录，工作流21文件及任务/请求/claim
均一致。验证直接读取D-Bus的微秒触发时钟与实际服务启动时钟，确认是定时触发。
辅助核验程序最初把systemctl的人类可读时长当整数，修正为读取D-Bus数值后通过；
该辅助程序错误没有改变工作者结果。SQLite在线快照比较完整验证链，JSON逐字节比较，
不要求在线SQLite副本的物理文件头与原库相同。

证据为`additional-backups/manifest.json`、`additional-backups-verification.json`与root安装记录。
原业务文件摘要一致，没有重建签发库、领取记录或原周期；四类网页状态仍待最后发布核对。

## 执行备份实际安装与并发失败

23:21将执行捕获和封装的两个原单元切换到500版本，Python依赖与原锁文件一致，
对应发行源码和绑定检查通过。原两个定时器定义保持，安装期间暂停后已恢复；
五个执行库inode、受保护私有流进程与原政策材料保持。研究备份源码和发布监控没有切换。
systemd重载会重算现有OnActiveSec，额外触发不能记作又完成一个12小时自然周期。

23:23第一次自然执行捕获、随后一次重试均因CHECKPOINT_SOURCE_CHANGED失败，旧失败和
两次新失败全部留存。独立12秒清单比较定位到原编号查询新增`evidence/queries/`和
`resident-recovery/attempts/`文件；没有缺失或删除原记录，也不是目录声明修复退回失败。
因此安排有界、查询和观察发布均已完成后的捕获，期间不暂停查询者或私有流，
不放松一致性规则。第一次60秒观察没有合适窗口，未触发捕获；第二次有界观察在
查询结束约1.97秒、观察发布结束约0.42秒时触发原捕获服务。

23:32捕获**5800文件**成功，23:35加密及完整隔离恢复通过。恢复保留9580条私有流记录、
244条wire记录、1个原预留、1个待恢复订单、0个OMS成交、原停机记录。
`session-results/`和固定账户statement的原文件均进入正式归档并逐字节核对。
周期`1789399949267-8ddca5139947`，密文摘要
`54f01667b49e18c2cc1e18e05e831bfe6bd6bdb7f43d92734a61cc3f9633a985`。
实际执行备份CURRENT，7个已验证恢复点、0待封装、4次历史捕获失败；其中2次是本轮并发失败。

`installed-backup-verification.json`同时核对原4份政策/安装/周期材料摘要、5个库inode、
原私有流进程、原备份结果和定时器定义均保留；完整dispatch链只有原1条SEND_CLAIM。
六个用户侧网页/后台/研究进程核验相同，见`user-process-postinstall.json`。
本次人工选择空闲窗口后完成快照，不代表无人值守捕获的并发问题已经消除。

## 正式网页检查

桌面1440px与手机390px实际HTTP检查通过：四类恢复点和四类自动调度均显示**4/4**，
执行、签发、工作流的恢复时间与新归档记录相同，原捕获失败仍在明细中显示。
页面无横向溢出、脚本异常或写请求；见`installed-backup-browser/result.json`及同目录截图。
首次页面检查时研究侧新快照仍在封装，页面正确显示1份待封装，没有提前显示为0。
原周期仍RECOVERY_REQUIRED，正式现金核对UNKNOWN、策略收益为空；备份成功没有改变这些结论。

23:38研究侧23466文件的新快照完成加密和完整隔离恢复；周期
`1789399387618-f2d5a86a847a`，密文摘要
`4c6e4b573d96cbed90b3908939495b306292e1a705884a549d95caec44b4e3dc`。
最终再次发布并核对：四类恢复点均CURRENT，全部待封装为0；研究/执行/签发/工作流
分别有9/7/1/1个已验证恢复点，执行侧4次历史捕获失败保留。
`final-backup-status.json`与`installed-backup-browser-final/result.json`保存最终状态，
桌面与手机再次核对通过。这是安装后实测，不是多个12小时周期的长期稳定性结论。

文档本地链接14项、仓库整洁检查均通过；提交前另运行实际pre-commit检查。

## 自动周期验收边界

原自动开仓1203137495与人工平仓1203150289的已见成交和费用对应，账户已人工归零。
原拒收消息在旧采集代码中只留SHA256，缺少原文，精确拒收原因仍不能确定。
币安[用户数据流文档](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)
不能代替本次缺失消息。原周期继续RECOVERY_REQUIRED，订单与正式现金恢复尚未完成。
本轮没有重签过期的一小时政策、重置停机状态、重新开仓，或将人工恢复称为自动闭环。
