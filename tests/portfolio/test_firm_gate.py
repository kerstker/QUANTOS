"""firm 级敞口硬门(services/portfolio/firm_gate.py):把 aggregate_firm_books 的诊断视图 + 上限
-> BLOCK/PASS/UNCONFIGURED。审计 P1-6——把"只诊断不阻断"的 firm 聚合升级成可阻断的硬门(数字由人签)。"""
from services.portfolio.firm_aggregation import aggregate_firm_books
from services.portfolio.firm_gate import evaluate_firm_gate


def _view(books, notionals=None):
    return aggregate_firm_books(books, notionals)


def test_propagates_blocked_aggregate():
    # 空 book -> aggregate BLOCKED -> gate 传播 BLOCK
    d = evaluate_firm_gate(aggregate_firm_books({}), max_gross=1e9)
    assert d["status"] == "BLOCK"


def test_no_limits_is_unconfigured_not_silent_pass():
    # 没配任何上限 -> UNCONFIGURED(调用方决定:多所自动当 fail-closed,单所可忽略),绝不静默 PASS
    view = _view({"a": {"BTC": 1.0}, "b": {"BTC": 1.0}}, {"a": 1000.0, "b": 1000.0})
    d = evaluate_firm_gate(view)
    assert d["status"] == "UNCONFIGURED"


def test_firm_gross_exceeded_blocks():
    # 两所各 $1000 BTC 多 -> firm_gross $2000 > max_gross $1500 -> BLOCK
    view = _view({"a": {"BTC": 1.0}, "b": {"BTC": 1.0}}, {"a": 1000.0, "b": 1000.0})
    d = evaluate_firm_gate(view, max_gross=1500.0)
    assert d["status"] == "BLOCK"
    assert any("firm_gross_exceeded" in r for r in d["reasons"])


def test_firm_net_exceeded_blocks():
    # 两所同向 BTC 多 -> firm_net $2000 > max_net $1500 -> BLOCK
    view = _view({"a": {"BTC": 1.0}, "b": {"BTC": 1.0}}, {"a": 1000.0, "b": 1000.0})
    d = evaluate_firm_gate(view, max_gross=1e9, max_net=1500.0)
    assert d["status"] == "BLOCK"
    assert any("firm_net_exceeded" in r for r in d["reasons"])


def test_single_name_concentration_exceeded_blocks():
    # BTC 跨两所加总占 firm_gross 100% > max_single_name_weight 0.5 -> BLOCK(隐藏拥挤)
    view = _view({"a": {"BTC": 1.0}, "b": {"BTC": 1.0}}, {"a": 1000.0, "b": 1000.0})
    d = evaluate_firm_gate(view, max_gross=1e9, max_net=1e9, max_single_name_weight=0.5)
    assert d["status"] == "BLOCK"
    assert any("firm_single_name_exceeded" in r for r in d["reasons"])


def test_within_all_limits_passes():
    # firm_gross $2000, net $0(多空抵消), 单名 BTC/ETH 各 50% -> 全在限内 -> PASS
    view = _view({"a": {"BTC": 1.0}, "b": {"ETH": -1.0}}, {"a": 1000.0, "b": 1000.0})
    d = evaluate_firm_gate(view, max_gross=5000.0, max_net=1000.0, max_single_name_weight=0.6)
    assert d["status"] == "PASS" and d["reasons"] == ()


def test_partial_config_checks_only_set_limits():
    # 只配 max_gross(满足)-> PASS(不因 net/single-name 未配而 BLOCK)
    view = _view({"a": {"BTC": 1.0}, "b": {"ETH": -1.0}}, {"a": 1000.0, "b": 1000.0})
    d = evaluate_firm_gate(view, max_gross=5000.0)
    assert d["status"] == "PASS"


def test_non_finite_limit_fails_closed():
    # NaN 上限参与 `>` 恒 False 会静默放行任意敞口 -> 必须显式 fail-closed 到 BLOCK(镜像 mandate NaN 修复)
    view = _view({"a": {"BTC": 1.0}}, {"a": 1000.0})
    d = evaluate_firm_gate(view, max_gross=float("nan"))
    assert d["status"] == "BLOCK" and any("non_finite_firm_limit" in r for r in d["reasons"])
