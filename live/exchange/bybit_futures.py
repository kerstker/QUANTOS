"""Bybit V5 统一账户(USDT 永续,linear category)签名 REST 客户端(Stage 1.5)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §10。

纯 stdlib(urllib/hmac/hashlib/json/time)——绝不 import 交易所 SDK(pybit 等)。与
`binance_futures.py` 满足同一个 `VenueAdapter` Protocol,但底层协议有真实差异(2026-07-04 研究
Bybit V5 公开文档得出,尚未对真测试网响应验证——首次真实联调是用户提供 Bybit 测试网 API key 后的
显式步骤,不在本次自动执行范围内):

1. **签名在 header,不在 query/body 里**:`sign_str = timestamp + api_key + recv_window + query_or_body`,
   HMAC-SHA256 十六进制放进 `X-BAPI-SIGN` header(Binance 是签名串追加进 query string)。
2. **响应信封**:所有端点统一 `{retCode, retMsg, result, time}`,`retCode == 0` 才是成功;非 2xx
   HTTP 状态或非零 retCode 都视为失败。
3. **Hedge Mode 用 `positionIdx` 字段**(0=one-way,1=多头腿,2=空头腿),不是像 Binance 那样在
   `positions` 数组里出现两条独立记录区分——但账户仍可能被切到 hedge mode,`positions()` 依然按
   标的**累加净数量**做防御(镜像 Binance 那次 hedge-mode 覆盖 bug 修复的同一姿态)。本系统假设
   one-way 模式,下单固定 `positionIdx=0`。
4. **订单查询分「未结」与「历史」两个端点**(`/v5/order/realtime` 只返回未成交/部分成交;已终态的
   单子需要查 `/v5/order/history`)——不像 Binance 一个 `/fapi/v1/order` 端点通吃。`get_order` 先查
   realtime、找不到再查 history,两边都空才是真正"不存在"。这个假设未经真实验证,标注在此。

只实现 MARKET 单;提供撤单(`cancel_order`,2026-07-05 OMS 补全;注意 Bybit 撤单 ACK 只回
`{orderId, orderLinkId}` 不带最终状态——ACK 仅表示"请求被受理",不保证已撤成,调用方必须
`get_order` 回查权威状态);不提供改单/杠杆配置;绝不提供提幣(withdraw)/划转(transfer)。
mainnet 结构性拒绝,与 `BinanceFuturesAdapter`/`services/trading/broker_adapter.py::
LiveBrokerAdapter` 对称。

**状态归一化(2026-07-05):** Bybit 原样状态("Filled"/"Cancelled"/"PartiallyFilledCanceled"…)
与流水线 `_TERMINAL_ORDER_STATUSES` 的规范大写集合(Binance 风格)不匹配——不归一化的话订单
永远"确认不了"(轮询打满也见不到终态)。`get_order` 统一映射到规范集合;未知状态原样透传
(诚实,流水线自然当非终态处理——安全默认)。

**运维硬化(2026-07-04,§12,与 binance_futures.py 同款):** HTTP 429 或业务错误码 `retCode==10006`
("Too many visits!")都视为限流信号,`live/exchange/retry.py::call_with_backoff` 指数退避重试,
耗尽仍限流才 `RateLimitedError`。`sync_clock()` 拉取 `/v5/market/time` 校正本机时钟偏移,
fail-soft(失败保留旧偏移)。
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

_RATE_LIMIT_RETCODE = 10006
_CANCEL_NOT_EXISTS_RETCODE = 110001    # "order not exists or too late to cancel"

# Bybit V5 orderStatus -> 流水线规范状态(见模块 docstring"状态归一化")
_STATUS_NORMALIZE = {
    "Created": "NEW", "New": "NEW", "Untriggered": "NEW", "Triggered": "NEW",
    "PartiallyFilled": "PARTIALLY_FILLED",
    "Filled": "FILLED",
    "Cancelled": "CANCELED", "PartiallyFilledCanceled": "CANCELED",
    "Rejected": "REJECTED",
    "Deactivated": "EXPIRED",
}


def _is_rate_limited(result: tuple) -> bool:
    status, data = result
    if status == 429:
        return True
    return isinstance(data, dict) and data.get("retCode") == _RATE_LIMIT_RETCODE


def _format_qty(qty: float) -> str:
    """浮点数量 -> Bybit 期望的十进制字符串(绝不用科学计数法;去尾随零,保留整数形式)。"""
    s = f"{qty:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _default_http_request(method: str, url: str, *, body: Optional[bytes] = None,
                          headers: Optional[dict] = None) -> tuple:
    """生产默认 transport:urllib,15s 超时。非 2xx 通过 HTTPError 捕获,原样把 (status, body) 交回
    上层解析。"""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class BybitFuturesAdapter:
    TESTNET_BASE = "https://api-testnet.bybit.com"
    # Demo Trading(2026-07-05):testnet 水龙头长期失灵,Bybit 官方主推的模拟环境是主站
    # Demo(api-demo)——V5 协议与 testnet 完全一致,只换主机;零真实资本,治理上与 testnet
    # 同类(venue_mode 同为 "testnet"),不受 mainnet 门禁管辖、也绝不放宽 mainnet 门禁。
    DEMO_BASE = "https://api-demo.bybit.com"
    MAINNET_BASE = "https://api.bybit.com"
    venue_name = "bybit"       # mandate 交易所域匹配(mandate.exchange 必须等于它,2026-07-05)
    _RECV_WINDOW = "5000"

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
        elif base_url in (self.TESTNET_BASE, self.DEMO_BASE):
            self.venue_mode = "testnet"        # demo 与 testnet 同为零真实资本的模拟环境
        else:
            raise ValueError(
                f"unknown base_url {base_url!r} — only TESTNET_BASE/DEMO_BASE/MAINNET_BASE permitted")
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
        """拉取 `/v5/market/time`(公开、不签名),计算本机时钟相对服务器的偏移(ms)——fail-soft,
        失败保留旧偏移(默认 0),不打断流程。同 binance_futures.py 的同款硬化。"""
        try:
            status, data = self._get("/v5/market/time", signed=False)
            if 200 <= status < 300 and isinstance(data, dict) and "time" in data:
                self._clock_offset_ms = int(data["time"]) - int(self._clock() * 1000)
        except Exception:
            pass
        return self._clock_offset_ms

    # --- 签名核心(header-based,与 Binance 的 query-embedded 不同) ---

    def _signed_headers(self, payload: str) -> dict:
        ts = str(int(self._clock() * 1000) + self._clock_offset_ms)
        sign_str = ts + self._api_key + self._RECV_WINDOW + payload
        sig = hmac.new(self._api_secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        return {"X-BAPI-API-KEY": self._api_key, "X-BAPI-TIMESTAMP": ts,
               "X-BAPI-SIGN": sig, "X-BAPI-RECV-WINDOW": self._RECV_WINDOW}

    @staticmethod
    def _parse_json(status: int, body: bytes) -> dict:
        """反向代理/网关故障或限流页面可能返回非 JSON body——统一成清晰的 RuntimeError,而不是让
        未处理的 json.JSONDecodeError 冒泡到调用方(同 binance_futures.py 的诚实修复)。"""
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            snippet = body[:200] if isinstance(body, (bytes, bytearray)) else str(body)[:200]
            raise RuntimeError(f"non-JSON response (status {status}): {snippet!r}") from exc

    def _get(self, path: str, params: Optional[dict] = None, *, signed: bool = True):
        params = params or {}
        qs = urllib.parse.urlencode(params)
        url = self._base_url + path + (f"?{qs}" if qs else "")

        def _do():
            # header 每次重试都重新签(含 timestamp)——同 binance_futures.py 的同款考量:退避可能
            # 跨越数秒,复用旧 timestamp 有落在 recv_window 之外的风险。
            headers = self._signed_headers(qs) if signed else {}
            status, body = self._http("GET", url, body=None, headers=headers)
            return status, self._parse_json(status, body)
        return call_with_backoff(_do, is_rate_limited=_is_rate_limited, max_retries=self._max_retries,
                                 base_delay=self._retry_base_delay, sleep=self._sleep)

    def _post_signed(self, path: str, params: dict):
        body_str = json.dumps(params, separators=(",", ":"))
        url = self._base_url + path

        def _do():
            headers = self._signed_headers(body_str)
            headers["Content-Type"] = "application/json"
            status, body = self._http("POST", url, body=body_str.encode(), headers=headers)
            return status, self._parse_json(status, body)
        return call_with_backoff(_do, is_rate_limited=_is_rate_limited, max_retries=self._max_retries,
                                 base_delay=self._retry_base_delay, sleep=self._sleep)

    @staticmethod
    def _unwrap(status: int, data: dict, *, action: str) -> dict:
        """统一信封校验:非 2xx 或 retCode!=0 都是失败(fail-closed,不放行含糊的部分成功)。"""
        if not (200 <= status < 300) or not isinstance(data, dict) or data.get("retCode") != 0:
            raise RuntimeError(f"{action} failed: {status} {data}")
        return data.get("result", {}) or {}

    # --- 公开(无签名) ---

    def exchange_filters(self) -> dict:
        status, data = self._get("/v5/market/instruments-info", {"category": "linear"}, signed=False)
        result = self._unwrap(status, data, action="exchange_filters")
        out: dict = {}
        for sym in result.get("list", []):
            symbol = sym.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            lot = sym.get("lotSizeFilter")
            if lot is None:
                continue                                        # 缺关键过滤器 -> 诚实丢弃,不硬凑
            try:
                step_size = float(lot["qtyStep"])
                min_qty = float(lot["minOrderQty"])
                min_notional = float(lot.get("minNotionalValue", 0) or 0)
            except (KeyError, TypeError, ValueError):
                continue
            tick_size = None
            price_filter = sym.get("priceFilter")
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
        status, data = self._get("/v5/account/wallet-balance", {"accountType": "UNIFIED"}, signed=True)
        self._unwrap(status, data, action="account")           # 校验失败即 raise;成功时保留原始信封
        return data

    def positions(self) -> dict:
        """规范化仓位。one-way 模式下每个标的通常只有一条记录(`side` Buy/Sell 决定符号);账户若被
        切到 hedge mode,同一标的会有 `positionIdx` 1/2 两条腿——按标的累加净数量(镜像
        `binance_futures.py::positions` 的 hedge-mode 覆盖 bug 修复姿态,防止静默丢失一侧敞口)。"""
        status, data = self._get("/v5/position/list",
                                 {"category": "linear", "settleCoin": "USDT"}, signed=True)
        result = self._unwrap(status, data, action="positions")
        legs: dict = {}                                          # inst -> (net_qty, dominant_entry, dominant_abs)
        for p in result.get("list", []):
            symbol = p.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            try:
                size = float(p.get("size", 0) or 0)
            except (TypeError, ValueError):
                continue
            if size == 0.0:
                continue
            side = p.get("side")
            signed_qty = size if side == "Buy" else -size if side == "Sell" else None
            if signed_qty is None:
                continue                                         # side 未知,诚实跳过不硬猜方向
            try:
                entry = float(p.get("avgPrice", 0) or 0)
            except (TypeError, ValueError):
                entry = 0.0
            inst = symbol[:-4]
            prev_qty, prev_entry, prev_abs = legs.get(inst, (0.0, 0.0, 0.0))
            abs_qty = abs(signed_qty)
            dominant_entry, dominant_abs = (entry, abs_qty) if abs_qty >= prev_abs else (prev_entry, prev_abs)
            legs[inst] = (prev_qty + signed_qty, dominant_entry, dominant_abs)
        return {inst: VenuePosition(instrument_id=inst, quantity=q, entry_price=e)
               for inst, (q, e, _) in legs.items() if q != 0.0}

    def place_order(self, order: LiveOrderRequest) -> VenueOrderResult:
        params = {
            "category": "linear",
            "symbol": order.instrument_id + "USDT",
            "side": "Buy" if order.side.value == "BUY" else "Sell",
            "orderType": "Market",
            "qty": _format_qty(order.quantity),
            "positionIdx": 0,                                    # one-way 模式假设,见模块 docstring
            "orderLinkId": order.client_order_id,
        }
        if order.reduce_only:
            params["reduceOnly"] = True          # 仅紧急减仓通道;普通单不带,保持请求形状不变
        status, data = self._post_signed("/v5/order/create", params)
        result = self._unwrap(status, data, action="place_order")
        return VenueOrderResult(
            venue_order_id=str(result.get("orderId", "")),
            client_order_id=result.get("orderLinkId", order.client_order_id),
            instrument_id=order.instrument_id, status="NEW",     # 创建响应本身不带成交状态字段
            filled_qty=0.0, avg_price=0.0, raw=data)

    def _query_orders(self, path: str, instrument_id: str, client_order_id: str) -> Optional[dict]:
        params = {"category": "linear", "symbol": instrument_id + "USDT", "orderLinkId": client_order_id}
        status, data = self._get(path, params, signed=True)
        result = self._unwrap(status, data, action=f"get_order({path})")
        items = result.get("list", []) or []
        return items[0] if items else None

    def get_order(self, instrument_id: str, client_order_id: str) -> Optional[VenueOrderResult]:
        """先查未结订单(`/v5/order/realtime`),找不到再查历史订单(`/v5/order/history`)——Bybit V5
        把这两类查询分成不同端点,不像 Binance 一个端点通吃。两边都空 -> 订单确实不存在,返回 None。
        """
        item = self._query_orders("/v5/order/realtime", instrument_id, client_order_id)
        if item is None:
            item = self._query_orders("/v5/order/history", instrument_id, client_order_id)
        if item is None:
            return None
        raw_status = item.get("orderStatus", "UNKNOWN")
        return VenueOrderResult(
            venue_order_id=str(item.get("orderId", "")),
            client_order_id=item.get("orderLinkId", client_order_id),
            instrument_id=instrument_id,
            status=_STATUS_NORMALIZE.get(raw_status, raw_status),   # 未知状态原样透传(诚实)
            filled_qty=float(item.get("cumExecQty", 0) or 0),
            avg_price=float(item.get("avgPrice", 0) or 0), raw=item)

    def cancel_order(self, instrument_id: str, client_order_id: str) -> Optional[VenueOrderResult]:
        """撤单。retCode 110001("order not exists or too late to cancel")-> 语义化 None——撤单与
        成交赛跑的正常路径(订单可能已成交),调用方回查 get_order 定论。成功 ACK 只回
        {orderId, orderLinkId} 不带最终状态——诚实返回非终态 UNKNOWN,绝不冒充 CANCELED。"""
        params = {"category": "linear", "symbol": instrument_id + "USDT",
                 "orderLinkId": client_order_id}
        status, data = self._post_signed("/v5/order/cancel", params)
        if isinstance(data, dict) and data.get("retCode") == _CANCEL_NOT_EXISTS_RETCODE:
            return None
        result = self._unwrap(status, data, action="cancel_order")
        return VenueOrderResult(
            venue_order_id=str(result.get("orderId", "")),
            client_order_id=result.get("orderLinkId", client_order_id),
            instrument_id=instrument_id, status="UNKNOWN",
            filled_qty=0.0, avg_price=0.0, raw=data)
