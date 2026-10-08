"""P2-13:研究/paper 干跑限额必须只有一个来源,且逐字段等于各工具重构前的内联字面量。

这些干跑把 DryRunRun 行写进 ``data/cache/dry_run.db``,下游证据(如
``tools/common_risk_research.py`` 按 strategy_id 读取)依赖其中的 ``n_approved``;
限额漂移会静默改写已归档证据。所以这里把重构前的内联字面量冻结成 golden,再**从工具源码
里解析出它实际传给 ``research_dry_run_limits`` 的参数**,断言工厂产出的 ``limits_hash()``
与 golden 逐字节相等——工具侧和工厂侧任何一边漂移都会红。

未归一化的历史变体(participate 0.10 的 carry 系、A股 0.10 集中度、候选扫描的 stress
参数)必须由调用方显式传参保留;这些分支同样被钉住。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services.risk.pre_trade.config import RiskLimits
from tools.research_dry_run_profile import research_dry_run_limits

ROOT = Path(__file__).resolve().parents[2]

_INSTRUMENTS = ("BTCUSDC", "ETHUSDC")

# 多数派内联字面量(重构前逐字段抄自 tools/dry_run_*_multi.py)。整数/浮点类型也承重:
# stable_hash 序列化 int 10**13 与 float 1e13 不同,类型漂移就是证据漂移。
_LEGACY_MAJORITY: dict[str, object] = {
    "default_max_position_qty": 10 ** 13,
    "max_order_notional": 1e14,
    "fat_finger_qty": 10 ** 13,
    "max_gross_exposure": 1e14,
    "max_net_exposure": 1e14,
    "max_leverage": 1e7,
    "max_single_name_pct": 1e7,
    "max_turnover": 1e7,
    "max_participation_rate": 1e7,
    "max_instrument_vol": 5.0,
    "daily_loss_limit": 1e14,
    "max_drawdown": 1.0,
}

# 受损工具及其旧内联字面量相对多数派的差异(必须原样保留,不得归一化)。
_TOOL_LEGACY_OVERRIDES: dict[str, dict[str, object]] = {
    "dry_run_combo_multi.py": {},
    "dry_run_crowd_contra_multi.py": {},
    "dry_run_trend_multi.py": {},
    "dry_run_carry_multi.py": {"max_participation_rate": 0.10},
    "dry_run_carry_authoritative_multi.py": {"max_participation_rate": 0.10},
    "dry_run_btc_spot_multi.py": {},
    "dry_run_top_contra_multi.py": {},
    "dry_run_crowd_contra_signal_adapter_multi.py": {},
    "dry_run_combo_riskparity_multi.py": {},
    "top_contra_dry_run_diagnostic.py": {},
    "crowd_contra_variants.py": {},
    "exit_policy_lab.py": {},
    "mean_reversion_cluster.py": {},
    "alpha_family_discovery.py": {},
    "promotion_gate_crowd_contra.py": {},
    "ashare_alpha_research.py": {"max_participation_rate": 0.10, "max_single_name_pct": 0.10},
    "crypto_robust_optimization.py": {"max_participation_rate": 0.05, "max_leverage": 2.5,
                                      "max_single_name_pct": 1.0, "max_drawdown": 0.30},
}

# 干跑工具的 instrument 实参表达式必须与重构前一致(标的集合决定 limits_hash)。
_TOOL_INSTRUMENT_EXPR: dict[str, str] = {
    "dry_run_btc_spot_multi.py": "frozenset({_INST})",
    "exit_policy_lab.py": "frozenset(book.bars_by_instrument)",
    "ashare_alpha_research.py": "frozenset(names)",
}

# crypto_robust_optimization.py 的 leverage/participation/drawdown 由候选与压力场景驱动:
# 这里只钉住"原样透传",不把它压成默认值。
_DYNAMIC_KWARG_EXPR: dict[str, dict[str, str]] = {
    "crypto_robust_optimization.py": {
        "max_participation_rate": "float(stress.participation_rate)",
        "max_leverage": "float(candidate.leverage_cap)",
        "max_drawdown": "float(candidate.dd_threshold)",
    },
}


def _legacy_limits(**overrides: object) -> RiskLimits:
    """重构前内联字面量的等价体——漂移基准,不改写。"""
    return RiskLimits(allowed_instruments=frozenset(_INSTRUMENTS),
                      allowed_accounts=frozenset({"paper"}),
                      **{**_LEGACY_MAJORITY, **overrides})


def _tool_source(filename: str) -> str:
    return (ROOT / "tools" / filename).read_text(encoding="utf-8")


def _factory_call(filename: str) -> ast.Call:
    tree = ast.parse(_tool_source(filename))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "research_dry_run_limits"]
    assert len(calls) == 1, filename
    return calls[0]


def _module_constants(filename: str) -> dict[str, object]:
    """解析模块级字面量常量(如 _MAX_PARTICIPATION = 0.10)。"""
    tree = ast.parse(_tool_source(filename))
    constants: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                constants[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return constants


def _resolved_kwargs(filename: str) -> dict[str, object]:
    """工具调用点实际传入的 keyword:字面量直接取值,模块常量按名解析,其余保留表达式文本。"""
    constants = _module_constants(filename)
    resolved: dict[str, object] = {}
    for keyword in _factory_call(filename).keywords:
        if isinstance(keyword.value, ast.Name) and keyword.value.id in constants:
            resolved[keyword.arg] = constants[keyword.value.id]
            continue
        try:
            resolved[keyword.arg] = ast.literal_eval(keyword.value)
        except ValueError:
            resolved[keyword.arg] = ast.unparse(keyword.value)
    return resolved


def test_factory_reproduces_majority_literal_byte_for_byte():
    limits = research_dry_run_limits(_INSTRUMENTS)
    assert limits.limits_hash() == _legacy_limits().limits_hash()
    assert limits == _legacy_limits()          # 逐字段,含 int/float 类型
    assert limits.allowed_accounts == frozenset({"paper"})


@pytest.mark.parametrize("filename", sorted(_TOOL_LEGACY_OVERRIDES))
def test_tool_call_site_reproduces_its_legacy_inline_literal(filename: str):
    call = _factory_call(filename)
    assert ast.unparse(call.args[0]) == _TOOL_INSTRUMENT_EXPR.get(filename, "frozenset(held_names)")
    kwargs = _resolved_kwargs(filename)
    dynamic = _DYNAMIC_KWARG_EXPR.get(filename, {})
    for name, expected_expr in dynamic.items():
        assert kwargs.pop(name, None) == expected_expr, (filename, name)
    expected = _TOOL_LEGACY_OVERRIDES[filename]
    # 静态字段必须逐值相等;动态字段只允许出现在上面钉住的表达式里。
    assert kwargs == {k: v for k, v in expected.items() if k not in dynamic}, filename
    got = research_dry_run_limits(_INSTRUMENTS, **expected)
    assert got.limits_hash() == _legacy_limits(**expected).limits_hash(), filename


@pytest.mark.parametrize("filename", sorted(_TOOL_LEGACY_OVERRIDES))
def test_tool_no_longer_inlines_a_private_copy_of_the_profile(filename: str):
    source = _tool_source(filename)
    assert "default_max_position_qty" not in source, filename   # 不再内联复制 profile
    assert "RiskLimits(" not in source, filename


def test_profile_is_paper_only_and_never_yields_a_live_account():
    with pytest.raises(ValueError):
        research_dry_run_limits(_INSTRUMENTS, account="live")
    with pytest.raises(ValueError):
        research_dry_run_limits(_INSTRUMENTS, account="mainnet")


def test_profile_module_stays_out_of_services_and_live_paths():
    """这套非物理占位限额只能留在 tools/ 研究侧;services/live/quantos 引用即红(AGENTS.md 边界)。"""
    offenders = [str(path.relative_to(ROOT))
                 for root in ("services", "live", "quantos")
                 for path in (ROOT / root).rglob("*.py")
                 if "research_dry_run_profile" in path.read_text(encoding="utf-8", errors="ignore")]
    assert offenders == []


def test_factory_leaves_untouched_risk_fields_at_risk_limits_defaults():
    """工厂只覆盖旧内联字面量写过的字段;其余必须仍是 RiskLimits 默认值(不得夹带收紧)。"""
    limits = research_dry_run_limits(_INSTRUMENTS)
    default = RiskLimits()
    for field in ("max_position_qty", "max_price_deviation_pct", "max_orders_per_window",
                  "rate_window_ms", "max_cancel_rate", "min_cancel_rate_samples", "cash_buffer_pct"):
        assert getattr(limits, field) == getattr(default, field), field
