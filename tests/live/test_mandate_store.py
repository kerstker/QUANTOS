"""mandate 文件加载器(live/governance/mandate_store.py)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.6 延伸。

真实数字由用户自己创建文件签署——镜像 API 密钥的处理方式(key/binance_test_key.json),
这里不代填任何"看起来合理"的默认数字,避免 AI 变相给自己发通行证。fail-closed:
文件缺失/格式错误/字段非法都要有清楚的错误,而不是静默产出一个"看起来能用"但错的 Mandate。
"""
import json

import pytest

from live.governance.mandate_store import MandateLoadError, load_mandate


def _write(tmp_path, data):
    path = tmp_path / "mandate.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(MandateLoadError, match="not found"):
        load_mandate(str(tmp_path / "nonexistent.json"))


def test_malformed_json_raises_clear_error(tmp_path):
    path = tmp_path / "mandate.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(MandateLoadError, match="malformed"):
        load_mandate(str(path))


def test_valid_mandate_loads_all_fields(tmp_path):
    path = _write(tmp_path, {
        "mandate_id": "m-2026-07", "venue": "testnet", "exchange": "binance",
        "allowed_instruments": ["BTC", "ETH"],
        "max_single_order_notional": 500.0, "max_gross_notional": 3000.0,
        "max_net_notional": 1500.0,
        "per_instrument_max_notional": {"BTC": 800.0, "ETH": 600.0},
        "approved_by": ["alice"], "issued_at": 1000, "expires_at": 2000,
    })
    m = load_mandate(path)
    assert m.mandate_id == "m-2026-07" and m.venue == "testnet"
    assert m.exchange == "binance"
    assert m.allowed_instruments == frozenset({"BTC", "ETH"})
    assert m.max_single_order_notional == 500.0
    assert m.max_gross_notional == 3000.0 and m.max_net_notional == 1500.0
    assert dict(m.per_instrument_max_notional) == {"BTC": 800.0, "ETH": 600.0}
    assert m.approved_by == ("alice",)
    assert m.issued_at == 1000 and m.expires_at == 2000


def test_null_expires_at_means_no_expiry(tmp_path):
    path = _write(tmp_path, {
        "mandate_id": "m", "venue": "testnet", "exchange": "binance", "allowed_instruments": ["BTC"],
        "max_single_order_notional": 1.0, "max_gross_notional": 1.0, "max_net_notional": 1.0,
        "expires_at": None,
    })
    m = load_mandate(path)
    assert m.expires_at is None


def test_optional_fields_default_sensibly(tmp_path):
    path = _write(tmp_path, {
        "mandate_id": "m", "venue": "testnet", "exchange": "binance", "allowed_instruments": ["BTC"],
        "max_single_order_notional": 1.0, "max_gross_notional": 1.0, "max_net_notional": 1.0,
    })
    m = load_mandate(path)
    assert m.per_instrument_max_notional == ()
    assert m.approved_by == ()
    assert m.issued_at == 0 and m.expires_at is None


@pytest.mark.parametrize("missing_field", [
    "mandate_id", "venue", "exchange", "allowed_instruments",
    "max_single_order_notional", "max_gross_notional", "max_net_notional",
])
def test_missing_required_field_raises_clear_error(tmp_path, missing_field):
    data = {
        "mandate_id": "m", "venue": "testnet", "exchange": "binance", "allowed_instruments": ["BTC"],
        "max_single_order_notional": 1.0, "max_gross_notional": 1.0, "max_net_notional": 1.0,
    }
    del data[missing_field]
    path = _write(tmp_path, data)
    with pytest.raises(MandateLoadError, match="invalid/missing"):
        load_mandate(path)


def test_wrong_type_field_raises_clear_error(tmp_path):
    path = _write(tmp_path, {
        "mandate_id": "m", "venue": "testnet", "exchange": "binance", "allowed_instruments": ["BTC"],
        "max_single_order_notional": "not-a-number", "max_gross_notional": 1.0,
        "max_net_notional": 1.0,
    })
    with pytest.raises(MandateLoadError, match="invalid/missing"):
        load_mandate(path)


# --- 2026-07-05 安全审查 HIGH 发现:NaN 数值字面量静默绕过全部名义额限额检查 ---

def test_load_mandate_rejects_nan_gross_notional(tmp_path):
    path = _write(tmp_path, {
        "mandate_id": "m", "venue": "testnet", "exchange": "binance",
        "allowed_instruments": ["BTC"], "max_single_order_notional": 1.0,
        "max_gross_notional": float("nan"), "max_net_notional": 1.0,
    })
    with pytest.raises(MandateLoadError):
        load_mandate(path)


def test_load_mandate_rejects_nan_from_json_literal(tmp_path):
    # json.load 默认接受非标准 NaN 字面量(标准 JSON 不允许,但 Python json 模块默认放行)——
    # 必须显式拒绝,不能指望上游格式校验
    import json as _json
    path = tmp_path / "live_mandate.json"
    path.write_text(_json.dumps({
        "mandate_id": "m", "venue": "testnet", "exchange": "binance",
        "allowed_instruments": ["BTC"], "max_single_order_notional": 1.0,
        "max_gross_notional": 1.0, "max_net_notional": 1.0,
    }).replace('"max_gross_notional": 1.0', '"max_gross_notional": NaN'), encoding="utf-8")
    with pytest.raises(MandateLoadError):
        load_mandate(str(path))


def test_load_mandate_rejects_infinity(tmp_path):
    path = _write(tmp_path, {
        "mandate_id": "m", "venue": "testnet", "exchange": "binance",
        "allowed_instruments": ["BTC"], "max_single_order_notional": float("inf"),
        "max_gross_notional": 1.0, "max_net_notional": 1.0,
    })
    with pytest.raises(MandateLoadError):
        load_mandate(path)
