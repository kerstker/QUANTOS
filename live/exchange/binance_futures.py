"""Binance USDT-M 合约签名 REST 客户端(Stage 1)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.3。

纯 stdlib(urllib/hmac/hashlib/json/time)——绝不 import 交易所 SDK(ccxt/python-binance 等)。
`http_request` 可注入(测试用假 transport,零真网络;生产默认走内置 urllib 实现)。

诚实边界:请求签名机制(HMAC-SHA256 查询串)、`exchangeInfo`/`account`/`order` 响应字段名依
公开 API 文档规格假设,**尚未对真测试网响应验证**——首次真实联调是用户提供测试网 API key 后的
显式步骤,不在本次自动执行范围内。只实现 MARKET 单(与前向跟踪信号路径一致);不提供撤单/改单/
杠杆配置;绝不提供提幣(withdraw)/划转(transfer)——治理测试钉死这一点。

mainnet 结构性拒绝(与 services/trading/broker_adapter.py::LiveBrokerAdapter 对称):
用 MAINNET_BASE 构造时若 `not safety_config.live_trading_armed`(L2 默认恒 False)→ raise
SafetyViolation。testnet 不受 SafetyConfig 管辖(它治理的是真钱/真券商,测试网是交易所官方沙盒,
零真实资本——见设计 spec §1.1);testnet 的准入来自"是否提供了测试网 API key"这一自然前提。
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
from quantos.contracts.config import SafetyConfig, SafetyViolation

_ORDER_NOT_FOUND_CODE = -2013     # Binance: "Order does not exist."


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
    TESTNET_BASE = "https://testnet.binancefuture.com"
    MAINNET_BASE = "https://fapi.binance.com"

    def __init__(self, *, api_key: str, api_secret: str, base_url: str,
                http_request: Optional[Callable] = None,
                clock: Optional[Callable[[], float]] = None,
                safety_config: Optional[SafetyConfig] = None) -> None:
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

    # --- 签名核心 ---

    def _signed_query(self, params: dict) -> str:
        p = dict(params)
        p["timestamp"] = int(self._clock() * 1000)
        p.setdefault("recvWindow", 5000)
        qs = urllib.parse.urlencode(p)
        sig = hmac.new(self._api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return f"{qs}&signature={sig}"

    def _get(self, path: str, params: Optional[dict] = None, *, signed: bool = True):
        params = params or {}
        headers = {"X-MBX-APIKEY": self._api_key}
        qs = self._signed_query(params) if signed else urllib.parse.urlencode(params)
        url = self._base_url + path + (f"?{qs}" if qs else "")
        status, body = self._http("GET", url, body=None, headers=headers)
        return status, json.loads(body)

    def _post_signed(self, path: str, params: dict):
        qs = self._signed_query(params)
        headers = {"X-MBX-APIKEY": self._api_key,
                  "Content-Type": "application/x-www-form-urlencoded"}
        url = self._base_url + path
        status, body = self._http("POST", url, body=qs.encode(), headers=headers)
        return status, json.loads(body)

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

    # --- 签名(账户/订单) ---

    def account(self) -> dict:
        status, data = self._get("/fapi/v2/account", signed=True)
        if not (200 <= status < 300):
            raise RuntimeError(f"account failed: {status} {data}")
        return data

    def positions(self) -> dict:
        raw = self.account()
        out: dict = {}
        for p in raw.get("positions", []):
            symbol = p.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            try:
                qty = float(p["positionAmt"])
            except (KeyError, TypeError, ValueError):
                continue
            if qty == 0.0:
                continue                                     # 平仓不出现(诚实,不塞噪声)
            try:
                entry = float(p.get("entryPrice", 0.0) or 0.0)
            except (TypeError, ValueError):
                entry = 0.0
            inst = symbol[:-4]
            out[inst] = VenuePosition(instrument_id=inst, quantity=qty, entry_price=entry)
        return out

    def place_order(self, order: LiveOrderRequest) -> VenueOrderResult:
        params = {
            "symbol": order.instrument_id + "USDT",
            "side": order.side.value,
            "type": "MARKET",
            "quantity": _format_qty(order.quantity),
            "newClientOrderId": order.client_order_id,
        }
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
