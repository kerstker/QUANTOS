"""策略 sleeve 级 sizing opt-in hook(services/portfolio/sleeve_sizing.py,Phase 3 子项2)。
诚实裁定:组合优化器是**跨 sleeve**风险预算分配器,单策略 live 下退化为恒等(no-op)——这个 hook 证明该事实,
且默认 off(live 默认路径永不调用,零回归),只备多策略上线时显式启用。设计:docs/superpowers/specs/2026-07-07-portfolio-optimizer-live-sizing.md。"""
import pytest

from services.portfolio.sleeve_sizing import apply_sleeve_scalar, size_strategy_sleeves


def test_single_sleeve_is_identity():
    # 单策略(唯一 sleeve)-> {id:1.0},证明组合优化器在单策略 live sizing 上是恒等空操作
    assert size_strategy_sleeves([[1.0]], ["crowd_contra"], mode="risk_parity") == {"crowd_contra": 1.0}
    assert size_strategy_sleeves([[9.9]], ["crowd_contra"], mode="inverse_vol") == {"crowd_contra": 1.0}


def test_off_mode_all_ones():
    # 默认 off:不论 cov 全返回 1.0(= 今日 live 行为,每 sleeve 满仓、无跨 sleeve 缩放)
    d = size_strategy_sleeves([[1.0, 0.0], [0.0, 4.0]], ["a", "b"], mode="off")
    assert d == {"a": 1.0, "b": 1.0}


def test_two_uncorrelated_sleeves_split_risk():
    # 2 个不相关等波动 sleeve risk-parity -> 等权(与 optimizer.risk_parity_weights 一致)
    d = size_strategy_sleeves([[1.0, 0.0], [0.0, 1.0]], ["a", "b"], mode="risk_parity")
    assert abs(d["a"] - 0.5) < 1e-6 and abs(d["b"] - 0.5) < 1e-6


def test_inverse_vol_downweights_high_vol_sleeve():
    d = size_strategy_sleeves([[1.0, 0.0], [0.0, 4.0]], ["lo", "hi"], mode="inverse_vol")
    assert d["hi"] < d["lo"]                       # 高波动 sleeve 得更低权


def test_fail_closed_on_non_finite_cov():
    with pytest.raises(ValueError):
        size_strategy_sleeves([[float("nan"), 0.0], [0.0, 1.0]], ["a", "b"], mode="risk_parity")


def test_unknown_mode_fails_closed():
    with pytest.raises(ValueError):
        size_strategy_sleeves([[1.0]], ["a"], mode="hearsay")


def test_apply_sleeve_scalar_preserves_dollar_neutrality():
    # 对整个 book 权重乘标量 -> net 按标量缩、仍近中性;gross 按标量缩
    w = {"BTC": 0.5, "ETH": -0.5}
    out = apply_sleeve_scalar(w, 0.4)
    assert abs(sum(out.values())) < 1e-9                          # 仍中性
    assert abs(sum(abs(v) for v in out.values()) - 0.4) < 1e-9    # gross = 1.0 * 0.4
