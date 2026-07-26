"""tools/verify_key_permissions.py:独立密钥权限校验工具(2026-07-08,安全 P0 补丁,不接线)。

全部注入假 transport——**零真网络请求**。覆盖:
- 两个 venue(binance/bybit)各自的明确 SAFE / 明确 UNSAFE 响应
- 网络异常(超时/连接错误)、限流耗尽重试 -> UNKNOWN(不抛异常崩调用方)
- 响应缺字段/非 JSON/非 2xx/信封 retCode!=0 -> UNKNOWN
- 签名请求的形状(host/path/header/query 签名正确性),验证复用了与
  live/exchange/{binance,bybit}_futures.py 相同的 HMAC 算法
- 凭据加载(复用 tools.live_track_advance._load_credentials)
- **哨兵字符串测试**:传入的 api_key/api_secret 明文绝不出现在任何 print/CLI 输出或
  KeyPermissionResult 的 repr 里
"""
import hashlib
import hmac
import json

import pytest

from live.exchange.binance_futures import BinanceFuturesAdapter
from live.exchange.bybit_futures import BybitFuturesAdapter
from tools.verify_key_permissions import (
    KeyPermissionResult,
    KeySafety,
    _binance_sign_query,
    _bybit_sign_headers,
    check_binance_key_permissions,
    check_bybit_key_permissions,
    main,
    verify_key_permissions,
)

# 哨兵:明显不会自然出现在任何合法输出里的字符串,一旦在 print/log/repr 里现身即为泄漏。
_SENTINEL_KEY = "SENTINEL-DO-NOT-LEAK-API-KEY-VALUE-zzz999"
_SENTINEL_SECRET = "SENTINEL-DO-NOT-LEAK-API-SECRET-VALUE-yyy888"


def _fake_transport(status, payload):
    def _http(method, url, *, body=None, headers=None):
        return status, json.dumps(payload).encode()
    return _http


def _raising_transport(exc):
    def _http(method, url, *, body=None, headers=None):
        raise exc
    return _http


# ---------------------------------------------------------------------------
# Binance: GET /sapi/v1/account/apiRestrictions
# ---------------------------------------------------------------------------

def test_binance_safe_withdraw_disabled_and_ip_restricted():
    resp = {"ipRestrict": True, "enableWithdrawals": False, "enableFutures": True, "enableReading": True}
    result = check_binance_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                           http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.SAFE
    assert result.venue == "binance"
    assert result.is_safe_to_proceed is True


def test_binance_unsafe_withdraw_enabled():
    resp = {"ipRestrict": True, "enableWithdrawals": True}
    result = check_binance_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                           http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNSAFE
    assert result.is_safe_to_proceed is False


def test_binance_unsafe_no_ip_restriction():
    resp = {"ipRestrict": False, "enableWithdrawals": False}
    result = check_binance_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                           http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNSAFE


def test_binance_unknown_on_network_error():
    result = check_binance_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                           http_request=_raising_transport(TimeoutError("boom")),
                                           max_retries=0, sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN
    assert result.is_safe_to_proceed is False


