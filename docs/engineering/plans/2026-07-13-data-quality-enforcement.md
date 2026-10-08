# 2026-07-13 承重数据质量与前向证据收口

状态:代码与上一轮完整门禁已完成;真实四源刷新、内容寻址 raw archive 与可复算准入 manifest 已落地。
perp mark price / OI 的质量与最近 8 日 lineage 已通过,但 Binance funding 公共端点外部超时,
故 quality / lineage / 总体三者仍 `BLOCK`。

## 问题

此前 `strict=True` 仍存在四个承重漏洞:质量检查发生在 SQLite 写入之后；解析拒绝行和空批不算失败；
completeness 没有显式 cadence/边界；前向兑现只看价格覆盖率,不知道本轮接入失败。`--no-refresh`
更把“没有运行质量检查”表示成空失败集合,可继续记录或兑现证据。

首次 funding 缓存还有独立风险:`fetch_daily_funding` 静默跳过畸形观测；`crypto_alpha_search`
把抓取异常吞成空序列并直接覆盖共享 JSON,可能发布半份缓存。

## 已实现

- `build_ingestion_report` 按 instrument 检查 rejected/empty/duplicate/order/future/cadence/boundary。
- strict 接入先验证、后写入；`SqlitePITStore.add_batch_atomic` 单事务提交或整批回滚。
- 日/1h/1m/crypto-funding 四个承重驱动器由 AST 测试强制 `strict=True + expected_interval`。
- funding 原始观测拒绝畸形、非有限、未来、重复/乱序记录；日缓存要求有限、UTC 日网格连续且不越界。
- `tools.cache_io.atomic_write_json` 使用同目录临时文件、fsync、`os.replace`,序列化失败保留旧文件。
- `realize_due` 按 `blocked_instruments` 拦截受影响 pending；无 `quality_evidence_current` 时全部留 pending。
- 刷新报告结构化暴露 `failed_tickers`;`--no-refresh` 显式阻断 record 与 realize。
- 四源刷新新增 USD-M 永续 mark-price 日归档；前向兑现不再用现货 close 代理永续合约价格，
  `forward_price_basis=BINANCE_USDM_PERP_MARK_PRICE`。
- funding 刷新以最多 8 个 worker 有界并发，仍逐 ticker 保留确定性合并和
  `TRANSIENT` / `NOT_FOUND` / `QUALITY` / `SOURCE_ERROR` 分类；OI 的 404 与瞬时失败也已分型。
- mark 质量门检查最近 8 个已收盘 UTC 日，默认尝试保留 16 日定价覆盖，以容纳“最多 8 日旧决策
  + 下一周兑现”；已有两条 pending 决策的 14/14 持仓起始 mark 已补齐。
- `tools/data_quality_admission.py` 固定 24 币前向宇宙，记录每个输入制品 SHA-256、逐 ticker
  质量结果、刷新观察、价格口径和总 verdict，产物为 `evidence/data_quality_admission.json`。
- `services/data/raw_archive.py` 将响应 body 按 SHA-256 存为不可变对象，并为每次观察保存独立
  content-addressed sidecar(source URI/provider/instrument/observed_at/representation/解析计数)；并发同对象
  去重，敏感 query key 拒绝，object/sidecar 篡改与重哈希后的非法 schema 均使汇总 `BLOCK`。
- mark/OI 保存精确 `HTTP_BODY_BYTES`；JSON 解码型 spot/funding 明示为 `CANONICAL_JSON_UTF8`，
  不冒充 wire bytes。daily/1h/1m、四源刷新和批量 funding 研究入口均由测试/AST 钉死 archive 接线。
- `tools/raw_data_lineage_backfill.py` 只读重取最近窗口并逐 ticker/day 与 canonical cache 对账，产物
  `evidence/raw_data_lineage_backfill.json`；不一致、缺 canonical、缺 refetch 均 fail-closed。网络 transient
  可从已验证 ACCEPTED observation 重放并保留 warning；evidence 绑定三份当前 cache SHA、scope、8 日窗口
  与 archive aggregate，cache 字节变化即失效。
- data admission 升级为 schema v2 的 `quality_verdict + lineage_verdict` 双门；前向驱动器也要求 archive
  integrity PASS、mark/funding/OI 三 dataset 齐备且 backfill evidence 当前/匹配，否则证据钟继续暂停。

## 当前制品验收

2026-07-13 对本地缓存执行真实刷新后只读检查:

- `crypto_perp_mark_prices.json`:前向 24/24 ticker 最近质量窗口通过，最新已收盘日为
  `1783728000000`;各 ticker 实存 15 日。Data Vision 对全宇宙同一旧日归档返回 NOT_FOUND，
  但两条现有 pending 决策的起始日与后续日均 14/14 覆盖。
- `crypto_oi_metrics.json`:默认 24/24 ticker 最近 8 日连续性、字段有限正值和 5 日新鲜度通过；
  刷新中的 5 条归档 `NOT_FOUND` 未进入 `failed_tickers`，没有伪装成 transient 或质量通过。
- `crypto_funding_rates.json`:24/24 前向 ticker 均因尾部 completeness 失败而阻断。整份 115 ticker
  刷新均返回结构化 `URLError / WinError 10060 / TRANSIENT`，未写入半份缓存，也未用其它 venue
  静默替代 Binance funding。
