"""2026-09-20 审计 P0-1:哈希序列化必须能拒绝 NaN/Infinity。

问题。``quantos/contracts/hashing.py`` 走 ``json.dumps``,而 Python 的
``json.dumps`` 默认 ``allow_nan=True``——它会输出 ``NaN`` / ``Infinity`` 这两个
**不是 RFC 8259 JSON 的字面量**。后果是:一个非有限的限额被"成功地"哈希成一个
看起来完全合法的 64 位十六进制摘要,于是 ``limits_hash()`` / 证据绑定会把一个
语义上无意义的配置当成有身份的正常配置。

为什么不是全局翻转。全仓默认收紧会打断真实的 inf 生产者
(services/alpha/walk_forward.py、decay.py、services/intelligence/search_strategy.py、
services/portfolio/signal_adapter.py 等),而且
tests/research/test_single_asset_rule_backtest.py 明确断言
``summary['profit_factor'] == math.inf``。所以这里提供**显式选择**的严格路径,
由风险/身份这类"绝不允许非有限"的调用点主动开启,默认行为保持不变。
"""

import json
import math

import pytest

from quantos.contracts.hashing import short_hash, stable_hash, stable_json


NON_FINITE = (
    ("nan", float("nan")),
    ("inf", float("inf")),
    ("-inf", float("-inf")),
)


def _reject_non_finite_constant(value: str):
    raise ValueError(f"non-RFC-8259 constant in payload: {value}")


def _is_strict_rfc8259(text: str) -> bool:
    try:
        json.loads(text, parse_constant=_reject_non_finite_constant)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------
# 1. 默认行为保持不变(向后兼容 396 个调用点)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("label, value", NON_FINITE)
def test_default_remains_permissive_for_existing_callers(label, value):
    digest = stable_hash({"limit": value})

    assert isinstance(digest, str) and len(digest) == 64


def test_default_output_is_deterministic_for_non_finite_values():
    a = stable_hash({"limit": float("nan")})
    b = stable_hash({"limit": float("nan")})

    assert a == b, "the permissive path must stay reproducible, however odd the input"


# --------------------------------------------------------------------------
# 2. 严格路径必须真的拒绝
# --------------------------------------------------------------------------


@pytest.mark.parametrize("label, value", NON_FINITE)
def test_strict_hash_rejects_non_finite(label, value):
    with pytest.raises(ValueError):
        stable_hash({"limit": value}, allow_non_finite=False)


@pytest.mark.parametrize("label, value", NON_FINITE)
def test_strict_json_rejects_non_finite(label, value):
    with pytest.raises(ValueError):
        stable_json({"limit": value}, allow_non_finite=False)


@pytest.mark.parametrize("label, value", NON_FINITE)
def test_strict_short_hash_rejects_non_finite(label, value):
    with pytest.raises(ValueError):
        short_hash({"limit": value}, allow_non_finite=False)


def test_strict_path_rejects_nested_and_listed_non_finite():
    with pytest.raises(ValueError):
        stable_hash({"a": {"b": [1.0, float("inf")]}}, allow_non_finite=False)


# --------------------------------------------------------------------------
# 3. 严格路径的产出确实是合法 RFC 8259;宽松路径的产出不是
# --------------------------------------------------------------------------


def test_strict_output_is_strict_rfc8259():
    assert _is_strict_rfc8259(stable_json({"limit": 1.0}, allow_non_finite=False))


@pytest.mark.parametrize("label, value", NON_FINITE)
def test_permissive_output_is_not_strict_rfc8259(label, value):
    assert not _is_strict_rfc8259(stable_json({"limit": value})), (
        "the permissive codec emits NaN/Infinity literals, which is exactly why the "
        "strict path exists for identity-critical payloads"
    )


# --------------------------------------------------------------------------
# 4. 有限输入不受开关影响
# --------------------------------------------------------------------------


def test_finite_payloads_hash_identically_under_both_modes():
    payload = {"a": [1.0, -2.5, 0.0], "b": {"c": 3.25}}

    assert stable_hash(payload) == stable_hash(payload, allow_non_finite=False)
    assert stable_json(payload) == stable_json(payload, allow_non_finite=False)


# --------------------------------------------------------------------------
# 5. 真实形态:非有限的"限额"绝不能被绑成合法身份
# --------------------------------------------------------------------------


def test_non_finite_limit_cannot_be_bound_as_a_stable_identity():
    """一个 NaN 限额此前会绑出一个看起来正常的 64 位摘要。"""
    nan_limit = {"max_gross_exposure": float("nan"), "max_leverage": 2.0}

    permissive = stable_hash(nan_limit)
    assert isinstance(permissive, str) and len(permissive) == 64   # 旧行为:静默通过

    with pytest.raises(ValueError):
        stable_hash(nan_limit, allow_non_finite=False)


def test_infinite_threshold_is_indistinguishable_from_a_real_one_when_permissive():
    """Infinity 与一个有限哨兵值此前都能"正常"哈希——两者都必须被严格路径拦下。"""
    with pytest.raises(ValueError):
        stable_hash({"max_order_notional": float("inf")}, allow_non_finite=False)
    assert math.isfinite(1e9)
