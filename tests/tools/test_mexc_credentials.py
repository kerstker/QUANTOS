"""tools/mexc_credentials.py + verify_key_permissions MEXC 分支(2026-07 MEXC 对接 1.3)。

全部注入假 transport / 临时目录——零真网、零真凭证。钉死的性质:
- 加载器只做形状校验(env 优先、key 文件兜底、base_url 白名单),绝不接受坏形状;
- §10 只读校验三态:唯有 可达 ∧ canWithdraw=False ∧ canTrade=True 才 safe;
  提现开着 → UNSAFE 语义;字段缺失/非 2xx/异常 → 不 safe(UNKNOWN 语义);
- verify_key_permissions 的 _CHECKERS/_MAINNET_CREDENTIAL_CONFIG 认识 venue "mexc";
- mainnet_key_safety_skip_reason 对 mexc:无凭据 = 逐字节 no-op(None,零网络)。
"""
import json

import pytest

from tools import mexc_credentials as mc
from tools.mexc_credentials import (MexcCredentialError, check_mexc_key_safety,
                                    load_mexc_credentials)


def _http_account(payload, status=200):
    def http(method, url, *, body=None, headers=None):
        return status, json.dumps(payload).encode()
    return http


# --- 加载器 ---

def test_load_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MEXC_API_KEY", " env-key ")
    monkeypatch.setenv("MEXC_API_SECRET", " env-secret ")
    monkeypatch.chdir(tmp_path)                       # 无 key 文件也应命中 env
    assert load_mexc_credentials() == ("env-key", "env-secret")


