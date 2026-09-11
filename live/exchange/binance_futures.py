"""Binance USDT-M 合约签名 REST 客户端(Stage 1)。设计:
docs/engineering/specs/2026-07-04-live-execution-stack-design.md §3.3。

纯 stdlib(urllib/hmac/hashlib/json/time)——绝不 import 交易所 SDK(ccxt/python-binance 等)。
`http_request` 可注入(测试用假 transport,零真网络;生产默认走内置 urllib 实现)。

诚实边界:请求签名机制(HMAC-SHA256 查询串)、`exchangeInfo`/`account`/`order` 响应字段名依
公开 API 文档规格假设,**尚未对真测试网响应验证**——首次真实联调是用户提供测试网 API key 后的
显式步骤,不在本次自动执行范围内。只实现 MARKET 单(与前向跟踪信号路径一致);提供撤单
(`cancel_order`,2026-07-05 OMS 补全——超时僵尸单处置的前提);不提供改单/杠杆配置;
绝不提供提幣(withdraw)/划转(transfer)——治理测试钉死这一点。

mainnet 结构性拒绝(与 services/trading/broker_adapter.py::LiveBrokerAdapter 对称):
用 MAINNET_BASE 构造时若 `not safety_config.live_trading_armed`(L2 默认恒 False)→ raise
SafetyViolation。testnet 不受 SafetyConfig 管辖(它治理的是真钱/真券商,测试网是交易所官方沙盒,
零真实资本——见设计 spec §1.1);testnet 的准入来自"是否提供了测试网 API key"这一自然前提。

**运维硬化(2026-07-04,§12):** HTTP 429(限流)/418(IP 封禁)不再和其它错误一视同仁地立刻
raise——`live/exchange/retry.py::call_with_backoff` 指数退避重试,耗尽仍限流才 `RateLimitedError`
(区别于普通 `RuntimeError`,调用方可识别)。`sync_clock()` 拉取交易所服务器时间、计算本机时钟
偏移,修正签名请求的 `timestamp`——fail-soft(查询失败保留旧偏移,不打断流程),默认偏移为 0
(不调用 `sync_clock()` 则行为与之前完全一致)。
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

from live.contracts import (ExchangeFilters, FundingRateSnapshot, LiveOrderRequest,
                            OrderBookSnapshot, PremiumIndexSnapshot, VenueOrderResult,
                            VenuePosition)
from live.exchange.depth import clamp_depth_limit, extract_as_of_ms, parse_depth_levels
from live.exchange.retry import call_with_backoff
from quantos.contracts.config import SafetyConfig, SafetyViolation
from quantos.contracts.binance_demo import DEMO_REST_BASE

_ORDER_NOT_FOUND_CODE = -2013     # Binance: "Order does not exist."
_CANCEL_REJECTED_CODE = -2011     # Binance: "Unknown order sent."(不存在或已终态,无法撤)
_RATE_LIMIT_STATUSES = frozenset({429, 418})
_FUNDING_RATE_MAX_LIMIT = 1000      # Binance /fapi/v1/fundingRate 的 limit 上限
_DEPTH_ALLOWED_LIMITS = (5, 10, 20, 50, 100, 500, 1000)   # Binance /fapi/v1/depth 合法 limit 档


def _is_rate_limited(result: tuple) -> bool:
    status, _data = result
    return status in _RATE_LIMIT_STATUSES


def _format_qty(qty: float) -> str:
    """浮点数量 -> Binance 期望的十进制字符串(绝不用科学计数法;去尾随零,保留整数形式)。"""
    s = f"{qty:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _default_http_request(method: str, url: str, *, body: Optional[bytes] = None,
                          headers: Optional[dict] = None) -> tuple:
    """生产默认 transport:urllib,15s 超时。非 2xx 通过 HTTPError 捕获,原样把 (status, body) 交回
    上层解析——上层根据 Binance 的错误 code 字段区分"真错误" vs "订单不存在"等语义化情形。"""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class BinanceFuturesAdapter:
    TESTNET_BASE = DEMO_REST_BASE
    MAINNET_BASE = "https://fapi.binance.com"
    venue_name = "binance"     # mandate 交易所域匹配(mandate.exchange 必须等于它,2026-07-05)

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
                    "mainnet base_url requires live_trading_armed (Phase 3A human gate) — "
                    "refusing construction at L2 (mirrors LiveBrokerAdapter's seam refusal)")
            self.venue_mode = "mainnet"
        elif base_url == self.TESTNET_BASE:
            self.venue_mode = "testnet"
        else:
            raise ValueError(f"unknown base_url {base_url!r} — only TESTNET_BASE/MAINNET_BASE permitted")
        self._base_url = base_url
        self._api_key = api_key
        self._api_secret = api_secret
        self._http = http_request or _default_http_request
        self._clock = clock or time.time
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._sleep = sleep or time.sleep
        self._clock_offset_ms = 0

    def sync_clock(self) -> int:
        """拉取交易所服务器时间,计算本机时钟相对服务器的偏移(ms),修正往后所有签名请求的
        timestamp——回答 2026-07-04 审查记录在案的时钟偏移缺口(recvWindow 硬编码 5000ms,没有
        校正在真实网络延迟/时钟漂移下容易导致 -1021 时间戳错误)。公开端点,不签名;失败 fail-soft
        (保留旧偏移,不让一次网络抖动打断整个流程)。"""
        try:
            status, data = self._get("/fapi/v1/time", signed=False)
            if 200 <= status < 300 and isinstance(data, dict) and "serverTime" in data:
                self._clock_offset_ms = int(data["serverTime"]) - int(self._clock() * 1000)
        except Exception:
            pass
        return self._clock_offset_ms

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
        """反向代理/网关故障或限流页面可能返回非 JSON body(2026-07-04 对抗式审查发现 #14)——
        统一成清晰的 RuntimeError,而不是让未处理的 json.JSONDecodeError 冒泡到调用方,和其它
        错误路径的诊断体验保持一致。"""
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            snippet = body[:200] if isinstance(body, (bytes, bytearray)) else str(body)[:200]
            raise RuntimeError(f"non-JSON response (status {status}): {snippet!r}") from exc

    def _get(self, path: str, params: Optional[dict] = None, *, signed: bool = True):
        params = params or {}
        headers = {"X-MBX-APIKEY": self._api_key} if signed else {}

        def _do():
            # 签名串(含 timestamp)每次重试都重新算——退避可能跨越数秒,复用旧 timestamp 有落在
            # recvWindow 之外的风险(2026-07-04 运维硬化,§12)。
            qs = self._signed_query(params) if signed else urllib.parse.urlencode(params)
            url = self._base_url + path + (f"?{qs}" if qs else "")
            status, body = self._http("GET", url, body=None, headers=headers)
            return status, self._parse_json(status, body)
        return call_with_backoff(_do, is_rate_limited=_is_rate_limited, max_retries=self._max_retries,
                                 base_delay=self._retry_base_delay, sleep=self._sleep)

    def _post_signed(self, path: str, params: dict):
        headers = {"X-MBX-APIKEY": self._api_key,
                  "Content-Type": "application/x-www-form-urlencoded"}
        url = self._base_url + path

        def _do():
            qs = self._signed_query(params)                    # 同上,每次重试重新签名
            status, body = self._http("POST", url, body=qs.encode(), headers=headers)
            return status, self._parse_json(status, body)
        return call_with_backoff(_do, is_rate_limited=_is_rate_limited, max_retries=self._max_retries,
                                 base_delay=self._retry_base_delay, sleep=self._sleep)

    def _delete_signed(self, path: str, params: dict):
        headers = {"X-MBX-APIKEY": self._api_key}

        def _do():
            qs = self._signed_query(params)                    # 同上,每次重试重新签名
            url = self._base_url + path + f"?{qs}"
            status, body = self._http("DELETE", url, body=None, headers=headers)
            return status, self._parse_json(status, body)
        return call_with_backoff(_do, is_rate_limited=_is_rate_limited, max_retries=self._max_retries,
                                 base_delay=self._retry_base_delay, sleep=self._sleep)

    # --- 公开(无签名) ---

    def exchange_info(self) -> dict:
        status, data = self._get("/fapi/v1/exchangeInfo", signed=False)
        if not (200 <= status < 300):
            raise RuntimeError(f"exchange_info failed: {status} {data}")
        return data

    def exchange_filters(self) -> dict:
        data = self.exchange_info()
        out: dict = {}
        for sym in data.get("symbols", []):
            symbol = sym.get("symbol", "")
            if not symbol.endswith("USDT"):
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
            inst = symbol[:-4]
            out[inst] = ExchangeFilters(instrument_id=inst, step_size=step_size, min_qty=min_qty,
                                        min_notional=min_notional, tick_size=tick_size)
        return out

    def book_ticker(self, instrument_id: str) -> Optional[dict]:
        """只读盘口(2026-07-06 战线②,LIMIT 定价锚来源):GET /fapi/v1/ticker/bookTicker,不
        签名。fail-soft——非 2xx/非 JSON/缺字段一律返回 None(调用方
        `live/execution/pricing.py::resolve_anchor` 据此回退决策价锚),绝不因盘口拿不到
        而抛异常打断整批。"""
        try:
            status, data = self._get("/fapi/v1/ticker/bookTicker",
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

    def premium_index(self, instrument_id: str) -> Optional[PremiumIndexSnapshot]:
        """只读标记价/指数价/最新资金费快照(Phase 2 缺口:此前资金费数据只有离线日级快照):
        GET /fapi/v1/premiumIndex,不签名。fail-soft 与 `book_ticker` 同型——非 2xx/非 JSON/
        缺字段/非有限值(NaN/Inf)一律返回 None(它是定价/诊断输入,不在下单路径上),
        绝不因快照拿不到而抛异常打断整批。"""
        try:
            status, data = self._get("/fapi/v1/premiumIndex",
                                     {"symbol": instrument_id + "USDT"}, signed=False)
            if not (200 <= status < 300):
                return None
            return PremiumIndexSnapshot(
                instrument_id=instrument_id,
                mark_price=float(data["markPrice"]),
                index_price=float(data["indexPrice"]),
                last_funding_rate=float(data["lastFundingRate"]),
                next_funding_time_ms=int(data["nextFundingTime"]),
                time_ms=int(data["time"]))
        except Exception:
            return None

    def funding_rates(self, instrument_id: str, *,
                      limit: int = 100) -> Optional[tuple[FundingRateSnapshot, ...]]:
        """只读历史资金费序列:GET /fapi/v1/fundingRate,不签名。limit 钳制到 Binance 上限 1000
        (下限 1)。fail-soft 与 `book_ticker` 同型——非 2xx/非 JSON/非数组/缺字段/非有限值
        一律返回 None。"""
        clamped = max(1, min(int(limit), _FUNDING_RATE_MAX_LIMIT))
        try:
            status, data = self._get("/fapi/v1/fundingRate",
                                     {"symbol": instrument_id + "USDT", "limit": clamped},
                                     signed=False)
            if not (200 <= status < 300) or not isinstance(data, list):
                return None
            return tuple(
                FundingRateSnapshot(instrument_id=instrument_id,
                                    funding_rate=float(item["fundingRate"]),
                                    funding_time_ms=int(item["fundingTime"]))
                for item in data)
        except Exception:
            return None

    def depth(self, instrument_id: str, *, limit: int = 100) -> Optional[OrderBookSnapshot]:
        """只读订单簿深度(SHADOW 比较输入,2026-07-27 Phase 7 S1):GET /fapi/v1/depth,
        不签名。limit 钳到 Binance 合法档(5/10/20/50/100/500/1000,向上取档绝不少给)。
        fail-soft 与 `book_ticker` 同型——非 2xx/非 JSON/形状不符/任一侧空簿/任一非有限档
        一律 None。as_of_ms 取载荷的 `E`(event time,行情推送时刻),缺失回退 `T`
        (transaction time),都没有则 None(绝不自造)。只读、绝不在下单路径上。"""
        clamped = clamp_depth_limit(limit, _DEPTH_ALLOWED_LIMITS)
        try:
            status, data = self._get("/fapi/v1/depth",
                                     {"symbol": instrument_id + "USDT", "limit": clamped},
                                     signed=False)
            if not (200 <= status < 300) or not isinstance(data, dict):
                return None
            bids = parse_depth_levels(data.get("bids"))
            asks = parse_depth_levels(data.get("asks"))
            if not bids or not asks:
                return None
            return OrderBookSnapshot(instrument_id=instrument_id, bids=bids, asks=asks,
                                     as_of_ms=extract_as_of_ms(data.get("E"), data.get("T")),
                                     source="binance")
        except Exception:
            return None

    # --- 签名(账户/订单) ---

    def account(self) -> dict:
        status, data = self._get("/fapi/v2/account", signed=True)
        if not (200 <= status < 300):
            raise RuntimeError(f"account failed: {status} {data}")
        return data

    def positions(self) -> dict:
        """规范化仓位。Hedge Mode(双向持仓)下同一 symbol 会在 `positions` 数组里出现 LONG/SHORT
        两条记录——2026-07-04 对抗式审查发现 #13:早期实现按 symbol 做 key 直接覆盖写入,会静默
        丢弃一侧、把净敞口算错而不报错(比崩溃更危险)。这里改成按标的**累加**净数量;entry_price
        没有单一"入场价"的良定义(两条腿是相反方向的仓位),取绝对数量更大那条腿的入场价作为参考。
        """
        raw = self.account()
        legs: dict = {}                                       # inst -> (net_qty, dominant_entry, dominant_abs_qty)
        for p in raw.get("positions", []):
            symbol = p.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            try:
                qty = float(p["positionAmt"])
            except (KeyError, TypeError, ValueError):
                continue
            if qty == 0.0:
                continue                                      # 该腿本身平仓,跳过(不代表整个标的平仓)
            try:
                entry = float(p.get("entryPrice", 0.0) or 0.0)
            except (TypeError, ValueError):
                entry = 0.0
            inst = symbol[:-4]
            prev_qty, prev_entry, prev_abs = legs.get(inst, (0.0, 0.0, 0.0))
            abs_qty = abs(qty)
            dominant_entry, dominant_abs = (entry, abs_qty) if abs_qty >= prev_abs else (prev_entry, prev_abs)
            legs[inst] = (prev_qty + qty, dominant_entry, dominant_abs)
        return {inst: VenuePosition(instrument_id=inst, quantity=q, entry_price=e)
               for inst, (q, e, _) in legs.items() if q != 0.0}   # 净敞口归零(完全对冲)= 诚实丢弃

    def place_order(self, order: LiveOrderRequest) -> VenueOrderResult:
        params = {
            "symbol": order.instrument_id + "USDT",
            "side": order.side.value,
            "type": order.order_type,
            "quantity": _format_qty(order.quantity),
            "newClientOrderId": order.client_order_id,
        }
        # LIMIT(2026-07-06 战线②):默认路径(order_type="MARKET")这个分支不触发,params
        # 与改动前逐键相等(回归锁 test_binance_market_request_shape_byte_identical 钉死)。
        # 价格已在 live/execution/pricing.py 按 tick 取整,这里只格式化不再取整(单一职责)。
        # GTX(post-only)越过盘口时 Binance 直接给终态 EXPIRED——已在 _TERMINAL_ORDER_STATUSES,
        # 流水线零改动即正确处置。
        if order.order_type == "LIMIT":
            params["price"] = _format_qty(order.limit_price)
            params["timeInForce"] = "GTX" if order.post_only else "GTC"
        if order.reduce_only:
            params["reduceOnly"] = "true"        # 仅紧急减仓通道;普通单不带,保持已验证的请求形状
        status, data = self._post_signed("/fapi/v1/order", params)
        if not (200 <= status < 300):
            raise RuntimeError(f"place_order failed: {status} {data}")
        return VenueOrderResult(
            venue_order_id=str(data.get("orderId", "")),
            client_order_id=data.get("clientOrderId", order.client_order_id),
            instrument_id=order.instrument_id, status=data.get("status", "UNKNOWN"),
            filled_qty=float(data.get("executedQty", 0) or 0),
            avg_price=float(data.get("avgPrice", 0) or 0), raw=data)

    def get_order(self, instrument_id: str, client_order_id: str) -> Optional[VenueOrderResult]:
        params = {"symbol": instrument_id + "USDT", "origClientOrderId": client_order_id}
        status, data = self._get("/fapi/v1/order", params, signed=True)
        if 200 <= status < 300:
            return VenueOrderResult(
                venue_order_id=str(data.get("orderId", "")),
                client_order_id=data.get("clientOrderId", client_order_id),
                instrument_id=instrument_id, status=data.get("status", "UNKNOWN"),
                filled_qty=float(data.get("executedQty", 0) or 0),
                avg_price=float(data.get("avgPrice", 0) or 0), raw=data)
        if isinstance(data, dict) and data.get("code") == _ORDER_NOT_FOUND_CODE:
            return None                                      # 语义化"订单不存在" -> None,非异常
        raise RuntimeError(f"get_order failed: {status} {data}")

    def cancel_order(self, instrument_id: str, client_order_id: str) -> Optional[VenueOrderResult]:
        """撤单(OMS 超时策略的前提,2026-07-05)。响应可能带着撤单前已发生的部分成交
        (executedQty>0)——按交易所报的真实数量透传,绝不假设"撤单=零成交"。-2011(无法撤:
        不存在或已终态)/-2013(不存在)-> 语义化 None——撤单与成交赛跑的正常路径,调用方
        必须再 get_order 回查权威状态定论,不是异常。"""
        params = {"symbol": instrument_id + "USDT", "origClientOrderId": client_order_id}
        status, data = self._delete_signed("/fapi/v1/order", params)
        if 200 <= status < 300:
            return VenueOrderResult(
                venue_order_id=str(data.get("orderId", "")),
                client_order_id=data.get("clientOrderId", client_order_id),
                instrument_id=instrument_id, status=data.get("status", "UNKNOWN"),
                filled_qty=float(data.get("executedQty", 0) or 0),
                avg_price=float(data.get("avgPrice", 0) or 0), raw=data)
        if isinstance(data, dict) and data.get("code") in (_CANCEL_REJECTED_CODE, _ORDER_NOT_FOUND_CODE):
            return None
        raise RuntimeError(f"cancel_order failed: {status} {data}")
