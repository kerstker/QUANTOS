"""MEXC 现货凭证加载器 + 只读启动校验(docs/MEXC_INTEGRATION.md §10)。

两个职责,都不触碰下单路径、绝不打印/落盘 secret 值本身:

1. `load_mexc_credentials` —— env(`MEXC_API_KEY`/`MEXC_API_SECRET`)优先、
   `key/mexc_spot.json` 兜底(形状 `{"api_key", "api_secret"}`)。只做形状校验:
   非空字符串、文件声明的 base_url 必须在白名单内(唯一合法值 = MEXC mainnet,
   MEXC 无现货 testnet)。`key/` gitignored 承诺不变——本文件只写加载逻辑,绝不写真实 key。
2. `check_mexc_key_safety` / `fetch_mexc_account` —— 启动时以签名
   `GET /api/v3/account` 做只读可达性校验,并核对账户响应的权限位:只接受
   「提现权限已在交易所侧关闭」(`canWithdraw=False`)且现货交易开着
   (`canTrade=True`)的 key。字段缺失/网络失败/任何不确定 → 不 safe
   (fail-closed),与 tools/verify_key_permissions.py 的三态哲学同构
   (该文件的 MEXC 分支直接复用本模块)。

**不实例化 MexcSpotAdapter**:mainnet base 在默认 SafetyConfig 下构造即
SafetyViolation,而本模块的全部意义就是在人工 `--arm-live` 之前先诊断这把 key
——与 verify_key_permissions 对 Binance/Bybit 的「ungated 纯函数镜像」同一设计
(签名算法逐行镜像 `MexcSpotAdapter._signed_query`,不重新设计一套签名)。

诚实标注:`canTrade`/`canWithdraw`/`balances[].free` 字段名依 MEXC 公开 v3 文档
(Binance 兼容)假设,**未对真实 mainnet 响应验证**——缺失或类型不符时诚实落入
「不 safe」,绝不默认 SAFE;首验由人工跑 tools/mexc_readonly_smoke.py 完成。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from live.exchange.retry import call_with_backoff

MEXC_MAINNET_BASE = "https://api.mexc.com"
MEXC_ENV = ("MEXC_API_KEY", "MEXC_API_SECRET")
MEXC_KEY_FILE = os.path.join("key", "mexc_spot.json")
_ACCOUNT_PATH = "/api/v3/account"
# 诊断性只读调用,给比生产 5000ms 更宽松的签名窗口(与 verify_key_permissions 同款考量)。
_DEFAULT_RECV_WINDOW_MS = 10_000
_RATE_LIMIT_STATUSES = frozenset({429, 418})


class MexcCredentialError(RuntimeError):
    """凭证缺失/形状非法/文件声明了非白名单 base_url——全部 fail-closed。"""


def _validate_shape(api_key, api_secret, *, source: str) -> tuple:
    if not isinstance(api_key, str) or not isinstance(api_secret, str):
        raise MexcCredentialError(f"{source}: api_key/api_secret must be strings")
    k, s = api_key.strip(), api_secret.strip()
    if not k or not s:
        raise MexcCredentialError(f"{source}: api_key/api_secret must be non-empty")
    return k, s


def load_mexc_credentials(*, env: tuple = MEXC_ENV, key_file: str = MEXC_KEY_FILE) -> tuple:
    """env 优先、key 文件兜底。返回 (api_key, api_secret);绝不记录/打印值本身。"""
    api_key = os.environ.get(env[0])
    api_secret = os.environ.get(env[1])
    if api_key and api_secret:
        return _validate_shape(api_key, api_secret, source=f"${env[0]}/${env[1]}")
    if os.path.exists(key_file):
        try:
            with open(key_file, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            raise MexcCredentialError(
                f"{key_file} unreadable/malformed: {type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise MexcCredentialError(f"{key_file} must be a JSON object")
        base_url = data.get("base_url")
        if base_url is not None and base_url != MEXC_MAINNET_BASE:
            raise MexcCredentialError(
                f"{key_file} declares non-whitelisted base_url {base_url!r} "
                "(only https://api.mexc.com is permitted; MEXC has no spot testnet)")
        k, s = data.get("api_key"), data.get("api_secret")
        if k and s:
            return _validate_shape(k, s, source=key_file)
    raise MexcCredentialError(
        f"MEXC credentials not found (set ${env[0]}/${env[1]}, or create {key_file} "
        '{"api_key": ..., "api_secret": ...} — withdraw/transfer must be DISABLED at the '
        "MEXC console, see docs/MEXC_INTEGRATION.md §10)")


# ---------------------------------------------------------------------------
# 只读账户查询(签名 GET /api/v3/account)——ungated 纯函数镜像,不实例化 gated 适配器
# ---------------------------------------------------------------------------

def _default_http_request(method: str, url: str, *, body: Optional[bytes] = None,
                          headers: Optional[dict] = None) -> tuple:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _sign_query(params: dict, api_secret: str) -> str:
    """逐行镜像 `MexcSpotAdapter._signed_query`(live/exchange/mexc_spot.py)同一算法:
    urlencode 参数 -> HMAC-SHA256(api_secret, query_string) -> 追加 `&signature=`。"""
    qs = urllib.parse.urlencode(params)
    sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return f"{qs}&signature={sig}"


def _is_rate_limited(result: tuple) -> bool:
    status, _data = result
    return status in _RATE_LIMIT_STATUSES


def fetch_mexc_account(api_key: str, api_secret: str, *, http_request: Optional[Callable] = None,
                       clock: Optional[Callable[[], float]] = None,
                       sleep: Optional[Callable[[float], None]] = None,
                       max_retries: int = 2, retry_base_delay: float = 1.0,
                       recv_window_ms: int = _DEFAULT_RECV_WINDOW_MS) -> dict:
    """签名 GET /api/v3/account(只读)。成功返回原始 dict;任何失败抛异常(调用方决定
    fail-soft 还是 fail-closed)。`http_request`/`clock`/`sleep` 全可注入(测试零真网)。"""
    http = http_request or _default_http_request
    clk = clock or time.time
    slp = sleep or time.sleep
    headers = {"X-MEXC-APIKEY": api_key}

    def _do():
        # 签名串(含 timestamp)每次重试都重新算——退避可能跨越数秒(与适配器同款考量)。
        params = {"timestamp": int(clk() * 1000), "recvWindow": recv_window_ms}
        url = MEXC_MAINNET_BASE + _ACCOUNT_PATH + "?" + _sign_query(params, api_secret)
        status, body = http("GET", url, body=None, headers=headers)
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"non-JSON response (status {status})") from exc
        return status, data

    status, data = call_with_backoff(_do, is_rate_limited=_is_rate_limited,
                                     max_retries=max_retries,
                                     base_delay=retry_base_delay, sleep=slp)
    if not (200 <= status < 300):
        raise RuntimeError(f"account read failed: HTTP {status}")
    if not isinstance(data, dict):
        raise RuntimeError("account response is not a JSON object")
    return data


@dataclass(frozen=True)
class MexcKeySafetyResult:
    """结构化只读校验结果。`raw_fields` 只含布尔/计数类非密钥诊断字段,绝不转存整个响应体。"""
    reachable: bool
    withdraw_disabled: Optional[bool]      # None = 字段缺失/形状未知(诚实,不当 False 用)
    trade_enabled: Optional[bool]
    reasons: tuple = field(default_factory=tuple)
    raw_fields: dict = field(default_factory=dict)

    @property
    def is_safe(self) -> bool:
        """fail-closed:唯有 可达 ∧ 明确 canWithdraw=False ∧ 明确 canTrade=True 才算 safe。
        任何 None/False/不可达都是 False——与 verify_key_permissions 的 UNKNOWN/UNSAFE 同拒。"""
        return (self.reachable and self.withdraw_disabled is True
                and self.trade_enabled is True)


def check_mexc_key_safety(api_key: str, api_secret: str, **kwargs) -> MexcKeySafetyResult:
    """启动只读校验(§10):可达性 + 权限位。任何异常优雅收敛成「不 safe」,绝不外抛、
    绝不把异常消息原文带出(只标异常类型——即便理论上不含密钥,也不冒这个险)。"""
    try:
        data = fetch_mexc_account(api_key, api_secret, **kwargs)
    except Exception as exc:
        return MexcKeySafetyResult(reachable=False, withdraw_disabled=None, trade_enabled=None,
                                   reasons=(f"account read failed: {type(exc).__name__}",))
    can_withdraw = data.get("canWithdraw")
    can_trade = data.get("canTrade")
    balances = data.get("balances")
    raw = {"canTrade": can_trade, "canWithdraw": can_withdraw,
           "n_balances": len(balances) if isinstance(balances, list) else None}
    if not isinstance(can_withdraw, bool) or not isinstance(can_trade, bool):
        return MexcKeySafetyResult(
            reachable=True, withdraw_disabled=None, trade_enabled=None, raw_fields=raw,
            reasons=("response missing/non-boolean canTrade/canWithdraw field(s) — field names "
                     "may not match the real API (UNVERIFIED_MAINNET_RESPONSE; run "
                     "tools/mexc_readonly_smoke.py first)",))
    if can_withdraw:
        return MexcKeySafetyResult(
            reachable=True, withdraw_disabled=False, trade_enabled=can_trade, raw_fields=raw,
            reasons=("canWithdraw=true — this key CAN withdraw funds; DISABLE withdrawals at "
                     "the MEXC console before any live use",))
    if not can_trade:
        return MexcKeySafetyResult(
            reachable=True, withdraw_disabled=True, trade_enabled=False, raw_fields=raw,
            reasons=("canTrade=false — spot trading is not enabled for this key",))
    return MexcKeySafetyResult(
        reachable=True, withdraw_disabled=True, trade_enabled=True, raw_fields=raw,
        reasons=("canWithdraw=false", "canTrade=true",
                 "IP-whitelist binding is NOT verifiable via MEXC v3 API — a human must "
                 "confirm it at the exchange console"))
