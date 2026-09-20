"""MEXC 现货(spot)签名 REST 客户端——零手续费 BTC 现货 venue(MEXC 集成第一阶段)。
设计与既有可能复用的模板:`live/exchange/binance_futures.py`(USDT-M 合约适配器)。

纯 stdlib(urllib/hmac/hashlib/json/time)——绝不 import 交易所 SDK(ccxt/python-mexc 等)。
`http_request` 可注入(测试用假 transport,零真网络;生产默认走内置 urllib 实现)。
MEXC Spot API v3 与 Binance 高度同构:HMAC-SHA256 查询串签名、X-MEXC-APIKEY 头、
timestamp+recvWindow 参数、错误 JSON `{"code":..., "msg":...}`。

**现货语义 vs 合约语义(诚实边界,不可混淆):**
- 现货**没有做空、没有仓位(position)概念**——只有各币种的自由/冻结余额。`positions()`
  把"非零自由余额"折算成 {asset: VenuePosition(quantity=free>0, entry_price=0.0)}:
  BTC 余额即多仓,quantity 恒非负;**没有入场价概念**,entry_price 诚实置 0.0(账户快照
  无法恢复成本基,硬编会造假)。调用方(reconcile_positions)据此只能对数量,不能对成本。
- **没有 reduce_only 交易所原生参数**。`LiveOrderRequest.reduce_only=True`(紧急减仓通道)
  映射为现货语义:仅允许 SELL,且数量 ≤ 该币种当前自由余额——超出即本地拒绝(不提交),
  宁可误拒也不把减仓单变成开新仓。
- **没有 testnet**。MEXC 不提供现货沙盒——毕业路径只能是"干跑/paper → 人工 mandate 后的
  极小真额"。因此本适配器没有 TESTNET_BASE;唯一合法 base_url 是 MAINNET_BASE。
  **mainnet 在默认 SafetyConfig 下构造即 raise SafetyViolation(L2 结构性拒绝)**,
  与 `services/trading/broker_adapter.py::LiveBrokerAdapter` 的接缝拒绝对称——真额联调必须
  先过 Phase 3A 人工 gate(显式全部 live 前置打开)。

**绝不提供提币/划转能力**——治理扫描器钉死(提币/划转类调用字面量一律 banned);
本模块连这些方法名都不出现。

运维硬化(与 binance_futures 同法):HTTP 429 经 `live/exchange/retry.py::call_with_backoff`
指数退避重试,耗尽仍限流才 `RateLimitedError`;签名串(含 timestamp)**每次重试重新算**
(退避可能跨越数秒,复用旧 timestamp 有落在 recvWindow 之外的风险)。`sync_clock()` 经
/api/v3/time 拉取服务器时间、计算本机偏移(ms)修正签名 timestamp——fail-soft(查询失败
保留旧偏移,不打断流程),默认偏移为 0。

**时钟同步失败可见性(2026-09-20,镜像 `bybit_futures.py` 的 2026-07-09 修复):** 此前
`sync_clock()` 的失败被裸 `except Exception: pass` 完全吞掉,失败现场不留下任何记录。现在结果
记进公开只读属性 `last_clock_sync_ok`/`last_clock_sync_error`(调用方按既有 `getattr` 惯例探测,
`tools/live_risk_sentinel.py::log_clock_sync_status` 已按该属性写告警台账),失败原因经
`_try_sync_clock_once()` 返回字符串——**不改 fail-soft 语义、不改返回的 int 偏移量契约、不加
外层重试(调用/重试次数与之前逐位相同)**。

诚实标注:响应字段名(orderId/executedQty/cummulativeQuoteQty/balances[].free、
openOrders/allOrders 的 origQty/transactTime、myTrades 的 isBuyer/quoteQty/commissionAsset、
exchangeInfo 的 makerCommission/takerCommission 等)依
MEXC 公开 API v3 文档规格假设,**尚未对真实 mainnet 响应验证**——首次真实联调是用户提供
API key + 人工 mandate 后的显式步骤,不在本文件范围内。MEXC MARKET 单不返回 avgPrice,
成交均价从 cummulativeQuoteQty / executedQty 折算;executedQty=0(未成交)时 avg_price=0.0。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from live.contracts import (ExchangeFilters, LiveOrderRequest, OrderBookSnapshot,
                            VenueOrderResult, VenuePosition)
from live.exchange.depth import clamp_depth_limit, parse_depth_levels
from live.exchange.retry import call_with_backoff
from quantos.contracts.config import SafetyConfig, SafetyViolation
from quantos.contracts.trading import OrderSide

_ORDER_NOT_FOUND_CODE = -2013     # MEXC: "Order does not exist."(与 Binance 同码)
_CANCEL_REJECTED_CODE = -2011     # MEXC: "Unknown order sent."(不存在或已终态,无法撤)
_RATE_LIMIT_STATUSES = frozenset({429, 418})
# MEXC /api/v3/depth 的合法 limit 档位(公开 API v3 文档;文档另有 5000 档,本适配器
# 保守不承接——SHADOW 读数 1000 档已远超需求,越深响应越大,无必要不请求)。
_DEPTH_ALLOWED_LIMITS = (5, 10, 20, 50, 100, 500, 1000)


def _is_rate_limited(result: tuple) -> bool:
    status, _data = result
    return status in _RATE_LIMIT_STATUSES


def _format_qty(qty: float) -> str:
    """浮点数量 -> 交易所期望的十进制字符串(绝不用科学计数法;去尾随零,保留整数形式)。"""
    s = f"{qty:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _parse_optional_float(raw) -> Optional[float]:
    """可选数值字段解析:缺失/不可解析 -> None(诚实缺席),绝不把解析失败硬凑成 0。"""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _require_float(raw, field: str) -> float:
    """对账输入的必填数值字段:缺失/不可解析 fail-loud(见 open_orders docstring 的
    fail-loud 裁定)。"""
    if raw is None or isinstance(raw, bool):
        raise RuntimeError(f"malformed exchange row: field {field!r} missing/not numeric: {raw!r}")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"malformed exchange row: field {field!r} not numeric: {raw!r}") from exc


def _normalize_order_row(row: dict) -> dict:
    """openOrders / allOrders 行的最小规范化(Phase 8 M1)。字段缺失/类型不符 -> fail-loud
    RuntimeError(不静默跳过——见 open_orders docstring)。"""
    if not isinstance(row, dict):
        raise RuntimeError(f"malformed exchange order row: not a dict: {type(row).__name__}")
    for key in ("orderId", "symbol", "side", "type", "status"):
        if not isinstance(row.get(key), (str, int)) or isinstance(row.get(key), bool):
            raise RuntimeError(f"malformed exchange order row: field {key!r} missing/invalid: "
                               f"{row.get(key)!r}")
    return {
        "order_id": str(row["orderId"]),
        "client_order_id": str(row.get("clientOrderId") or ""),
        "symbol": str(row["symbol"]),
        "side": str(row["side"]),
        "type": str(row["type"]),
        "status": str(row["status"]),
        "price": _require_float(row.get("price"), "price"),
        "orig_qty": _require_float(row.get("origQty"), "origQty"),
        "executed_qty": _require_float(row.get("executedQty"), "executedQty"),
        "transact_time_ms": int(_require_float(row.get("transactTime"), "transactTime")),
    }


def _normalize_trade_row(row: dict) -> dict:
    """myTrades 行的最小规范化(Phase 8 M1),fail-loud 与 _normalize_order_row 同法。
    side 从 isBuyer 推导(Binance 兼容口径);MEXC myTrades 按公开 v3 文档带 clientOrderId,
    缺席诚实置 ""。"""
    if not isinstance(row, dict):
        raise RuntimeError(f"malformed exchange trade row: not a dict: {type(row).__name__}")
    for key in ("id", "orderId", "symbol"):
        if not isinstance(row.get(key), (str, int)) or isinstance(row.get(key), bool):
            raise RuntimeError(f"malformed exchange trade row: field {key!r} missing/invalid: "
                               f"{row.get(key)!r}")
    is_buyer = row.get("isBuyer")
    if not isinstance(is_buyer, bool):
        raise RuntimeError(f"malformed exchange trade row: field 'isBuyer' missing/not bool: "
                           f"{is_buyer!r}")
    return {
        "trade_id": str(row["id"]),
        "order_id": str(row["orderId"]),
        "client_order_id": str(row.get("clientOrderId") or ""),
        "symbol": str(row["symbol"]),
        "side": "BUY" if is_buyer else "SELL",
        "price": _require_float(row.get("price"), "price"),
        "qty": _require_float(row.get("qty"), "qty"),
        "quote_qty": _require_float(row.get("quoteQty"), "quoteQty"),
        "commission": _require_float(row.get("commission"), "commission"),
        "commission_asset": str(row.get("commissionAsset") or ""),
        "time_ms": int(_require_float(row.get("time"), "time")),
        "is_maker": bool(row.get("isMaker")),
    }


def _default_http_request(method: str, url: str, *, body: Optional[bytes] = None,
                          headers: Optional[dict] = None) -> tuple:
    """生产默认 transport:urllib,15s 超时。非 2xx 通过 HTTPError 捕获,原样把 (status, body)
    交回上层解析——上层根据 MEXC 错误 code 字段区分"真错误" vs "订单不存在"等语义化情形。"""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class MexcSpotAdapter:
    """MEXC 现货 VenueAdapter 实现。MEXC **无现货 testnet**,唯一合法 base_url = MAINNET_BASE;
    mainnet 在默认 SafetyConfig 下构造即 SafetyViolation(见模块 docstring)。"""

    MAINNET_BASE = "https://api.mexc.com"
    USER_STREAM_BASE = "wss://wbs-api.mexc.com/ws"
    venue_name = "mexc"       # mandate 交易所域匹配(mandate.exchange 必须等于它)

    def __init__(self, *, api_key: str, api_secret: str, base_url: str,
                http_request: Optional[Callable] = None,
                clock: Optional[Callable[[], float]] = None,
                safety_config: Optional[SafetyConfig] = None,
                max_retries: int = 3, retry_base_delay: float = 1.0,
                sleep: Optional[Callable[[float], None]] = None) -> None:
        if base_url == self.MAINNET_BASE:
            cfg = safety_config or SafetyConfig()
            if not cfg.live_trading_armed:
                raise SafetyViolation(
                    "MEXC mainnet base_url requires live_trading_armed (Phase 3A human gate) — "
                    "refusing construction at L2; MEXC has NO spot testnet, the graduation path is "
                    "dry-run/paper + tiny real amount behind a human mandate")
            self.venue_mode = "mainnet"
        else:
            raise ValueError(
                f"unknown base_url {base_url!r} — MEXC spot has no testnet; "
                "only MAINNET_BASE is permitted")
        self._base_url = base_url
        self._api_key = api_key
        self._api_secret = api_secret
        self._http = http_request or _default_http_request
        self._clock = clock or time.time
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._sleep = sleep or time.sleep
        self._clock_offset_ms = 0
        # 时钟同步可见性(2026-09-20,P2-14):None=从未同步过;True/False=最近一次尝试的结果。
        # 与 bybit_futures.py 同款——此前裸 except Exception: pass 把失败现场完全吞掉。
        self.last_clock_sync_ok: Optional[bool] = None
        self.last_clock_sync_error: Optional[str] = None

    def sync_clock(self) -> int:
        """拉取交易所服务器时间,计算本机时钟相对服务器的偏移(ms),修正往后所有签名请求的
        timestamp(与 binance_futures 同法)。公开端点,不签名;失败 fail-soft(保留旧偏移,
        不让一次网络抖动打断整个流程),但结果记进公开只读属性
        `last_clock_sync_ok`/`last_clock_sync_error`(2026-09-20 加固,镜像
        bybit_futures.py::sync_clock 的 2026-07-09 修复)——此前失败被裸 except 完全吞掉,运维
        看不到任何记录。返回值(int 偏移量)语义不变;本 venue 不引入 bybit 那层外层重试,
        调用/重试次数保持逐位不变。"""
        error = self._try_sync_clock_once()
        self.last_clock_sync_ok = error is None
        self.last_clock_sync_error = error
        return self._clock_offset_ms

    def _try_sync_clock_once(self) -> Optional[str]:
        """一次时钟同步尝试。成功更新 `self._clock_offset_ms` 并返回 `None`;失败**不抛异常**,
        返回人类可读的失败原因字符串——调用方(`sync_clock`)据此记录可见性状态(与
        bybit_futures.py::_try_sync_clock_once 同型)。整个解析仍在 try 内:响应形状异常
        (含 serverTime 不可转 int)同样只记成失败原因,绝不让一次同步抖动变成抛异常打断整体
        fail-soft 流程。"""
        try:
            status, data = self._get("/api/v3/time", signed=False)
            if not (200 <= status < 300) or not isinstance(data, dict) or "serverTime" not in data:
                return f"unexpected /api/v3/time response (status={status}, body={data!r})"
            self._clock_offset_ms = int(data["serverTime"]) - int(self._clock() * 1000)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        return None

    # --- 签名核心 ---

    def _signed_query(self, params: dict) -> str:
        p = dict(params)
        p["timestamp"] = int(self._clock() * 1000) + self._clock_offset_ms
        p.setdefault("recvWindow", 5000)
        qs = urllib.parse.urlencode(p)
        sig = hmac.new(self._api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return f"{qs}&signature={sig}"

    @staticmethod
    def _parse_json(status: int, body: bytes) -> dict:
        """反向代理/网关故障或限流页面可能返回非 JSON body——统一成清晰的 RuntimeError,而不是
        让未处理的 json.JSONDecodeError 冒泡到调用方(与 binance_futures 同法)。"""
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            snippet = body[:200] if isinstance(body, (bytes, bytearray)) else str(body)[:200]
            raise RuntimeError(f"non-JSON response (status {status}): {snippet!r}") from exc

    def _get(self, path: str, params: Optional[dict] = None, *, signed: bool = True):
        params = params or {}
        headers = {"X-MEXC-APIKEY": self._api_key}

        def _do():
            # 签名串(含 timestamp)每次重试都重新算——退避可能跨越数秒,复用旧 timestamp
            # 有落在 recvWindow 之外的风险(与 binance_futures 同法)。
            qs = self._signed_query(params) if signed else urllib.parse.urlencode(params)
            url = self._base_url + path + (f"?{qs}" if qs else "")
            status, body = self._http("GET", url, body=None, headers=headers)
            return status, self._parse_json(status, body)
        return call_with_backoff(_do, is_rate_limited=_is_rate_limited, max_retries=self._max_retries,
                                 base_delay=self._retry_base_delay, sleep=self._sleep)

    def _post_signed(self, path: str, params: dict):
        headers = {"X-MEXC-APIKEY": self._api_key,
                  "Content-Type": "application/x-www-form-urlencoded"}
        url = self._base_url + path

        def _do():
            qs = self._signed_query(params)                    # 同上,每次重试重新签名
            status, body = self._http("POST", url, body=qs.encode(), headers=headers)
            return status, self._parse_json(status, body)
        return call_with_backoff(_do, is_rate_limited=_is_rate_limited, max_retries=self._max_retries,
                                 base_delay=self._retry_base_delay, sleep=self._sleep)

    def _delete_signed(self, path: str, params: dict):
        headers = {"X-MEXC-APIKEY": self._api_key}

        def _do():
            qs = self._signed_query(params)                    # 同上,每次重试重新签名
            url = self._base_url + path + f"?{qs}"
            status, body = self._http("DELETE", url, body=None, headers=headers)
            return status, self._parse_json(status, body)
        return call_with_backoff(_do, is_rate_limited=_is_rate_limited, max_retries=self._max_retries,
                                 base_delay=self._retry_base_delay, sleep=self._sleep)

    def _api_key_request(self, method: str, path: str, params: Optional[dict] = None):
        """API-key-only request used by MEXC listen-key lifecycle endpoints."""
        headers = {"X-MEXC-APIKEY": self._api_key}
        params = params or {}

        def _do():
            query = urllib.parse.urlencode(params)
            url = self._base_url + path + (f"?{query}" if query else "")
            status, body = self._http(method, url, body=None, headers=headers)
            return status, self._parse_json(status, body)

        return call_with_backoff(
            _do,
            is_rate_limited=_is_rate_limited,
            max_retries=self._max_retries,
            base_delay=self._retry_base_delay,
            sleep=self._sleep,
        )

    # --- 公开(无签名) ---

    def exchange_info(self) -> dict:
        status, data = self._get("/api/v3/exchangeInfo", signed=False)
        if not (200 <= status < 300):
            raise RuntimeError(f"exchange_info failed: {status} {data}")
        return data

    def exchange_filters(self) -> dict:
        """解析 LOT_SIZE / MIN_NOTIONAL / PRICE_FILTER -> ExchangeFilters(与 binance_futures
        同法)。现货 symbol 用 baseAsset(响应提供)作为 instrument_id;缺关键过滤器 -> 诚实
        丢弃该 symbol,不硬凑。"""
        data = self.exchange_info()
        out: dict = {}
        for sym in data.get("symbols", []):
            symbol = sym.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            inst = sym.get("baseAsset") or symbol[:-4]
            if not inst:
                continue
            by_type = {f.get("filterType"): f for f in sym.get("filters", [])}
            lot, notional = by_type.get("LOT_SIZE"), by_type.get("MIN_NOTIONAL")
            if lot is None or notional is None:
                continue                                    # 缺关键过滤器 -> 诚实丢弃,不硬凑
            try:
                step_size = float(lot["stepSize"])
                min_qty = float(lot["minQty"])
                min_notional = float(notional["notional"])
            except (KeyError, TypeError, ValueError):
                continue
            tick_size = None
            price_filter = by_type.get("PRICE_FILTER")
            if price_filter is not None:
                try:
                    tick_size = float(price_filter["tickSize"])
                except (KeyError, TypeError, ValueError):
                    tick_size = None
            # Phase 8 M1(2026-07-27):交易所按 symbol 报的 maker/taker 费率(MEXC
            # 零手续费现货对核心策略是真实成本项)。可解析才填,否则诚实 None——
            # 绝不把"字段缺席"当 0(缺席 ≠ 免费)。
            maker_commission = _parse_optional_float(sym.get("makerCommission"))
            taker_commission = _parse_optional_float(sym.get("takerCommission"))
            out[inst] = ExchangeFilters(instrument_id=inst, step_size=step_size, min_qty=min_qty,
                                        min_notional=min_notional, tick_size=tick_size,
                                        maker_commission=maker_commission,
                                        taker_commission=taker_commission)
        return out

    def book_ticker(self, instrument_id: str) -> Optional[dict]:
        """只读盘口(LIMIT 定价锚来源):GET /api/v3/ticker/bookTicker,不签名。fail-soft——
        非 2xx/非 JSON/缺字段一律返回 None(调用方 live/execution/pricing.py::resolve_anchor
        据此回退决策价锚),绝不因盘口拿不到而抛异常打断整批。"""
        try:
            status, data = self._get("/api/v3/ticker/bookTicker",
                                     {"symbol": instrument_id + "USDT"}, signed=False)
            if not (200 <= status < 300):
                return None
            book = {"bid": float(data["bidPrice"]), "ask": float(data["askPrice"])}
            raw_timestamp = data.get("time")
            if not isinstance(raw_timestamp, bool):
                try:
                    as_of_ms = int(raw_timestamp)
                except (TypeError, ValueError, OverflowError):
                    pass
                else:
                    if as_of_ms > 0:
                        book["as_of_ms"] = as_of_ms
            return book
        except Exception:
            return None

    def depth(self, instrument_id: str, *, limit: int = 100) -> Optional[OrderBookSnapshot]:
        """只读订单簿深度(SHADOW 比较输入,2026-07-27 Phase 7 S1):GET /api/v3/depth,
        不签名。limit 钳到 MEXC 合法档(5/10/20/50/100/500/1000,向上取档绝不少给;
        文档另有 5000 档,保守不承接)。fail-soft 与 `book_ticker` 同型——非 2xx/非 JSON/
        形状不符/任一侧空簿/任一非有限档一律 None。MEXC 该响应**不带时间戳**,
        as_of_ms 诚实置 None(绝不自造——调用方据此无法做 staleness 判定,如实缺失)。
        只读、绝不在下单路径上。"""
        clamped = clamp_depth_limit(limit, _DEPTH_ALLOWED_LIMITS)
        try:
            status, data = self._get("/api/v3/depth",
                                     {"symbol": instrument_id + "USDT", "limit": clamped},
                                     signed=False)
            if not (200 <= status < 300) or not isinstance(data, dict):
                return None
            bids = parse_depth_levels(data.get("bids"))
            asks = parse_depth_levels(data.get("asks"))
            if not bids or not asks:
                return None
            return OrderBookSnapshot(instrument_id=instrument_id, bids=bids, asks=asks,
                                     as_of_ms=None, source="mexc")
        except Exception:
            return None

    # --- 签名(账户/订单) ---

    def account(self) -> dict:
        """GET /api/v3/account。现货响应形状 {"balances": [{"asset", "free", "locked"}, ...]}
        ——原始诊断形状,不用于对账(对账走 positions() 的规范化输出)。"""
        status, data = self._get("/api/v3/account", signed=True)
        if not (200 <= status < 300):
            raise RuntimeError(f"account failed: {status} {data}")
        return data

    def positions(self) -> dict:
        """现货语义规范化仓位:非零**自由**余额折算 {asset: VenuePosition}。BTC 余额即多仓,
        quantity 恒 > 0(现货无做空);USDT 稳定币现金腿跳过(不是风险敞口标的)。entry_price
        诚实置 0.0——账户快照无法恢复成本基。冻结(locked)余额不计:有挂单冻结的资产
        不能被视为可支配仓位。"""
        raw = self.account()
        out: dict = {}
        for bal in raw.get("balances", []):
            asset = bal.get("asset", "")
            if not asset or asset == "USDT":
                continue
            try:
                free = float(bal["free"])
            except (KeyError, TypeError, ValueError):
                continue
            if free <= 0.0:
                continue
            out[asset] = VenuePosition(instrument_id=asset, quantity=free, entry_price=0.0)
        return out

    def open_orders(self, instrument_id: Optional[str] = None) -> list:
        """GET /api/v3/openOrders(签名,只读)——交易所侧当前挂单权威清单
        (Phase 8 M1,727 §13.5 启动对账的交易所侧输入)。
        instrument_id 为 None 时不带 symbol 参数(全市场挂单)。

        **fail-loud 裁定(与 book_ticker 的 fail-soft 相反,刻意)**:非 2xx ->
        RuntimeError(带 status);响应非 list 或任一行字段缺失/类型不符 ->
        RuntimeError。本方法喂的是**对账**——静默跳过一行等于把交易所侧的真实挂单
        从对账视野里抹掉(账本说无单、交易所说有单,漂移不报错)。宁可整批拒绝,
        由调用方(M2/M3 的对账器)显式处理。只读、绝不在下单路径上。"""
        params = {"symbol": instrument_id + "USDT"} if instrument_id else {}
        status, data = self._get("/api/v3/openOrders", params, signed=True)
        if not (200 <= status < 300):
            raise RuntimeError(f"open_orders failed: {status} {data}")
        if not isinstance(data, list):
            raise RuntimeError(f"open_orders failed: expected list payload, got "
                               f"{type(data).__name__}: {data}")
        return [_normalize_order_row(row) for row in data]

    def all_orders(self, instrument_id: str, *, limit: int = 500) -> list:
        """GET /api/v3/allOrders(签名,只读)——近期订单历史(含终态)。
        symbol 必填;limit 钳 [1,1000]。**MEXC 窗口诚实注记:该端点只覆盖最近
        7 天**(公开 v3 文档口径)——更早的历史要靠本地账本/成交回填,本方法不承诺
        全量历史。fail-loud 与 open_orders 同裁定。"""
        clamped = max(1, min(int(limit), 1000))
        params = {"symbol": instrument_id + "USDT", "limit": clamped}
        status, data = self._get("/api/v3/allOrders", params, signed=True)
        if not (200 <= status < 300):
            raise RuntimeError(f"all_orders failed: {status} {data}")
        if not isinstance(data, list):
            raise RuntimeError(f"all_orders failed: expected list payload, got "
                               f"{type(data).__name__}: {data}")
        return [_normalize_order_row(row) for row in data]

    def my_trades(self, instrument_id: str, *, limit: int = 500) -> list:
        """GET /api/v3/myTrades(签名,只读)——账户成交历史(逐笔 fill,含手续费)。
        limit 钳 [1,1000]。**MEXC 窗口诚实注记:约最近 1 个月**(公开 v3 文档口径)。
        这是 §13.4 unknown 订单接管与 §14 成交对账的交易所侧输入——commission /
        commission_asset 原样透出(零手续费 venue 恒 0 是 venue 事实,不是解析假设)。
        fail-loud 与 open_orders 同裁定。"""
        clamped = max(1, min(int(limit), 1000))
        params = {"symbol": instrument_id + "USDT", "limit": clamped}
        status, data = self._get("/api/v3/myTrades", params, signed=True)
        if not (200 <= status < 300):
            raise RuntimeError(f"my_trades failed: {status} {data}")
        if not isinstance(data, list):
            raise RuntimeError(f"my_trades failed: expected list payload, got "
                               f"{type(data).__name__}: {data}")
        return [_normalize_trade_row(row) for row in data]

    def place_order(self, order: LiveOrderRequest) -> VenueOrderResult:
        """LiveOrderRequest -> MEXC MARKET/LIMIT 现货单(POST /api/v3/order)。

        现货无 reduce_only 交易所参数:`reduce_only=True` 映射为"仅 SELL ∧ 数量 ≤ 自由余额"
        ——BUY 或超出余额直接本地拒绝(不提交),宁可误拒也不把紧急减仓单变成开新仓。
        LIMIT 带 price + timeInForce(GTC;现货无 GTX——post_only 映射为限价单已是最小可
        用诚实口径,驱动器不接 post_only)。"""
        if order.reduce_only:
            if order.side is not OrderSide.SELL:
                raise RuntimeError(
                    "spot has no reduce-only parameter: reduce_only=True maps to SELL-only, "
                    f"refusing BUY order for {order.instrument_id}")
            free = self._free_balance(order.instrument_id)
            if order.quantity > free:
                raise RuntimeError(
                    f"spot reduce-only SELL rejected locally: qty {order.quantity} exceeds free "
                    f"balance {free} of {order.instrument_id} — refusing to submit")
        status, data = self._post_signed("/api/v3/order", self._order_params(order))
        if not (200 <= status < 300):
            raise RuntimeError(f"place_order failed: {status} {data}")
        return self._to_order_result(data, order.instrument_id, order.client_order_id)

    def test_order(self, order: LiveOrderRequest) -> dict:
        """**此端点永不进撮合**(MEXC 端语义:`POST /api/v3/order/test` 只创建并校验订单、
        不送入撮合引擎,成功响应空对象 `{}`)——仅用于 727 §13 阶段 C / Phase 8 M2 的链路
        验证(签名/timestamp/recvWindow/参数校验/权限的交易所侧回答)。

        参数构造经 `_order_params` 与 `place_order` **逐字段共享**——「测的就是会发的」
        由机器证明(参数一致性测试钉死),不是纪律声明。**端点级薄封装**:精度/最小名义
        预校验归管线(`apply_exchange_filters`),reduce_only 余额检查归 `place_order`
        本地——本方法不复制任何预校验,给什么 LiveOrderRequest 就验证什么。

        结构性隔离(never-matching 双保险):本方法**不进** VenueAdapter Protocol;
        `submit_live_batch`/ops API/任何下单路径绝不引用它(治理 AST 钉死);真实
        mainnet 首次调用是人工专属步骤(tools/mexc_readonly_smoke.py --test-order)。
        非 2xx -> RuntimeError 带 MEXC 错误码原文(与 place_order 同错误语义)。"""
        status, data = self._post_signed("/api/v3/order/test", self._order_params(order))
        if not (200 <= status < 300):
            raise RuntimeError(f"test_order failed: {status} {data}")
        return {"validated": True, "client_order_id": order.client_order_id,
                "exchange_response": data}

    @staticmethod
    def _order_params(order: LiveOrderRequest) -> dict:
        """LiveOrderRequest -> MEXC 下单参数。place_order 与 test_order 的唯一共享构造点
        ——保证「测的就是会发的」(Phase 8 M2);place_order 行为逐位不变。"""
        params = {
            "symbol": order.instrument_id + "USDT",
            "side": order.side.value,
            "type": order.order_type,
            "quantity": _format_qty(order.quantity),
            "newClientOrderId": order.client_order_id,
        }
        if order.order_type == "LIMIT":
            params["price"] = _format_qty(order.limit_price)
            params["timeInForce"] = "GTC"      # 现货无 GTX;post_only 字段预留、驱动器不接
        return params

    def get_order(self, instrument_id: str, client_order_id: str) -> Optional[VenueOrderResult]:
        params = {"symbol": instrument_id + "USDT", "origClientOrderId": client_order_id}
        status, data = self._get("/api/v3/order", params, signed=True)
        if 200 <= status < 300:
            return self._to_order_result(data, instrument_id, client_order_id)
        if isinstance(data, dict) and data.get("code") == _ORDER_NOT_FOUND_CODE:
            return None                                      # 语义化"订单不存在" -> None,非异常
        raise RuntimeError(f"get_order failed: {status} {data}")

    def cancel_order(self, instrument_id: str, client_order_id: str) -> Optional[VenueOrderResult]:
        """撤单(DELETE /api/v3/order)。响应可能带着撤单前已发生的部分成交(executedQty>0)
        ——按交易所报的真实数量透传。-2011(无法撤:不存在或已终态)/-2013(不存在)-> 语义化
        None——撤单与成交赛跑的正常路径,调用方必须再 get_order 回查权威状态定论。"""
        params = {"symbol": instrument_id + "USDT", "origClientOrderId": client_order_id}
        status, data = self._delete_signed("/api/v3/order", params)
        if 200 <= status < 300:
            return self._to_order_result(data, instrument_id, client_order_id)
        if isinstance(data, dict) and data.get("code") in (_CANCEL_REJECTED_CODE, _ORDER_NOT_FOUND_CODE):
            return None
        raise RuntimeError(f"cancel_order failed: {status} {data}")

    # --- User stream (M4; transport/Protobuf decoder injected) ---

    def create_listen_key(self) -> str:
        status, data = self._api_key_request("POST", "/api/v3/userDataStream")
        key = data.get("listenKey") if isinstance(data, dict) else None
        if not (200 <= status < 300) or not isinstance(key, str) or not key:
            raise RuntimeError(f"create listen key failed: {status}")
        return key

    def keepalive_listen_key(self, listen_key: str) -> None:
        if not isinstance(listen_key, str) or not listen_key:
            raise ValueError("listen_key must be non-empty")
        status, _ = self._api_key_request(
            "PUT", "/api/v3/userDataStream", {"listenKey": listen_key}
        )
        if not (200 <= status < 300):
            raise RuntimeError(f"keepalive listen key failed: {status}")

    def close_listen_key(self, listen_key: str) -> None:
        if not isinstance(listen_key, str) or not listen_key:
            raise ValueError("listen_key must be non-empty")
        status, _ = self._api_key_request(
            "DELETE", "/api/v3/userDataStream", {"listenKey": listen_key}
        )
        if not (200 <= status < 300):
            raise RuntimeError(f"close listen key failed: {status}")

    def user_stream(
        self,
        *,
        ws_connect: Callable[[str], object],
        decode: Callable[[object], dict],
        on_event: Callable[[dict], object],
        rest_backfill: Callable[["MexcSpotAdapter"], None],
        max_reconnects: int = 3,
    ) -> int:
        """Consume private account/order/deal events with fail-closed recovery.

        MEXC currently emits Protobuf frames, so the transport and decoder are
        explicit dependencies; there is no permissive JSON fallback. Every
        disconnect runs the authoritative REST backfill before reconnecting.
        `on_event` returns False for a clean operator stop. The listen key is
        always invalidated on exit.
        """
        if max_reconnects < 0:
            raise ValueError("max_reconnects must be >= 0")
        for value, name in (
            (ws_connect, "ws_connect"),
            (decode, "decode"),
            (on_event, "on_event"),
            (rest_backfill, "rest_backfill"),
        ):
            if not callable(value):
                raise TypeError(f"{name} must be callable")
        listen_key = self.create_listen_key()
        reconnects = 0
        channels = (
            "spot@private.account.v3.api.pb",
            "spot@private.orders.v3.api.pb",
            "spot@private.deals.v3.api.pb",
        )
        try:
            while True:
                socket = None
                try:
                    socket = ws_connect(
                        f"{self.USER_STREAM_BASE}?listenKey={listen_key}"
                    )
                    socket.send(
                        json.dumps(
                            {"method": "SUBSCRIPTION", "params": list(channels)},
                            separators=(",", ":"),
                        )
                    )
                    while True:
                        frame = socket.recv()
                        if frame is None:
                            raise ConnectionError("MEXC user stream disconnected")
                        event = decode(frame)
                        if not isinstance(event, dict):
                            raise RuntimeError("MEXC user stream decoder returned non-object")
                        if event.get("msg") == "PING":
                            socket.send('{"method":"PING"}')
                            continue
                        if on_event(event) is False:
                            return reconnects
                except Exception:
                    rest_backfill(self)
                    if reconnects >= max_reconnects:
                        raise
                    reconnects += 1
                    self.keepalive_listen_key(listen_key)
                finally:
                    if socket is not None:
                        try:
                            socket.close()
                        except Exception:
                            pass
        finally:
            try:
                self.close_listen_key(listen_key)
            except Exception:
                pass

    # --- 内部辅助 ---

    def _free_balance(self, asset: str) -> float:
        """现货 reduce-only 校验用:取该币种自由余额(带账号签名查询)。缺失/解析失败 -> 0.0
        (fail-closed:查不到余额就宁可拒绝减仓单,也不放它过去)。"""
        try:
            for bal in self.account().get("balances", []):
                if bal.get("asset") == asset:
                    return float(bal.get("free", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
        return 0.0

    @staticmethod
    def _to_order_result(data: dict, instrument_id: str, client_order_id: str) -> VenueOrderResult:
        """MEXC MARKET 单不返回 avgPrice——成交均价从 cummulativeQuoteQty / executedQty 折算;
        未成交(executedQty=0)时 avg_price=0.0,绝不硬凑。"""
        filled = float(data.get("executedQty", 0) or 0)
        try:
            avg = float(data.get("avgPrice", 0) or 0)
        except (TypeError, ValueError):
            avg = 0.0
        if avg == 0.0 and filled > 0.0:
            quote = float(data.get("cummulativeQuoteQty", 0) or 0)
            if quote > 0.0:
                avg = quote / filled
        return VenueOrderResult(
            venue_order_id=str(data.get("orderId", "")),
            client_order_id=data.get("clientOrderId", client_order_id),
            instrument_id=instrument_id, status=data.get("status", "UNKNOWN"),
            filled_qty=filled, avg_price=avg, raw=data)