def test_binance_unknown_on_missing_fields():
    result = check_binance_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                           http_request=_fake_transport(200, {"someOtherField": 1}),
                                           sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_binance_unknown_on_non_boolean_fields():
    resp = {"ipRestrict": "true", "enableWithdrawals": "false"}     # 字符串而非布尔——诚实标 UNKNOWN
    result = check_binance_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                           http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_binance_unknown_on_non_json_body():
    def _http(method, url, *, body=None, headers=None):
        return 200, b"<html>not json</html>"
    result = check_binance_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET, http_request=_http,
                                           sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_binance_unknown_on_valid_json_but_not_an_object():
    # 2026-07-08 code review 修复:此前唯一的"非 JSON"用例用的是畸形文本,走的是
    # _parse_json 的 JSONDecodeError -> 外层 except Exception 兜底,从没真正触发
    # _parse_binance_api_restrictions 里 `isinstance(data, dict)` 这条防御分支。
    # 合法 JSON 但不是对象(数组)才能真正走到那条分支。
    result = check_binance_key_permissions(
        _SENTINEL_KEY, _SENTINEL_SECRET,
        http_request=_fake_transport(200, [1, 2, 3]),
        sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_binance_unknown_on_non_2xx_status():
    result = check_binance_key_permissions(
        _SENTINEL_KEY, _SENTINEL_SECRET,
        http_request=_fake_transport(401, {"code": -2015, "msg": "Invalid API-key"}),
        sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_binance_unknown_after_rate_limit_exhausted():
    # 2026-07-08 code review 修复:此前只断言最终 safety,没验证退避重试真的执行了——非 2xx
    # 分支恰好也会独立产出 UNKNOWN,即便重试接线被整段移除测试也照样通过。这里记录实际调用
    # 次数,证明确实发生了 max_retries+1 次请求(不是恰好落在另一个判定分支)。
    calls = []

    def _http(method, url, *, body=None, headers=None):
        calls.append(1)
        return 429, json.dumps({"msg": "too many requests"}).encode()

    result = check_binance_key_permissions(
        _SENTINEL_KEY, _SENTINEL_SECRET, http_request=_http,
        max_retries=1, retry_base_delay=0.0, sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN
    assert len(calls) == 2  # max_retries(1) + 1 次初始请求


def test_binance_recovers_after_transient_rate_limit_then_success():
    # 更强的证明:第 1 次限流、第 2 次成功返回明确的 SAFE 响应,证明重试路径真的把恢复后的
    # 响应一路带回最终结果——如果退避重试逻辑被禁用/绕过,这里只会拿到第一次的 429 -> UNKNOWN。
    responses = iter([
        (429, json.dumps({"msg": "too many requests"}).encode()),
        (200, json.dumps({"ipRestrict": True, "enableWithdrawals": False}).encode()),
    ])

    def _http(method, url, *, body=None, headers=None):
        return next(responses)

    result = check_binance_key_permissions(
        _SENTINEL_KEY, _SENTINEL_SECRET, http_request=_http,
        max_retries=2, retry_base_delay=0.0, sleep=lambda s: None)
    assert result.safety is KeySafety.SAFE


def test_binance_request_is_signed_and_targets_account_host_apirestrictions_path():
    calls = []

    def _http(method, url, *, body=None, headers=None):
        calls.append((method, url, headers))
        return 200, json.dumps({"ipRestrict": True, "enableWithdrawals": False}).encode()

    check_binance_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET, http_request=_http,
                                  clock=lambda: 1_700_000_000.0, sleep=lambda s: None)
    assert len(calls) == 1
    method, url, headers = calls[0]
    assert method == "GET"
    assert url.startswith("https://api.binance.com/sapi/v1/account/apiRestrictions?")
    assert headers["X-MBX-APIKEY"] == _SENTINEL_KEY
    qs, _, sig = url.split("?", 1)[1].rpartition("&signature=")
    # 与 live/exchange/binance_futures.py::_signed_query 同一算法(HMAC-SHA256 query string)
    assert hmac.new(_SENTINEL_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest() == sig


def test_binance_sign_query_matches_real_adapter_algorithm_byte_for_byte():
    # 2026-07-08 code review 修复:此前的"签名"测试只是把镜像函数用到的同一条 HMAC 公式又
    # 抄一遍去比对,只能测出镜像函数内部的自相矛盾,测不出与真实 adapter 的算法漂移。这里直接
    # 实例化真实 BinanceFuturesAdapter(TESTNET_BASE,不受 mainnet live_trading_armed 闸门约束、
    # 不发真实网络请求——只调用私有签名方法),与本工具的镜像函数逐字节比对同一组入参的输出。
    # 未来任一方签名算法漂移(哪怕只加一个字段),这里都会立刻报红。
    fixed_clock = lambda: 1_700_000_000.0
    adapter = BinanceFuturesAdapter(api_key=_SENTINEL_KEY, api_secret=_SENTINEL_SECRET,
                                    base_url=BinanceFuturesAdapter.TESTNET_BASE, clock=fixed_clock)
    # 空 params:adapter._signed_query 自己填 timestamp + 默认 recvWindow=5000(镜像函数的
    # 调用方 check_binance_key_permissions 则显式传 timestamp/recvWindow——这里手动对齐两者)。
    real_qs = adapter._signed_query({})
    mirror_qs = _binance_sign_query(
        {"timestamp": int(fixed_clock() * 1000), "recvWindow": 5000}, _SENTINEL_SECRET)
    assert real_qs == mirror_qs


# ---------------------------------------------------------------------------
# Bybit: GET /v5/user/query-api
# ---------------------------------------------------------------------------

def test_bybit_safe_no_withdraw_permission_and_ip_restricted():
    resp = {"retCode": 0, "retMsg": "OK",
           "result": {"ips": ["1.2.3.4"], "permissions": {"Wallet": ["AccountTransfer"],
                                                           "ContractTrade": ["Order"]}}}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.SAFE
    assert result.venue == "bybit"


def test_bybit_unsafe_withdraw_permission_granted():
    resp = {"retCode": 0, "result": {"ips": ["1.2.3.4"], "permissions": {"Wallet": ["Withdraw"]}}}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNSAFE


def test_bybit_unsafe_unrestricted_ip_wildcard():
    resp = {"retCode": 0, "result": {"ips": ["*"], "permissions": {"Wallet": []}}}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNSAFE


def test_bybit_unsafe_empty_ip_list_treated_as_unrestricted():
    resp = {"retCode": 0, "result": {"ips": [], "permissions": {"Wallet": []}}}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNSAFE


def test_bybit_raw_fields_include_actual_ip_list_for_manual_verification():
    # 2026-07-08 code review 修复:`ip_unrestricted` 的判定基于未经真实响应验证的假设(见模块
    # docstring)——此前 raw_fields 只存 ip_count(一个数字),模块 docstring 建议的"人工核对
    # raw_fields 与交易所网页是否一致"实际核对不到任何有效信息。raw_fields 现在必须包含真实
    # ips 列表内容(IP 不是密钥,没有保密顾虑),人工才能看到究竟是什么值驱动了这个判定。
    resp = {"retCode": 0, "result": {"ips": ["1.2.3.4", "5.6.7.8"], "permissions": {"Wallet": []}}}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.raw_fields["ips"] == ["1.2.3.4", "5.6.7.8"]


def test_bybit_unknown_on_network_error():
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_raising_transport(ConnectionError("boom")),
                                         max_retries=0, sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_bybit_unknown_on_nonzero_retcode():
    resp = {"retCode": 10003, "retMsg": "invalid api key"}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_bybit_unknown_on_missing_fields():
    resp = {"retCode": 0, "result": {"someOtherField": 1}}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_bybit_unknown_on_non_2xx_status():
    # 2026-07-08 code review 修复:此前只有 Binance 侧有这条覆盖,Bybit 完全没有对称测试。
    result = check_bybit_key_permissions(
        _SENTINEL_KEY, _SENTINEL_SECRET,
        http_request=_fake_transport(401, {"retCode": 10003, "retMsg": "invalid api key"}),
        sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_bybit_unknown_on_non_json_body():
    # 2026-07-08 code review 修复:此前只有 Binance 侧有这条覆盖,Bybit 完全没有对称测试。
    def _http(method, url, *, body=None, headers=None):
        return 200, b"<html>not json</html>"
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET, http_request=_http,
                                         sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_bybit_unknown_on_valid_json_but_not_an_object():
    # 合法 JSON 但不是对象——真正走到 isinstance(data, dict) 防御分支(不是 _parse_json 的
    # JSONDecodeError 兜底路径)。
    result = check_bybit_key_permissions(
        _SENTINEL_KEY, _SENTINEL_SECRET,
        http_request=_fake_transport(200, [1, 2, 3]),
        sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_bybit_unknown_on_ips_wrong_type():
    # 2026-07-08 code review 修复:264-270 行的类型防御分支此前完全没有测试覆盖过。
    resp = {"retCode": 0, "result": {"ips": "1.2.3.4", "permissions": {"Wallet": []}}}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_bybit_unknown_on_permissions_wrong_type():
    resp = {"retCode": 0, "result": {"ips": ["1.2.3.4"], "permissions": ["Wallet"]}}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_bybit_unknown_on_wallet_permissions_wrong_type():
    resp = {"retCode": 0, "result": {"ips": ["1.2.3.4"], "permissions": {"Wallet": "AccountTransfer"}}}
    result = check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN


def test_bybit_unknown_after_rate_limit_exhausted():
    # 2026-07-08 code review 修复:此前只断言最终 safety,没验证退避重试真的执行了。这里记录
    # 实际调用次数,证明确实发生了 max_retries+1 次请求。
    calls = []

    def _http(method, url, *, body=None, headers=None):
        calls.append(1)
        return 200, json.dumps({"retCode": 10006, "retMsg": "Too many visits!"}).encode()

    result = check_bybit_key_permissions(
        _SENTINEL_KEY, _SENTINEL_SECRET, http_request=_http,
        max_retries=1, retry_base_delay=0.0, sleep=lambda s: None)
    assert result.safety is KeySafety.UNKNOWN
    assert len(calls) == 2  # max_retries(1) + 1 次初始请求


def test_bybit_recovers_after_transient_rate_limit_then_success():
    # 更强的证明:第 1 次限流、第 2 次成功返回明确的 SAFE 响应,证明重试路径真的把恢复后的
    # 响应一路带回最终结果。
    responses = iter([
        (200, json.dumps({"retCode": 10006, "retMsg": "Too many visits!"}).encode()),
        (200, json.dumps({"retCode": 0,
                         "result": {"ips": ["1.2.3.4"], "permissions": {"Wallet": []}}}).encode()),
    ])

    def _http(method, url, *, body=None, headers=None):
        return next(responses)

    result = check_bybit_key_permissions(
        _SENTINEL_KEY, _SENTINEL_SECRET, http_request=_http,
        max_retries=2, retry_base_delay=0.0, sleep=lambda s: None)
    assert result.safety is KeySafety.SAFE


def test_bybit_request_is_signed_via_headers_and_targets_query_api_path():
    calls = []

    def _http(method, url, *, body=None, headers=None):
        calls.append((method, url, headers))
        return 200, json.dumps({"retCode": 0,
                               "result": {"ips": ["1.2.3.4"], "permissions": {"Wallet": []}}}).encode()

    check_bybit_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET, http_request=_http,
                                clock=lambda: 1_700_000_000.0, sleep=lambda s: None)
    assert len(calls) == 1
    method, url, headers = calls[0]
    assert method == "GET"
    assert url == "https://api.bybit.com/v5/user/query-api"
    assert headers["X-BAPI-API-KEY"] == _SENTINEL_KEY
    ts, recv = headers["X-BAPI-TIMESTAMP"], headers["X-BAPI-RECV-WINDOW"]
    # 与 live/exchange/bybit_futures.py::_signed_headers 同一算法(timestamp+key+recv_window+payload)
    expected = hmac.new(_SENTINEL_SECRET.encode(), (ts + _SENTINEL_KEY + recv + "").encode(),
                        hashlib.sha256).hexdigest()
    assert headers["X-BAPI-SIGN"] == expected


def test_bybit_sign_headers_matches_real_adapter_algorithm_byte_for_byte():
    # 2026-07-08 code review 修复:同 Binance 侧的交叉校验测试——直接实例化真实
    # BybitFuturesAdapter(TESTNET_BASE,不受 mainnet 闸门约束、不发真实网络请求),调用它的
    # 私有 `_signed_headers`,与本工具的镜像函数逐字节比对同一组入参的输出。
    fixed_clock = lambda: 1_700_000_000.0
    adapter = BybitFuturesAdapter(api_key=_SENTINEL_KEY, api_secret=_SENTINEL_SECRET,
                                  base_url=BybitFuturesAdapter.TESTNET_BASE, clock=fixed_clock)
    real_headers = adapter._signed_headers("")
    # adapter._RECV_WINDOW 固定为字符串 "5000"——传 5000(int)让镜像函数 str() 后一致。
    mirror_headers = _bybit_sign_headers(_SENTINEL_KEY, _SENTINEL_SECRET,
                                         int(fixed_clock() * 1000), 5000, "")
    assert real_headers == mirror_headers


# ---------------------------------------------------------------------------
# 凭据加载分派(复用 tools.live_track_advance._load_credentials)
# ---------------------------------------------------------------------------

def test_verify_key_permissions_unknown_venue_raises():
    with pytest.raises(SystemExit):
        verify_key_permissions("hyperliquid")


def test_verify_key_permissions_loads_env_credentials_and_dispatches_to_binance(monkeypatch):
    monkeypatch.setenv("BINANCE_MAINNET_API_KEY", _SENTINEL_KEY)
    monkeypatch.setenv("BINANCE_MAINNET_API_SECRET", _SENTINEL_SECRET)
    resp = {"ipRestrict": True, "enableWithdrawals": False}
    result = verify_key_permissions("binance", http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.SAFE


def test_verify_key_permissions_loads_env_credentials_and_dispatches_to_bybit(monkeypatch):
    monkeypatch.setenv("BYBIT_MAINNET_API_KEY", _SENTINEL_KEY)
    monkeypatch.setenv("BYBIT_MAINNET_API_SECRET", _SENTINEL_SECRET)
    resp = {"retCode": 0, "result": {"ips": ["1.2.3.4"], "permissions": {"Wallet": []}}}
    result = verify_key_permissions("bybit", http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert result.safety is KeySafety.SAFE


def test_verify_key_permissions_missing_credentials_raises_informative_error(monkeypatch, tmp_path):
    monkeypatch.delenv("BINANCE_MAINNET_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_MAINNET_API_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)     # 隔离:确保这里看不到仓库里真实的 key/ 目录
    with pytest.raises(RuntimeError, match="MAINNET"):
        verify_key_permissions("binance")


# ---------------------------------------------------------------------------
# CLI 入口 + 哨兵不泄漏
# ---------------------------------------------------------------------------

def test_cli_safe_key_prints_safe_and_exits_zero_without_leaking_secrets(monkeypatch, capsys):
    monkeypatch.setenv("BINANCE_MAINNET_API_KEY", _SENTINEL_KEY)
    monkeypatch.setenv("BINANCE_MAINNET_API_SECRET", _SENTINEL_SECRET)
    resp = {"ipRestrict": True, "enableWithdrawals": False, "enableFutures": True}
    monkeypatch.setattr("tools.verify_key_permissions._binance_default_transport",
                        _fake_transport(200, resp))
    exit_code = main(["--venue", "binance"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SAFE" in out
    assert _SENTINEL_KEY not in out
    assert _SENTINEL_SECRET not in out


def test_cli_unsafe_key_prints_unsafe_and_exits_nonzero_without_leaking_secrets(monkeypatch, capsys):
    monkeypatch.setenv("BYBIT_MAINNET_API_KEY", _SENTINEL_KEY)
    monkeypatch.setenv("BYBIT_MAINNET_API_SECRET", _SENTINEL_SECRET)
    resp = {"retCode": 0, "result": {"ips": ["*"], "permissions": {"Wallet": ["Withdraw"]}}}
    monkeypatch.setattr("tools.verify_key_permissions._bybit_default_transport",
                        _fake_transport(200, resp))
    exit_code = main(["--venue", "bybit"])
    out = capsys.readouterr().out
    assert exit_code != 0
    assert "UNSAFE" in out
    assert _SENTINEL_KEY not in out
    assert _SENTINEL_SECRET not in out


def test_cli_missing_credentials_prints_error_and_exits_nonzero(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("BINANCE_MAINNET_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_MAINNET_API_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)
    exit_code = main(["--venue", "binance"])
    out = capsys.readouterr().out
    assert exit_code != 0
    assert _SENTINEL_KEY not in out
    assert _SENTINEL_SECRET not in out


def test_result_repr_never_contains_key_material():
    resp = {"ipRestrict": True, "enableWithdrawals": False}
    result = check_binance_key_permissions(_SENTINEL_KEY, _SENTINEL_SECRET,
                                           http_request=_fake_transport(200, resp), sleep=lambda s: None)
    assert isinstance(result, KeyPermissionResult)
    dump = repr(result)
    assert _SENTINEL_KEY not in dump
    assert _SENTINEL_SECRET not in dump


# ---------------------------------------------------------------------------
# --write-evidence(2026-07-24,go-live 向导):脱敏证据 artifact
# ---------------------------------------------------------------------------

def test_cli_write_evidence_writes_redacted_artifact(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BINANCE_MAINNET_API_KEY", _SENTINEL_KEY)
    monkeypatch.setenv("BINANCE_MAINNET_API_SECRET", _SENTINEL_SECRET)
    resp = {"ipRestrict": True, "enableWithdrawals": False, "enableFutures": True}
    monkeypatch.setattr("tools.verify_key_permissions._binance_default_transport",
                        _fake_transport(200, resp))
    out_path = tmp_path / "key_permissions_binance_mainnet.json"
    exit_code = main(["--venue", "binance", "--write-evidence", str(out_path)])
    assert exit_code == 0
    artifact = json.loads(out_path.read_text(encoding="utf-8"))
    assert artifact["venue"] == "binance"
    assert artifact["safety"] == "SAFE"
    assert isinstance(artifact["reasons"], list) and artifact["reasons"]
    assert artifact["raw_fields"]["ipRestrict"] is True
    assert artifact["raw_fields"]["enableWithdrawals"] is False
    assert isinstance(artifact["checked_at_ms"], int) and artifact["checked_at_ms"] > 0
    # 哨兵:密钥明文绝不出现在 artifact 文件里(整个文件原文检查,不只解析后的字段)
    raw_text = out_path.read_text(encoding="utf-8")
    assert _SENTINEL_KEY not in raw_text
    assert _SENTINEL_SECRET not in raw_text


def test_cli_write_evidence_records_unsafe_verdict(monkeypatch, tmp_path):
    monkeypatch.setenv("BYBIT_MAINNET_API_KEY", _SENTINEL_KEY)
    monkeypatch.setenv("BYBIT_MAINNET_API_SECRET", _SENTINEL_SECRET)
    resp = {"retCode": 0, "result": {"ips": ["*"], "permissions": {"Wallet": ["Withdraw"]}}}
    monkeypatch.setattr("tools.verify_key_permissions._bybit_default_transport",
                        _fake_transport(200, resp))
    out_path = tmp_path / "kp.json"
    exit_code = main(["--venue", "bybit", "--write-evidence", str(out_path)])
    assert exit_code != 0                       # 非 SAFE 退出码不变
    artifact = json.loads(out_path.read_text(encoding="utf-8"))
    assert artifact["safety"] == "UNSAFE"       # 不安全结论同样诚实落证据


def test_cli_default_writes_no_evidence_file(monkeypatch, capsys, tmp_path):
    # 默认行为逐字节不变:不带 --write-evidence 时绝不写任何文件
    monkeypatch.setenv("BINANCE_MAINNET_API_KEY", _SENTINEL_KEY)
    monkeypatch.setenv("BINANCE_MAINNET_API_SECRET", _SENTINEL_SECRET)
    resp = {"ipRestrict": True, "enableWithdrawals": False}
    monkeypatch.setattr("tools.verify_key_permissions._binance_default_transport",
                        _fake_transport(200, resp))
    monkeypatch.chdir(tmp_path)
    exit_code = main(["--venue", "binance"])
    assert exit_code == 0
    assert list(tmp_path.iterdir()) == []
