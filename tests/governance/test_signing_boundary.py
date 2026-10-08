"""治理:机器钉死签名接缝的 L2 边界。
1) signing.py 不 import 券商/网络/HSM/密钥 SDK;
2) signing.py 无嵌入密钥材料(无 PEM 头、无 *_KEY/SECRET 字面量赋值);
3) NullSigningAuthority.sign() 在 L2 raise;verify_signed_ref 无出进程权威时 fail-closed。"""
import ast
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MOD = os.path.join(_ROOT, "services", "governance", "signing.py")

_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_NETWORK_ROOTS = {"urllib", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
                  "ftplib", "telnetlib", "http"}
# 密钥/HSM 库:L2 下签名接缝不该碰真密码学密钥材料
_KEY_HSM_ROOTS = {"pkcs11", "hsm", "cryptography", "nacl", "Crypto", "keyring", "boto3"}


def _import_roots(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            roots.add(n.module.split(".")[0])
    return roots


def test_signing_imports_no_sdk_network_or_key_lib():
    roots = _import_roots(_MOD)
    for bad, label in ((_FORBIDDEN_SDK, "broker SDK"), (_NETWORK_ROOTS, "network"),
                       (_KEY_HSM_ROOTS, "key/HSM lib")):
        assert not (roots & bad), f"FORBIDDEN {label} in signing.py: {sorted(roots & bad)}"


def test_signing_has_no_embedded_key_material():
    src = open(_MOD, encoding="utf-8").read()
    assert "-----BEGIN" not in src                                   # 无 PEM 密钥头
    # 无形如 PRIVATE_KEY = "..." / SECRET_KEY = '...' / API_KEY = "..." 的密钥字面量赋值
    assert not re.search(r'(PRIVATE_KEY|SECRET_KEY|API_KEY|PRIVATE_SECRET)\s*=\s*["\']', src)


def test_l2_cannot_sign_or_verify():
    from services.governance.signing import (NullSigningAuthority, SignedDecisionRef,
                                             SigningUnavailable, verify_signed_ref)
    with pytest.raises(SigningUnavailable):
        NullSigningAuthority().sign("ph")
    ref = SignedDecisionRef(payload_hash="ph", signature="s", key_id="k", issued_by="x")
    assert verify_signed_ref(ref, "ph", NullSigningAuthority()) is False
    assert verify_signed_ref(ref, "ph", None) is False