- 非前向定价用的现货参考库刷新仍将历史 XMR 缺档标为 `QUALITY`；它不改变 perp mark 准入口径。
- `evidence/raw_data_lineage_backfill.json`:mark 192/192(本轮 192 refetch)、OI 192/192(112 refetch +
  80 verified archive replay；80 个 vendor transient 保留为 warning)与 canonical cache 完全一致；三轮真实
  观察后 archive 共 384 unique objects / 1065 observations、0 invalid，aggregate SHA-256 为
  `86aff230c6fe46fe1da664288e056a01ac8b3763df2cddf140b6ac14a71955e7`。funding 0/192：
  24 条 `TRANSIENT`、96 条 canonical 尾部缺失、96 条 refetch 缺失，且没有 funding observation。
- `evidence/data_quality_admission.json` schema v2:价格 PASS、OI PASS、funding BLOCK；raw archive integrity
  PASS 且 cache SHA/scope/days/archive aggregate 均绑定；但 required lineage dataset 与 backfill source 均缺
  funding，故 `quality_verdict=BLOCK`、`lineage_verdict=BLOCK`。
- `forward_track_advance --no-refresh` 对抗运行前后，`crowd_contra` / `carry_oi_majors` 均保持
  1 条 pending、0 条 realized、0 个新增 forward period；两条均记录 `n_quality_blocked_this_run=1`。

结论:价格经济口径、OI 以及两者最近 8 日 raw lineage 已准入，证据钟仍因 funding 单一外部阻断而暂停。
不得把两源通过、archive integrity PASS 或代码绿等同于三源 lineage 已齐备。

## 刷新运行可靠性增量

- 四源各自在独立 `spawn` 子进程运行；父进程执行 prices/funding 15 分钟、mark 20 分钟、OI 与全局
  30 分钟绝对 deadline。超时后 `terminate + join`，结构化 `BUDGET_EXCEEDED` 并把该源完整 ticker
  宇宙加入 `failed_tickers`，前向门因此 fail-closed。
- `services/data/refresh_history.py` 以 SQLite WAL/FULL、禁止 UPDATE/DELETE trigger、canonical status
  hash 与 `prev_hash -> entry_hash` 保存每轮状态。重启后重算主状态及规范化 source 行；任一篡改都会
  使链失效并拒绝追加。
- SLO 同时衡量 30 轮 run-window、99% instrument coverage、连续 3 次失败、最新水位与全局耗时。
  survivorship-free 历史 XMR 的单点缺口仍被明确阻断，但 114/115=99.13% 不会把整个价格源误报为
  0% 可用；缓存质量通过时尚未发布的 `NOT_FOUND` 归档只记 warning。
- 两轮真实运行均约 635.5 秒；最新轮 prices coverage=99.13%，mark/OI=100%，funding=0%、连续失败
  2 次且水位 5 天。链 2/2 完整，`evidence/data_refresh_slo.json` 诚实给出 `BREACH/BLOCK`。
- 这只证明机制与两次真实运行，不证明长期运营。至少积累 30 轮真实调度、告警送达/值班响应记录，
  并把链头锚定到外部 WORM/对象锁后，才可讨论关闭该证据缺口。

## 剩余工作

1. 恢复 Binance funding 公共端点可达性后重跑同一准入 manifest；若引入备用 venue，必须先建立
   明确的合约映射、时间语义、差异对账和 provenance，不能静默填充现有 Binance 序列。
2. funding 恢复后补齐最近窗口 raw observation + 对账；再排期历史 mark/OI/funding 与 spot/1h/1m
   旧缓存 backfill。当前本地 content-addressed archive 尚无外部 WORM/对象锁、异地副本、保留/删除策略，
   JSON 源保存的是 canonical decoded response 而非 wire bytes，不能虚报完整机构级历史档案。
3. 持续运行现有四源 SLO 至至少 30 轮，补齐真实告警送达、值班响应与恢复证据；将本地链头定期锚定
   到外部 WORM/对象锁。当前只有 2 轮且 funding `BREACH`，不能把代码机制冒充长期 SLO 达标。
4. 把同一证据契约扩到实际承重的 FX/宏观入口；exploratory 驱动器保持诚实标注,不一刀切冒充生产。
5. P2-2 已于 2026-07-13 根因闭合：数据库是统一 quote USD volume，错误是 scenario 容量二次乘价；
   显式 `VolumeUnit/VolumeSemantics` 与 quote-notional helper 已接线。旧制品逐请求 raw lineage 仍归本计划。

## 验收命令

```bash
python -m pytest -q tests/data tests/research/test_forward_track.py \
  tests/research/test_forward_track_gate.py tests/research/test_live_data_refresh.py
python apps/research-console/scripts/run_paper_to_live_governance_check.py
python scripts/validate.py
```

## 验收结果

- 本增量定向回归 `53 passed`；扩大 data/forward 回归 `250 passed`。
- 治理扫描:`246 files / 0 violations`。
- 完整门禁:`2080 passed, 1 skipped`;独立 live/trading/tools:`856 passed`；前端 typecheck、
  `68 passed`、production build 全绿。
- 顶级能力机器结论保持 `NOT_TOP_TIER`, `16/22 PASS`,没有因本轮代码修复虚增成熟度。
