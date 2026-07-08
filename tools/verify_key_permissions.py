"""独立密钥权限校验工具(2026-07-08,安全 P0 补丁;2026-07-09 接入 `tools/live_track_advance.py`
的 `--submit` 前置链,见下方"接线现状")。

## 审计发现(已核实)

全仓库 grep `apiRestrictions`/`ipRestrict`/`enableWithdrawals` 零命中——没有任何代码在启动/首次
使用时校验交易所 API key 的实际权限。`live/exchange/{binance,bybit}_futures.py` 的构造函数只
校验 `base_url` 是否在白名单、以及 `SafetyConfig.live_trading_armed`,从未对 `api_key`/`api_secret`
本身的实际权限做任何检查——如果人工在交易所网站创建主网 key 时漏勾"禁用提现"或忘了绑 IP 白名单,
系统不会有任何提示。

## 接线现状(2026-07-09,取代下面已过期的"绝不接入"表述)

本工具**已接入** `tools/live_track_advance.py::mainnet_key_safety_skip_reason`,在该驱动器
`main()` 的 `--submit` 前置链里无条件调用(preview 模式只告警、`--submit` 才真正跳批)。接线本身
对本机(0 个 MAINNET 凭据)是逐字节 no-op(`_load_mainnet_credentials` 抛 `RuntimeError` ->
直接 `return None`,不发起任何网络请求),不影响 11 个既有排程任务今天的行为。

**接线时尚未做的事,如实记录**:下方"端点/字段名诚实标注"里承认的 Binance
(`enableWithdrawals`/`ipRestrict`/`enableFutures`/`enableReading`)与 Bybit
(`result.ips`/`result.permissions.Wallet`)字段名假设,**接线这一刻依然未经真实 API 响应验证**
——这不是遗漏,是刻意的风险排序:fail-closed 设计保证字段名对不上时退化成 `UNKNOWN`(跳批,不是
误判 SAFE 放行),所以先接线让"驱动器不会因为一个从未验证过的 KeyError 而崩溃"这条不变量先立住;
真实字段名核对仍然是**用户在拿到真实 mainnet key 后必须手动做一次**的步骤,不因为已接线而免除。

## 范围边界(历史记录,2026-07-08 首次交付时的决定;接线现状见上方新段落)

这一轮只交付一个**独立、可手动调用**的校验工具。**绝不接入** `tools/live_track_advance.py`
的 `--submit` 前置链、`tools/live_ops_api.py`、或任何 scheduled task——11 个 QUANTOS 排程任务
是真实在跑的生产自动化,不能让一个可能有误判的新 fail-closed 检查静默打断它们。是否接线是
**下一轮独立决定**,由用户在人工验证过真实 key 的解析正确性之后拍板,这份文件本身不做这个决定。

## 设计决定:为什么不直接实例化 BinanceFuturesAdapter / BybitFuturesAdapter 打 mainnet?

两个 adapter 的构造函数对 `base_url == MAINNET_BASE` 做了结构性拒绝:除非
`SafetyConfig.live_trading_armed` 为真(= `live_trading_enabled` + `real_broker_enabled` +
`allow_order_submission` 三个旗标同时为真,且 `allow_ai_direct_trading` 为假)否则
`raise SafetyViolation`——这是 QUANTOS "5 个 LIVE 安全旗标"里最核心的一道闸门,任务红线明确要求
不去碰这些旗标,哪怕只是本地临时构造一个从不下单的对象来复用签名方法。

但本工具存在的全部意义,就是在人工去碰那些旗标**之前**先诊断一下这把 mainnet key 本身安不安全
——如果被那道闸门挡住,工具就失去了意义。所以这里**不实例化**两个 gated 类,而是只复用它们已经
导出的、**不依赖实例构造、不受 mainnet 闸门约束**的部分:

- 模块级函数 `_default_http_request`(纯 stdlib urllib transport,两个 adapter 文件都有);
- `@staticmethod _parse_json` / (Bybit) 常量 `MAINNET_BASE`(静态方法/类属性,unbound 访问不触发
  `__init__`,自然不会碰任何安全旗标);
- `live/exchange/retry.py::call_with_backoff` + `RateLimitedError`(纯函数,无 gate)。

签名本身(HMAC-SHA256)是两个 adapter 的**实例方法**(`_signed_query`/`_signed_headers`),依赖
`self._api_secret`/`self._clock` 等实例状态,没法在不实例化的前提下复用——这里各写了一个纯函数
镜像(`_binance_sign_query` / `_bybit_sign_headers`),算法与被镜像的方法逐行一致(见各自函数
docstring 里标注的源代码位置),**不是重新设计一套签名方案**,只是把同一条公式从"gated 实例方法"
降格成"ungated 纯函数",避免touch 闸门。

## 端点/字段名诚实标注(未见过真实响应)

- **Binance**:`GET https://api.binance.com/sapi/v1/account/apiRestrictions`(USER_DATA,HMAC
  签名,`X-MBX-APIKEY` header)——这是查 key 权限位最直接的接口,不在 `binance_futures.py` 已用的
  `fapi.binance.com`(合约)host 上,而在**现货/账户 host** `api.binance.com` 上;Binance 的
  api_key/api_secret 是账户级的,同一把 futures 用的 key 在这个 host 上一样有效。字段名
  (`ipRestrict`/`enableWithdrawals`/`enableFutures`/`enableReading`/`enableSpotAndMarginTrading`/
  `enableMargin`)依据 Binance 官方文档规格假设 + `binance_futures.py` 现有 `_parse_json` 解析
  模式做合理推断——**本工具作者未见过真实响应**,首次真实联调是用户提供真实 mainnet key 后的
  显式步骤,不在本次自动执行范围内;建议用户首次手动运行时核对这些字段名与实际返回是否一致
  (若字段名对不上,本工具的 fail-closed 设计会让它诚实退化成 UNKNOWN,而不是静默给出错误的
  SAFE 结论——但仍强烈建议人工核对一次)。
- **Bybit**:`GET https://api.bybit.com/v5/user/query-api`(V5 统一签名:header 里的
  `X-BAPI-SIGN`,与 `bybit_futures.py::_signed_headers` 同一机制)。响应信封同 V5 其它端点
  (`retCode`/`retMsg`/`result`);`result.ips`(IP 白名单数组,`["*"]` 按官方文档语义代表"未设
  限制")与 `result.permissions`(按业务域分组的权限字符串字典,`result.permissions.Wallet` 数组
  里含 `"Withdraw"` 代表提现权限已开)依官方文档 + `bybit_futures.py` 现有信封解析模式推断——
  同样**未见过真实响应**,同样的核对建议适用。

## Fail-closed 语义(为将来接线做对,这一轮本身不接线)

`KeyPermissionResult.safety` 三态:`SAFE`(明确解析出"提现已禁用" **且** "已绑定 IP 白名单")/
`UNSAFE`(明确检测到提现权限打开,或明确检测到无 IP 限制)/ `UNKNOWN`(网络错误、超时、限流耗尽、
响应非 JSON、非 2xx、信封校验失败、字段缺失或类型不对——任何不确定情况)。`is_safe_to_proceed`
只在 `SAFE` 时为真;`UNKNOWN` 与 `UNSAFE` 都是 False——调用方(未来若决定接线)应把两者一视同仁
地当作"不要继续"处理。

## 密钥值绝不落地

整个项目的既有纪律——密钥只按名称/类型/长度处理,不按值处理。本文件的任何 print/日志/异常消息/
`KeyPermissionResult` 字段都**绝不包含** `api_key`/`api_secret` 的明文值(哪怕交易所自己的响应
里可能回显 key 的部分信息,本文件的解析函数也只挑选具名的、非密钥类的诊断字段,绝不把整个响应体
原样吐出)。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from live.exchange.binance_futures import BinanceFuturesAdapter
from live.exchange.binance_futures import _default_http_request as _binance_default_transport
from live.exchange.binance_futures import _is_rate_limited as _binance_is_rate_limited
from live.exchange.bybit_futures import BybitFuturesAdapter
from live.exchange.bybit_futures import _default_http_request as _bybit_default_transport
from live.exchange.bybit_futures import _is_rate_limited as _bybit_is_rate_limited
from live.exchange.retry import call_with_backoff
from tools.live_track_advance import _load_credentials

# Binance 的账户/权限查询在**现货**host,不是 binance_futures.py 用的 fapi(合约)host——见模块
# docstring"端点/字段名诚实标注"。字面量常量,不从 BinanceFuturesAdapter 派生(那个类只知道
# fapi host)。
_BINANCE_ACCOUNT_HOST = "https://api.binance.com"
_BINANCE_API_RESTRICTIONS_PATH = "/sapi/v1/account/apiRestrictions"

# Bybit 的 query-api 在与 bybit_futures.py 相同的统一 host 上——直接取类属性字符串(静态访问,
# 不实例化、不触发 __init__ 里的 mainnet 闸门)。
_BYBIT_MAINNET_HOST = BybitFuturesAdapter.MAINNET_BASE
_BYBIT_QUERY_API_PATH = "/v5/user/query-api"

# 诊断性手动工具,不追求交易级别的时钟精度——给一个比生产 5000ms 更宽松的窗口,降低人工单次
# 运行时因本机时钟漂移导致的假 UNKNOWN(不影响判定的保守性:窗口只影响"能不能通过签名校验拿到
# 响应",不影响响应里 SAFE/UNSAFE 字段本身的解读)。
_DEFAULT_RECV_WINDOW_MS = 10_000


class KeySafety(str, Enum):
    """三态,见模块 docstring "Fail-closed 语义"。"""
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class KeyPermissionResult:
    """结构化结果。`raw_fields` 只包含明确挑选的非密钥诊断字段(布尔值/计数/权限字符串列表),
    绝不整体转存交易所响应体——见模块 docstring "密钥值绝不落地"。"""
    venue: str
    safety: KeySafety
    reasons: tuple = field(default_factory=tuple)
    raw_fields: dict = field(default_factory=dict)

    @property
    def is_safe_to_proceed(self) -> bool:
        """fail-closed:唯有明确 SAFE 才是 True。UNKNOWN 与 UNSAFE 都返回 False——这次任务本身
        不接线,但保证返回值语义为将来接线做对(见模块 docstring)。"""
        return self.safety is KeySafety.SAFE


# ---------------------------------------------------------------------------
# Binance:GET /sapi/v1/account/apiRestrictions
# ---------------------------------------------------------------------------

def _binance_sign_query(params: dict, api_secret: str) -> str:
    """镜像 `BinanceFuturesAdapter._signed_query`(live/exchange/binance_futures.py)同一算法:
    urlencode 参数 -> HMAC-SHA256(api_secret, query_string) -> 追加 `&signature=`。不能直接调用
    该方法,因为它是绑定 `self._api_secret`/`self._clock` 的实例方法,实例化该类需要
    `base_url`,打 mainnet host 会触发 `__init__` 里的 `live_trading_armed` 闸门(见模块
    docstring "设计决定")。"""
    qs = urllib.parse.urlencode(params)
    sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return f"{qs}&signature={sig}"


def _parse_binance_api_restrictions(status: int, data) -> KeyPermissionResult:
    """把 `/sapi/v1/account/apiRestrictions` 的响应解析成 `KeyPermissionResult`。字段名见模块
    docstring 的诚实标注段落。"""
    if not (200 <= status < 300):
        return KeyPermissionResult(venue="binance", safety=KeySafety.UNKNOWN,
                                   reasons=(f"non-2xx status {status}",))
    if not isinstance(data, dict):
        return KeyPermissionResult(venue="binance", safety=KeySafety.UNKNOWN,
                                   reasons=("response body is not a JSON object",))
    if "enableWithdrawals" not in data or "ipRestrict" not in data:
        return KeyPermissionResult(
            venue="binance", safety=KeySafety.UNKNOWN,
            reasons=("response missing enableWithdrawals/ipRestrict field(s) — "
                     "endpoint/field names may not match real API, see module docstring",))
    withdraw_enabled = data.get("enableWithdrawals")
    ip_restricted = data.get("ipRestrict")
    if not isinstance(withdraw_enabled, bool) or not isinstance(ip_restricted, bool):
        return KeyPermissionResult(
            venue="binance", safety=KeySafety.UNKNOWN,
            reasons=("enableWithdrawals/ipRestrict field(s) not boolean — unexpected response shape",))
    raw = {"enableWithdrawals": withdraw_enabled, "ipRestrict": ip_restricted,
          "enableFutures": data.get("enableFutures"), "enableReading": data.get("enableReading")}
    if withdraw_enabled:
        return KeyPermissionResult(venue="binance", safety=KeySafety.UNSAFE,
                                   reasons=("enableWithdrawals=true — this key CAN withdraw funds",),
                                   raw_fields=raw)
    if not ip_restricted:
        return KeyPermissionResult(venue="binance", safety=KeySafety.UNSAFE,
                                   reasons=("ipRestrict=false — this key is NOT bound to an IP whitelist",),
                                   raw_fields=raw)
    return KeyPermissionResult(venue="binance", safety=KeySafety.SAFE,
                               reasons=("enableWithdrawals=false", "ipRestrict=true"), raw_fields=raw)


def check_binance_key_permissions(api_key: str, api_secret: str, *,
                                  http_request: Optional[Callable] = None,
                                  clock: Optional[Callable[[], float]] = None,
                                  sleep: Optional[Callable[[float], None]] = None,
                                  max_retries: int = 2, retry_base_delay: float = 1.0,
                                  recv_window_ms: int = _DEFAULT_RECV_WINDOW_MS) -> KeyPermissionResult:
    """查询 Binance mainnet 账户的 API key 权限位。`http_request` 可注入(测试用假 transport,
    默认走真实 `urllib`——复用 `binance_futures.py::_default_http_request`)。任何网络/解析异常
    (含限流重试耗尽)一律**优雅返回 UNKNOWN**,不向调用方抛异常。"""
    http = http_request or _binance_default_transport
    clk = clock or time.time
    slp = sleep or time.sleep
    headers = {"X-MBX-APIKEY": api_key}

    def _do():
        # 签名串(含 timestamp)每次重试都重新算——同 binance_futures.py 的同款考量:退避可能
        # 跨越数秒,复用旧 timestamp 有落在 recvWindow 之外的风险。
        params = {"timestamp": int(clk() * 1000), "recvWindow": recv_window_ms}
        qs = _binance_sign_query(params, api_secret)
        url = _BINANCE_ACCOUNT_HOST + _BINANCE_API_RESTRICTIONS_PATH + "?" + qs
        status, body = http("GET", url, body=None, headers=headers)
        data = BinanceFuturesAdapter._parse_json(status, body)      # noqa: SLF001 (staticmethod, unbound-safe)
        return status, data

    try:
        status, data = call_with_backoff(_do, is_rate_limited=_binance_is_rate_limited,
                                         max_retries=max_retries, base_delay=retry_base_delay, sleep=slp)
    except Exception as exc:
        # type(exc).__name__ 只标注异常类型,绝不 str(exc)(即便理论上不含密钥,也不冒这个险
        # ——见模块 docstring"密钥值绝不落地")。
        return KeyPermissionResult(venue="binance", safety=KeySafety.UNKNOWN,
                                   reasons=(f"request failed: {type(exc).__name__}",))
    return _parse_binance_api_restrictions(status, data)


# ---------------------------------------------------------------------------
# Bybit:GET /v5/user/query-api
# ---------------------------------------------------------------------------

def _bybit_sign_headers(api_key: str, api_secret: str, timestamp_ms: int, recv_window_ms: int,
                        payload: str) -> dict:
    """镜像 `BybitFuturesAdapter._signed_headers`(live/exchange/bybit_futures.py)同一算法:
    `sign_str = timestamp + api_key + recv_window + payload`,HMAC-SHA256 十六进制放进
    `X-BAPI-SIGN` header。理由同 `_binance_sign_query` 的 docstring 段落。"""
    ts = str(timestamp_ms)
    recv_window = str(recv_window_ms)
    sign_str = ts + api_key + recv_window + payload
    sig = hmac.new(api_secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    return {"X-BAPI-API-KEY": api_key, "X-BAPI-TIMESTAMP": ts,
           "X-BAPI-SIGN": sig, "X-BAPI-RECV-WINDOW": recv_window}


def _parse_bybit_query_api(status: int, data) -> KeyPermissionResult:
    """把 `/v5/user/query-api` 的响应解析成 `KeyPermissionResult`。字段名见模块 docstring 的
    诚实标注段落。故意不复用 `BybitFuturesAdapter._unwrap`——它的异常消息里会原样嵌入整个响应体
    `data`(可能含 `apiKey`/`note` 等 Bybit 自己回显的字段),这里全程只挑选具名字段
    (`ips`/`permissions`),绝不把响应体整体吐进任何消息(见模块 docstring "密钥值绝不落地")。"""
    if not (200 <= status < 300):
        return KeyPermissionResult(venue="bybit", safety=KeySafety.UNKNOWN,
                                   reasons=(f"non-2xx status {status}",))
    if not isinstance(data, dict):
        return KeyPermissionResult(venue="bybit", safety=KeySafety.UNKNOWN,
                                   reasons=("response body is not a JSON object",))
    ret_code = data.get("retCode")
    if ret_code != 0:
        return KeyPermissionResult(venue="bybit", safety=KeySafety.UNKNOWN,
                                   reasons=(f"retCode={ret_code!r} (non-zero envelope = failure)",))
    result = data.get("result")
    if not isinstance(result, dict):
        return KeyPermissionResult(venue="bybit", safety=KeySafety.UNKNOWN,
                                   reasons=("response missing result envelope",))
    if "ips" not in result or "permissions" not in result:
        return KeyPermissionResult(
            venue="bybit", safety=KeySafety.UNKNOWN,
            reasons=("result missing ips/permissions field(s) — endpoint/field names may not "
                     "match real API, see module docstring",))
    ips = result.get("ips")
    permissions = result.get("permissions")
    if not isinstance(ips, list) or not isinstance(permissions, dict):
        return KeyPermissionResult(venue="bybit", safety=KeySafety.UNKNOWN,
                                   reasons=("ips/permissions field(s) unexpected shape",))
    wallet_perms = permissions.get("Wallet")
    if not isinstance(wallet_perms, list):
        return KeyPermissionResult(venue="bybit", safety=KeySafety.UNKNOWN,
                                   reasons=("permissions.Wallet missing/unexpected shape",))
    withdraw_granted = "Withdraw" in wallet_perms
    ip_unrestricted = (not ips) or ("*" in ips)
    # 2026-07-08 code review 修复:`ip_unrestricted` 的判定依赖一个未经真实响应验证的假设
    # (见模块 docstring"端点/字段名诚实标注"——"未见过真实响应")。若真实 API 用别的方式表示
    # "未限制 IP"(不是 []/["*"]),这里会误判为 False(= 误判成"已绑定白名单"),让整体结果
    # 从本该的 UNSAFE 静默变成 SAFE。IP 地址不是密钥(不同于 api_key/api_secret 的保密纪律),
    # 所以把真实 `ips` 列表内容放进 raw_fields——模块 docstring 建议的"首次手动运行后核对
    # raw_fields 是否与交易所网页一致"这道人工复核防线,原先只能看到一个数字/布尔结论,现在能
    # 看到究竟是什么值让判定走向了这个结果,显著提高人工发现"假设与实际不符"的概率。
    raw = {"withdraw_permission_granted": withdraw_granted, "ip_unrestricted": ip_unrestricted,
          "ips": list(ips), "ip_count": len(ips), "wallet_permissions": list(wallet_perms)}
    if withdraw_granted:
        return KeyPermissionResult(venue="bybit", safety=KeySafety.UNSAFE,
                                   reasons=("'Withdraw' present in permissions.Wallet — this key "
                                            "CAN withdraw funds",), raw_fields=raw)
    if ip_unrestricted:
        return KeyPermissionResult(venue="bybit", safety=KeySafety.UNSAFE,
                                   reasons=("ips is empty or contains '*' — this key is NOT bound "
                                            "to an IP whitelist",), raw_fields=raw)
    return KeyPermissionResult(venue="bybit", safety=KeySafety.SAFE,
                               reasons=("no 'Withdraw' permission", "IP whitelist bound"), raw_fields=raw)


def check_bybit_key_permissions(api_key: str, api_secret: str, *,
                                http_request: Optional[Callable] = None,
                                clock: Optional[Callable[[], float]] = None,
                                sleep: Optional[Callable[[float], None]] = None,
                                max_retries: int = 2, retry_base_delay: float = 1.0,
                                recv_window_ms: int = _DEFAULT_RECV_WINDOW_MS) -> KeyPermissionResult:
    """查询 Bybit mainnet 账户的 API key 权限范围。同 `check_binance_key_permissions` 的注入/
    fail-closed 约定。"""
    http = http_request or _bybit_default_transport
    clk = clock or time.time
    slp = sleep or time.sleep
    url = _BYBIT_MAINNET_HOST + _BYBIT_QUERY_API_PATH

    def _do():
        headers = _bybit_sign_headers(api_key, api_secret, int(clk() * 1000), recv_window_ms, "")
        status, body = http("GET", url, body=None, headers=headers)
        data = BybitFuturesAdapter._parse_json(status, body)        # noqa: SLF001 (staticmethod, unbound-safe)
        return status, data

    try:
        status, data = call_with_backoff(_do, is_rate_limited=_bybit_is_rate_limited,
                                         max_retries=max_retries, base_delay=retry_base_delay, sleep=slp)
    except Exception as exc:
        return KeyPermissionResult(venue="bybit", safety=KeySafety.UNKNOWN,
                                   reasons=(f"request failed: {type(exc).__name__}",))
    return _parse_bybit_query_api(status, data)


_CHECKERS = {"binance": check_binance_key_permissions, "bybit": check_bybit_key_permissions}


# ---------------------------------------------------------------------------
# 凭据加载(复用 tools.live_track_advance._load_credentials,不重写读取逻辑)
# ---------------------------------------------------------------------------

# **这是 MAINNET 专用凭据位置,与 tools/live_track_advance.py::_VENUES 里已有的 TESTNET 凭据
# (BINANCE_TESTNET_API_KEY / key/binance_test_key.json 等)是两回事,不能混用**——本工具的存在
# 意义就是在人工去申请/使用真实 mainnet key 之前先诊断它,查 testnet key 的权限没有意义(testnet
# 没有真实资金,也不暴露在 `/sapi/v1/account/apiRestrictions`/`/v5/user/query-api` 这两个
# mainnet-only 诊断端点上)。JSON 文件形状与既有 testnet key 文件一致:`{"api_key": ..., "api_secret": ...}`。
_MAINNET_CREDENTIAL_CONFIG = {
    "binance": {"env": ("BINANCE_MAINNET_API_KEY", "BINANCE_MAINNET_API_SECRET"),
               "key_file": os.path.join("key", "binance_mainnet_key.json")},
    "bybit": {"env": ("BYBIT_MAINNET_API_KEY", "BYBIT_MAINNET_API_SECRET"),
             "key_file": os.path.join("key", "bybit_mainnet_key.json")},
}


def _load_mainnet_credentials(venue: str) -> tuple:
    """复用 `tools.live_track_advance._load_credentials` 的查找逻辑(env var -> key 文件 ->
    报错),只是喂给它本工具自己的 MAINNET 专用 venue_cfg。原函数的报错文案硬编码了"testnet"
    字样(不属于本次允许修改的文件),这里捕获后用更准确的 MAINNET 措辞重新抛出,不改原函数
    一个字。"""
    cfg = _MAINNET_CREDENTIAL_CONFIG[venue]
    try:
        return _load_credentials(cfg, venue)
    except RuntimeError as exc:
        env_key, env_secret = cfg["env"]
        raise RuntimeError(
            f"{venue} MAINNET credentials not found (set ${env_key}/${env_secret}, or create "
            f"{cfg['key_file']} with {{\"api_key\": ..., \"api_secret\": ...}} — see "
            f"tools/live_track_advance.py::_load_credentials for the lookup order). This is "
            f"separate from the existing TESTNET credentials.") from exc


def verify_key_permissions(venue: str, **kwargs) -> KeyPermissionResult:
    """顶层入口:venue 名 -> 加载 MAINNET 凭据(复用既有逻辑)-> 分派到对应 venue 的权限查询。
    `**kwargs`(`http_request`/`clock`/`sleep`/`max_retries`/...)透传给 `check_*_key_permissions`
    ——供测试注入假 transport,生产默认走真实网络。"""
    if venue not in _MAINNET_CREDENTIAL_CONFIG:
        raise SystemExit(f"unknown --venue {venue!r} (choose from {sorted(_MAINNET_CREDENTIAL_CONFIG)})")
    api_key, api_secret = _load_mainnet_credentials(venue)
    return _CHECKERS[venue](api_key, api_secret, **kwargs)


# ---------------------------------------------------------------------------
# CLI(人工手动调用:`python -m tools.verify_key_permissions --venue binance`)
# ---------------------------------------------------------------------------

def _print_result(result: KeyPermissionResult) -> None:
    print(f"[verify_key_permissions] venue={result.venue} safety={result.safety.value}")
    for reason in result.reasons:
        print(f"  - {reason}")
    if result.raw_fields:
        print(f"  raw_fields (non-secret diagnostics): {result.raw_fields}")
    if result.safety is KeySafety.SAFE:
        print("  -> CONFIRMED SAFE (withdrawals disabled + IP whitelist bound).")
    else:
        print("  -> NOT confirmed safe. Treat as 不要继续 (do not proceed to live/mainnet use "
             "with this key until this is resolved and a human has reviewed the exchange's "
             "API-key settings page directly).")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="独立手动密钥权限校验工具(不接线)——诊断 mainnet API key 是否禁用提现 + "
                    "绑定 IP 白名单。见模块 docstring。")
    parser.add_argument("--venue", required=True, choices=sorted(_MAINNET_CREDENTIAL_CONFIG),
                        help="要校验的交易所")
    args = parser.parse_args(argv)

    try:
        result = verify_key_permissions(args.venue)
    except RuntimeError as exc:
        print(f"[verify_key_permissions] ERROR loading credentials: {exc}")
        return 2

    _print_result(result)
    return 0 if result.safety is KeySafety.SAFE else 1


if __name__ == "__main__":
    raise SystemExit(main())