def test_load_falls_back_to_key_file(monkeypatch, tmp_path):
    monkeypatch.delenv("MEXC_API_KEY", raising=False)
    monkeypatch.delenv("MEXC_API_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "key").mkdir()
    (tmp_path / "key" / "mexc_spot.json").write_text(
        json.dumps({"api_key": "file-key", "api_secret": "file-secret",
                    "base_url": "https://api.mexc.com"}), encoding="utf-8")
    assert load_mexc_credentials() == ("file-key", "file-secret")


def test_load_rejects_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("MEXC_API_KEY", raising=False)
    monkeypatch.delenv("MEXC_API_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(MexcCredentialError):
        load_mexc_credentials()


def test_load_rejects_malformed_key_file(monkeypatch, tmp_path):
    monkeypatch.delenv("MEXC_API_KEY", raising=False)
    monkeypatch.delenv("MEXC_API_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "key").mkdir()
    (tmp_path / "key" / "mexc_spot.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(MexcCredentialError):
        load_mexc_credentials()


def test_load_rejects_non_whitelisted_base_url(monkeypatch, tmp_path):
    monkeypatch.delenv("MEXC_API_KEY", raising=False)
    monkeypatch.delenv("MEXC_API_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "key").mkdir()
    (tmp_path / "key" / "mexc_spot.json").write_text(
        json.dumps({"api_key": "k", "api_secret": "s",
                    "base_url": "https://evil.example.com"}), encoding="utf-8")
    with pytest.raises(MexcCredentialError):
        load_mexc_credentials()


def test_load_rejects_empty_strings(monkeypatch):
    monkeypatch.setenv("MEXC_API_KEY", "   ")
    monkeypatch.setenv("MEXC_API_SECRET", "x")
    with pytest.raises(MexcCredentialError):
        load_mexc_credentials()


# --- §10 只读校验三态 ---

_GOOD_ACCOUNT = {"canTrade": True, "canWithdraw": False,
                 "balances": [{"asset": "USDT", "free": "100.0", "locked": "0"}]}


def test_check_safe_only_when_reachable_and_withdraw_disabled_and_trade_enabled():
    result = check_mexc_key_safety("k", "s", http_request=_http_account(_GOOD_ACCOUNT),
                                   clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert result.is_safe is True
    assert result.reachable is True and result.withdraw_disabled is True
    assert any("IP-whitelist" in r or "IP" in r for r in result.reasons)   # 诚实标注不可验证项


def test_check_unsafe_when_withdraw_enabled():
    bad = dict(_GOOD_ACCOUNT, canWithdraw=True)
    result = check_mexc_key_safety("k", "s", http_request=_http_account(bad),
                                   clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert result.is_safe is False
    assert result.withdraw_disabled is False


def test_check_not_safe_when_trade_disabled():
    bad = dict(_GOOD_ACCOUNT, canTrade=False)
    result = check_mexc_key_safety("k", "s", http_request=_http_account(bad),
                                   clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert result.is_safe is False


def test_check_unknown_when_permission_fields_missing():
    # 字段名假设与真实响应不符 → 诚实 UNKNOWN(不 safe),绝不默认 SAFE
    result = check_mexc_key_safety("k", "s", http_request=_http_account({"balances": []}),
                                   clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert result.is_safe is False
    assert result.withdraw_disabled is None
    assert result.reachable is True


def test_check_not_safe_on_non_2xx_and_on_exception():
    result = check_mexc_key_safety("k", "s", http_request=_http_account({}, status=401),
                                   clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert result.is_safe is False and result.reachable is False

    def boom(method, url, *, body=None, headers=None):
        raise ConnectionError("offline")
    result2 = check_mexc_key_safety("k", "s", http_request=boom,
                                    clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert result2.is_safe is False and result2.reachable is False


def test_signature_is_present_and_hmac_shaped():
    captured = {}

    def http(method, url, *, body=None, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return 200, json.dumps(_GOOD_ACCOUNT).encode()
    check_mexc_key_safety("k", "topsecretvalue", http_request=http,
                          clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert "signature=" in captured["url"]
    assert "timestamp=" in captured["url"]
    assert captured["headers"]["X-MEXC-APIKEY"] == "k"
    assert "topsecretvalue" not in captured["url"]    # secret 只进 HMAC,绝不进明文


# --- verify_key_permissions MEXC 分支 ---

def test_verify_key_permissions_knows_mexc():
    from tools.verify_key_permissions import _CHECKERS, _MAINNET_CREDENTIAL_CONFIG
    assert "mexc" in _CHECKERS and "mexc" in _MAINNET_CREDENTIAL_CONFIG
    env = _MAINNET_CREDENTIAL_CONFIG["mexc"]["env"]
    key_file = _MAINNET_CREDENTIAL_CONFIG["mexc"]["key_file"]
    assert env == ("MEXC_MAINNET_API_KEY", "MEXC_MAINNET_API_SECRET")
    assert key_file.endswith("mexc_spot.json")


def test_check_mexc_key_permissions_maps_three_states():
    from tools.verify_key_permissions import KeySafety, check_mexc_key_permissions
    safe = check_mexc_key_permissions("k", "s", http_request=_http_account(_GOOD_ACCOUNT),
                                      clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert safe.safety is KeySafety.SAFE and safe.is_safe_to_proceed
    unsafe = check_mexc_key_permissions(
        "k", "s", http_request=_http_account(dict(_GOOD_ACCOUNT, canWithdraw=True)),
        clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert unsafe.safety is KeySafety.UNSAFE and not unsafe.is_safe_to_proceed
    unknown = check_mexc_key_permissions(
        "k", "s", http_request=_http_account({}, status=500),
        clock=lambda: 1_700_000_000.0, sleep=lambda _s: None)
    assert unknown.safety is KeySafety.UNKNOWN and not unknown.is_safe_to_proceed
    assert unknown.venue == "mexc"


def test_mainnet_key_safety_skip_reason_mexc_noop_without_credentials(monkeypatch, tmp_path):
    # 无凭据 = 逐字节 no-op:返回 None、零网络(头号验收标准)
    from tools.live_track_advance import mainnet_key_safety_skip_reason
    monkeypatch.delenv("MEXC_MAINNET_API_KEY", raising=False)
    monkeypatch.delenv("MEXC_MAINNET_API_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)
    assert mainnet_key_safety_skip_reason("mexc") is None
