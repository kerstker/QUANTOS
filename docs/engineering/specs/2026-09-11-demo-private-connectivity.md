# Demo 凭证安装与真实私有连接自动检查

2026-09-11。用户明确提供 Demo 凭证并要求写入 DGX 的 key 目录、自动跑通 Demo 测试。
这次授权覆盖凭证安装和 Demo 联调；不会将其转换为主网授权、经济准入或绕过既有订单门。
开发起点 `7136c383c`，分支 `codex/demo-integration-20260911`。

## 已执行与实际结果

- 本机确认为用户指定的 DGX：192.168.5.68、ARM64。凭证直接写入开发目录
  `key/binance_demo/credentials.json`，目录0700、文件0600、Git忽略；原附件权限收紧至0600。
  没有覆盖其他配置，没有把内容输出到终端、报告、浏览器或版本库。
- 首次既有 readiness 工具真实公共与签名账户 GET 成功。
- 新自动检查实际完成：公共时钟和两合约规则、签名账户、BTCUSDT/BTCUSDC各自挂单及
  前15分钟成交 GET、创建或复用 listen key、两次私有 WebSocket 连接和两次 ping/pong。
- 本次真实自动检查耗时18.177秒，8次REST调用、2次WebSocket连接；两种计价的挂单/近期成交均0条，
  观察到0个私有事件。只证明账户与私有连接检查通过；没有证明事件投递、成交补拉或现金对账。
- 为前端与最终契约分别复核两次，耗时35.106秒和24.889秒，两次连接检查均通过；全部尝试分别保留。
  账户实际有14个非零持仓，其中BTCUSDT为0.0097 BTC。现有仓位未改动；空仓起始的有限V2回放不能
  直接承担这个账户的现金权威。后续需初始账户状态与本次订单归属隔离，不能把没有近期成交当作空仓。
- **订单提交仍为BLOCKED**：`services/validation/live_submission_authority.py` 的普通提交校验仍拒绝；
  `tools/testnet_execution_service.py::build_runner` 在读取凭证/创建数据库/构造适配器前调用它。
  本次没有订单POST、撤单、修改杠杆或保证金模式，没有构造HUMAN、签发自有权威或重置kill-switch。

用户已经授权Demo测试；这个阻断属于未完成的代码集成，不能归咎于用户没有再确认一次。
完整“目标→风控→提交→真实成交回执→恢复→唯一账本”仍未验收，本记录不称其全部通过。

## 实现与边界

`tools/binance_demo_private_check.py` 默认零网络、零凭证读取。
显式 `--operator-check` 才读取固定Demo环境的文件，检查文件类型、所有者、权限、大小、重复字段和内容。
不执行附件内容，不source环境文件，不回落到主网配置；子进程不继承其他交易凭证或Python注入配置。

REST固定官方Demo主机与有限路径，只允许5种GET以及listenKey的POST，拒绝重定向。
不提供任意URL、订单、DELETE、账户配置或转账入口。
单响应最多2MB、socket10秒、整个子进程120秒；失败停止后续请求，无自动重试。
近期成交最多100行，达到页上限则报告不完整；这是连接检查，不是全历史或逐笔账本补拉器。
错误只保留固定原因码。私有WebSocket日志隔离，不持久化订阅令牌、签名URL、请求头或原始私有帧。
事件计数只说明收到事件，不代表成交归属、费用或订单生命周期验证。

两次连接顺序打开、ping/pong、有限观察、关闭，第二次验证主动重连。
最多40秒；每次观察3秒、最多100事件。没有自动故障循环或常驻服务。
listenKey可能由同账户其他连接共享，因此只关闭本次WebSocket连接，不DELETE账户订阅。
未持续续期；服务端按自身规则到期。

每次最终结果按内容摘要独立保存，latest原子更新；读取验证摘要与严格契约。
整批超时保留worker失败结果，尚不保证保存被终止子进程的每个中间步骤。
来源摘要标识此检查的工具/合同/存储文件，不是外部来源认证或完整发布身份。
Console只读结果，新增`private_checks`；TypeScript、API契约、页面同步。
页面分别展示连接次数、心跳、事件计数、失败原因、程序摘要和历史状态。
120秒后标为历史；不会将过期检查、没有事件、没有订单或没有账本当作成功的成交闭环。

## 本机复现

```bash
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python \
  -m tools.binance_demo_private_check --operator-check \
  --credential-file /home/kerstker/QUANTOS-backtest-runtime-integration/key/binance_demo/credentials.json \
  --output-dir /home/kerstker/quantos-dev-output/demo-private-integration-20260911/state/data/cache/binance_demo_private_checks
```

命令不含密钥值。退出0仅代表账户与私有连接全部检查通过，订单提交/现金对账仍分别为BLOCKED/UNKNOWN。
默认不加`--operator-check`返回未请求状态和退出2。
Console绑定对应隔离state根；没有将凭证装入Console/AI/Runner或切换正式服务。

## 验证与后续

先复现缺失自动检查及Console字段，再开发。补充回归复现“未请求却携事件数”和状态/原因矛盾，
随后收紧前后端契约。最后后端专项161通过/1跳过；提交入口/适配器补充76通过（与前者重叠）；
前端全量378通过，最后相关14通过，类型/ESLint/构建通过。
真实Console→浏览器的桌面、390px手机、暗色均无页面错误、横向页面溢出或WCAG 2 A/AA扫描违规；
0浏览器写请求、0浏览器直连场所请求。补修手机步骤表，使结果列不必横向滚动才能看到。
首次浏览器启动因默认缓存位置缺失失败，改用已有匹配版本缓存，未下载或升级依赖；失败历史保留。
任务输出：`/home/kerstker/quantos-dev-output/demo-private-integration-20260911/`。
暂存差异与39份当时输出的真实凭证匹配扫描均通过；未将此前完整28/28或ARM64发布验收转移到本批。
最终手机步骤表复核仍无溢出或无障碍违规，三列均可见；历史状态使用真实旧观察验证，没有重写观察时钟。
仓库卫生、服务依赖图、治理扫描及实际pre-commit hook通过。
临时回环Console16840/前端16911已停止并检查端口关闭；没有安装或切换正式常驻服务。

下一步仍须完成现有提交校验的真实独立集成及现有人工限额输入，再以固定有限订单计划验证
真实Demo提交、撤单、回执漏收补拉、OMS收敛和同账户现金对账。
私有连接通过不能替代上述条件，也不产生主网盈利结论。

## 官方接口依据

- [Binance General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)：
  当前Demo REST与WebSocket主机；2026-09-11核查并实际连通。
- [Binance User Data Streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)：
  私有连接路径、会话有效期与共享行为。
- [Binance Listen Key REST](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/user-data-streams)：
  POST建立或复用会话；没有删除共享会话。
