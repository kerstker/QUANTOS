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

诚实标注:响应字段名(orderId/executedQty/cummulativeQuoteQty/balances[].free 等)依
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

from live.contracts import ExchangeFilters, LiveOrderRequest, VenueOrderResult, VenuePosition
from live.exchange.retry import call_with_backoff
from quantos.contracts.config import SafetyConfig, SafetyViolation
from quantos.contracts.trading import OrderSide

_ORDER_NOT_FOUND_CODE = -2013     # MEXC: "Order does not exist."(与 Binance 同码)
_CANCEL_REJECTED_CODE = -2011     # MEXC: "Unknown order sent."(不存在或已终态,无法撤)
_RATE_LIMIT_STATUSES = frozenset({429, 418})


def _is_rate_limited(result: tuple) -> bool:
    status, _data = result
    return status in _RATE_LIMIT_STATUSES


def _format_qty(qty: float) -> str:
    """浮点数量 -> 交易所期望的十进制字符串(绝不用科学计数法;去尾随零,保留整数形式)。"""
    s = f"{qty:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


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

    def sync_clock(self) -> int:
        """拉取交易所服务器时间,计算本机时钟相对服务器的偏移(ms),修正往后所有签名请求的
        timestamp(与 binance_futures 同法)。公开端点,不签名;失败 fail-soft(保留旧偏移,
        不让一次网络抖动打断整个流程)。"""
        try:
            status, data = self._get("/api/v3/time", signed=False)
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
            out[inst] = ExchangeFilters(instrument_id=inst, step_size=step_size, min_qty=min_qty,
                                        min_notional=min_notional, tick_size=tick_size)
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
        status, data = self._post_signed("/api/v3/order", params)
        if not (200 <= status < 300):
            raise RuntimeError(f"place_order failed: {status} {data}")
        return self._to_order_result(data, order.instrument_id, order.client_order_id)

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
