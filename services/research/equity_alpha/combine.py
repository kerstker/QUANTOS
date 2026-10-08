"""Train-only composite alpha — strict no-peek.

fit_combo_weights reads ONLY the TRAIN IC aggregates (the test aggregates are
never an input — the function signature makes peeking impossible without
changing the call site). Selection: |train t-stat| > T_THRESHOLD; direction =
sign(train mean IC); magnitude = |train ICIR|; weights normalized so sum(|w|) = 1.
Deterministic tie-breaking by factor name. If nothing passes, returns {} —
fail-closed and honest (an empty composite is a finding, not an error).

apply_combo combines per-date cross-sectional z-scores of the selected RAW
factors with the fitted signed weights (all-or-nothing per stock-date, matching
ic_panel.signal_composite's gate). The same fitted weights are then frozen and
applied unchanged to the test window — the no-peek property is pinned by
tests/research/test_equity_alpha.py (perturbing test-period inputs must not
change the weights).
"""
from __future__ import annotations

from services.research.equity_alpha.factors import cs_zscore_row
from services.research.equity_alpha.hygiene import cs_winsorized_zscore_row

T_THRESHOLD = 2.0


def fit_combo_weights(train_ic_stats: dict, horizon_key: str = "h1",
                      t_threshold: float = T_THRESHOLD,
                      names: "list | None" = None, scheme: str = "icir") -> dict:
    """train_ic_stats: {factor_name: {"h1": {"train": agg, ...}, ...}} (the full eval
    dict is accepted but ONLY the ["h1"/horizon_key]["train"] aggregates are read).
    Returns {factor_name: signed_weight}, sum(|w|) = 1, or {} if no factor passes.

    names: 可选白名单(去冗余幸存因子);None = 全部过筛因子(向后兼容默认)。
    scheme: "icir" = |train ICIR| 加权(既有规则);"equal" = 幸存因子等权(方向仍由
    train IC 符号决定)。两种方案的冠军选择在 train h5 ICIR 上,见 select_champion。"""
    if scheme not in ("icir", "equal"):
        raise ValueError(f"unknown combo scheme: {scheme}")
    pool = sorted(names) if names is not None else sorted(train_ic_stats)
    selected = []
    for name in pool:
        agg = train_ic_stats[name][horizon_key]["train"]
        t, icir, mean_ic = agg.get("t_stat"), agg.get("icir"), agg.get("mean_ic")
        if t is None or icir is None or mean_ic is None:
            continue
        if abs(t) > t_threshold:
            mag = abs(icir) if scheme == "icir" else 1.0
            selected.append((name, (1.0 if mean_ic > 0 else -1.0) * mag))
    tot = sum(abs(w) for _, w in selected)
    if tot <= 0:
        return {}
    return {name: w / tot for name, w in selected}


def select_champion(stats_by_scheme: dict, horizon_key: str = "h5") -> str:
    """冠军组合选择:argmax TRAIN <horizon_key> ICIR。只读 train 聚合(no-peek 由测试钉死:
    扰动 test 聚合冠军不得改变)。ICIR 缺失视为 -inf;平局按方案名字典序(确定性)。
    stats_by_scheme: {scheme: evaluate_signal 输出}。空 dict -> raise(fail-closed)。"""
    if not stats_by_scheme:
        raise ValueError("select_champion needs at least one scheme")
    def _key(item):
        scheme, stats = item
        icir = stats[horizon_key]["train"].get("icir")
        return (-(icir if icir is not None else float("-inf")), scheme)
    return min(stats_by_scheme.items(), key=_key)[0]


def apply_combo(factor_mats: dict, weights: dict, winsorize: bool = True) -> list:
    """Composite score matrix = sum_f w_f * zscore_f(raw factor row). All-or-nothing
    per stock-date (any selected factor missing -> None), matching house style.
    winsorize=True(默认):z-score 前先做 1%/99% 横截面截尾(hygiene),抗肥尾离群值;
    False = 改动前的原始 z-score(回归对照用)。"""
    names = sorted(weights)
    if not names:
        return []
    T = len(factor_mats[names[0]])
    N = len(factor_mats[names[0]][0]) if T else 0
    zf = cs_winsorized_zscore_row if winsorize else cs_zscore_row
    z_rows = {name: [zf(row) for row in factor_mats[name]] for name in names}
    out = [[None] * N for _ in range(T)]
    for t in range(T):
        for i in range(N):
            acc = 0.0
            for name in names:
                z = z_rows[name][t][i]
                if z is None:
                    acc = None
                    break
                acc += weights[name] * z
            out[t][i] = acc
    return out
