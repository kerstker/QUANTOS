# 真实加密数据接入:CoinGecko BTC(只读)+ 多资产就绪

**日期:** 2026-06-25
**状态:** 已确认方向(前三轮)+ ultracode → 实现
**所在层:** `services/data/`(离线摄取接缝)+ `tools/`(门禁外只读抓取)
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究回测;只读数据,绝无交易/券商/凭证写入。

## 目标

接入真实 BTC 行情(CoinGecko,纯数据机构、无下单能力),在真实加密信号上跑回测;架构做成
多资产就绪(crypto/equity/... 用同一摄取接缝),为后续接股票/期权数据铺路。

## 安全边界(不可破)

- 只读数据:CoinGecko 无任何下单/账户能力;只 GET 公开行情。绝不引入交易所/券商 SDK。
- stdlib only:用 urllib 打 REST,不引第三方 SDK(避免依赖 + 双用途下单面)。
- 密钥:只读数据 key;运行时从 COINGECKO_API_KEY 环境变量或 key/coinkey.txt(已 gitignore)
  读取;绝不提交/打印/硬编码。
- 网络隔离:网络只发生在 tools/ 的门禁外抓取;研究/validate.py/测试全离线确定性
  (测试注入桩 http_get,绝不真连网)。
- L2 边界一字不改;无实盘、无 arming flag。

## API(CoinGecko v3 Demo)

- Base https://api.coingecko.com/api/v3;鉴权头 x-cg-demo-api-key: <KEY>。
- OHLC:GET /coins/{id}/ohlc?vs_currency=usd&days={N} → [[ts_ms, open, high, low, close], ...]
  (无 volume;Bar.volume 默认 0、ge=0,合约有效)。BTC id = bitcoin。
- 颗粒度(按 days 自动):days=1 → 30 分钟(最小);7/14/30 → 4 小时;90/180/365 → 4 天。
  起步用 days=1 取 30 分钟(最小颗粒度)。
- 真实抓取时经验验证端点/颗粒度/鉴权(网络层若挡文档,以实测为准)。

## 时间编码(多资产/intraday 通用)

- event_time = ts_ms(epoch 毫秒;CoinGecko 原生、单调、支持 intraday;panel_builder 按位置取前向 → 任意单调整数皆可)。
- knowledge_time = ts_ms + interval_ms(bar 在其收盘才可知;PIT 正确,kt>=et 成立)。
- 说明:一个 store/研究运行内时间标度需一致(crypto 用 ms;日线股票 CSV 仍可用 YYYYMMDD;勿混用)。
  crypto 24/7 → 无交易日历假日问题。

## 架构

### services/data/market_provider.py(离线、多资产就绪接缝;无网络/密钥)
- MarketDataProvider(Protocol):asset_class: str;fetch_ohlc(instrument_id, *, days, vs_currency) -> list[dict]
  返回规范行 {instrument_id, event_time, knowledge_time, open, high, low, close, volume}。任何 provider
  (CoinGecko crypto、未来 equity)实现它 → 系统多资产就绪。
- ingest_provider_rows(rows, store, *, now=None, dataset_id="market", version="") → 复用
  file_ingest._ingest_records 失败关闭核心(normalize_bar 校验 OHLC/正价/bitemporal、去重、hash、quality)。
  资产类无关:行来自任何 provider。

### tools/coingecko_fetch.py(门禁外;网络+密钥只在此)
- _read_key():env 优先,回退 key/coinkey.txt;缺失抛错;永不打印。
- _http_get_json(url, headers):唯一网络调用(urllib GET,timeout)。
- fetch_ohlc(coin_id="bitcoin", *, vs_currency="usd", days=1, http_get=_http_get_json):发 demo 头;
  http_get 可注入(测试传桩 → 全离线)。
- parse_ohlc(payload, *, instrument_id, interval_ms)(纯函数):[ts,o,h,l,c] → 规范行
  (event_time=ts,kt=ts+interval,volume=0)。可离线测试。
- fetch_btc_rows(*, days=1, ...) + main():CLI 手动抓取 → ingest_provider_rows 入
  data/cache/crypto.db(gitignored)。

## 测试矩阵(tests/data/test_market_provider.py,全离线)

1. ingest_provider_rows:规范行 → store 入库;bars_ingested 正确;OHLC 矛盾行进 rejected(失败关闭)。
2. parse_ohlc:静态样本 payload → 规范行(event_time=ts、kt=ts+interval、volume 0)数值正确。
3. fetch_ohlc 注入桩 http_get(无网络)+ env COINGECKO_API_KEY="TESTKEY":断言请求带
   x-cg-demo-api-key 头且非空、URL 含 coin/days;桩验证 key 被用但测试不含真 key。
4. _read_key:env 命中返回(strip);缺失抛错。
5. 端到端(离线):桩 payload → parse → ingest → store.as_of 取回 → 经研究 provider 可构建周期。
6. 确定性:同 payload 两次 ingest 幂等(content_hash 不变)。

## 真实验证(手动、门禁外)
跑一次 python -m tools.coingecko_fetch 1 用真 key 抓 BTC 30 分钟线 → 入本地 store → 在真实 BTC
数据上跑研究流水线一遍(证明端到端)。数据不入库 git。

## 涉及文件
- 新建 services/data/market_provider.py、tools/__init__.py、tools/coingecko_fetch.py、
  tests/data/test_market_provider.py。
- 修改 .gitignore(已加 key/)。
- 复用不动:file_ingest._ingest_records、SqlitePITStore、研究流水线。

## 安全审查(将由 Workflow 多 agent 对抗验证)
维度:无券商/交易 SDK;无下单/账户能力;密钥不泄漏/不提交/不打印;网络仅门禁外、研究核心全离线确定性;
PIT 无前瞻(kt=收盘);多资产接缝设计稳健;治理扫描仍绿。
