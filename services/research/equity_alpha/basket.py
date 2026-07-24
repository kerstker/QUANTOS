"""Weekly basket selection + capacity-aware, cost-aware long-only backtest — pure functions.

Basket: at each weekly rebalance date, top-N stocks by composite score,
long-only equal-weight. Capacity: per-stock notional capped at
PARTICIPATION_CAP (5%) of the 20-observation mean amt_proxy at AUM — a capped
name's excess weight is redistributed among the uncapped selected names
(iteratively, deterministic); if EVERY selected name is capped the residual
stays in cash (weights sum < 1, reported honestly — at AUM=300万 on a
>=5000万-ADV universe this should essentially never bind).

Backtest: rebalance-to-rebalance buy-and-hold returns per name
(close[t']/close[t] - 1; a name missing the exit close contributes 0 — a
suspension freeze, documented). Cost = TURNOVER * cost_bps (single-sided
convention: Σ|Δw| counts buys+sells, each side charged cost_bps → 15bps covers
A股佣金+印花税量级). Benchmark = equal-weight over ALL eligible names that day,
same periods, same cost convention on its own (near-zero) turnover.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

BASKET_N = 20
AUM_CNY = 3_000_000.0
PARTICIPATION_CAP = 0.05
COST_BPS = 15.0
WEEK_STEP = 5  # trading days between rebalances
CB_HALT_DD = 0.25   # circuit-breaker: drawdown from peak that triggers half exposure
CB_RESUME_DD = 0.15  # recovery threshold that restores full exposure


def trailing_mean_matrix(value_mat: list, window: int = 20) -> list:
    """Per-cell trailing mean of the last `window` non-None values (gap days skipped,
    never back-filled — same accumulation policy as factors.build_factor_matrices).
    Cell[t][i] uses only rows <= t (PIT-safe); < window valid obs -> None (fail-closed).
    Used for the real trailing-20d average VOLUME (share units) that feeds the dry-run
    gate's est_daily_volume — replacing the 1e12 placeholder. Units = input units."""
    T = len(value_mat)
    N = len(value_mat[0]) if T else 0
    out = [[None] * N for _ in range(T)]
    for i in range(N):
        hist: list = []
        for t in range(T):
            v = value_mat[t][i]
            if v is not None:
                hist.append(v)
            if len(hist) >= window:
                out[t][i] = sum(hist[-window:]) / window
    return out


def weekly_indices(n_rows: int, step: int = WEEK_STEP) -> list:
    """Rebalance row indices: 0, step, 2*step, ... (deterministic phase at 0 — the same
    known phase-dependence convention as ic_panel.compute_horizon_ic_series)."""
    return list(range(0, n_rows, step))


def _rank_candidates(score_row: list, adv_row: list, instruments) -> list:
    """[(idx, score)] ranked by (-score, instrument) — the shared candidate ordering.
    Names with missing score / missing-zero ADV are fail-closed OUT (cannot size honestly)."""
    cand = [(i, score_row[i]) for i in range(len(score_row))
            if score_row[i] is not None and adv_row[i] is not None and adv_row[i] > 0]
    cand.sort(key=lambda kv: (-kv[1], instruments[kv[0]]))
    return cand


def _cap_weights(sel: list, adv_row: list, instruments, aum: float, cap_pct: float):
    """Participation-cap redistribution over the chosen index list `sel` (the exact
    iterative fixed-point logic the baseline has always used)."""
    if not sel:
        return {}, [], []
    cap_w = {i: cap_pct * adv_row[i] / aum for i, _ in sel}
    w = {i: 1.0 / len(sel) for i, _ in sel}
    capped: set = set()
    for _ in range(len(sel) + 1):  # iterate cap-and-redistribute to a fixed point
        over = 0.0
        newly = []
        for i, _ in sel:
            if i not in capped and w[i] > cap_w[i]:
                over += w[i] - cap_w[i]
                w[i] = cap_w[i]
                newly.append(i)
        capped.update(newly)
        room = [i for i, _ in sel if i not in capped]
        if over <= 0 or not room:
            break
        for i in room:
            w[i] += over / len(room)
    weights = {instruments[i]: w[i] for i, _ in sel if w[i] > 0}
    return weights, sorted(instruments[i] for i in capped), [instruments[i] for i, _ in sel]


def select_basket(score_row: list, adv_row: list, instruments, n: int = BASKET_N,
                  aum: float = AUM_CNY, cap_pct: float = PARTICIPATION_CAP):
    """Top-n by score, equal-weight, ADV-participation-capped.

    Returns (weights {instrument: w}, capped [instrument], selected [instrument]).
    weights sums to 1 unless every selected name is capacity-capped (then the
    residual is cash — reported, not hidden). Names with missing/zero ADV are
    fail-closed OUT of the basket (cannot size them honestly)."""
    cand = _rank_candidates(score_row, adv_row, instruments)
    return _cap_weights(cand[:n], adv_row, instruments, aum, cap_pct)


def _oneway_sells(target_names: list, prev_weights: dict) -> float:
    """one-way turnover(卖出腿)= Σ max(0, prev_w - target_w),target 等权 1/n。"""
    if not target_names:
        return sum(prev_weights.values())
    tw = {nm: 1.0 / len(target_names) for nm in target_names}
    return sum(max(0.0, prev_weights.get(nm, 0.0) - tw.get(nm, 0.0))
               for nm in set(prev_weights) | set(tw))


def select_basket_managed(score_row: list, adv_row: list, instruments,
                          prev_weights: dict, n: int = BASKET_N, aum: float = AUM_CNY,
                          cap_pct: float = PARTICIPATION_CAP, buffer_k=None,
                          max_oneway=None):
    """换手压缩变体(2026-07-24:全宇宙上 baseline 周换手 1.06,15bps 下成本吃掉超额)。

    buffer_k(缓冲带/滞后):现任持仓只要排名 <= buffer_k 就留任(不被挤出),空位按
    排名从前向后补到 n 名;留任者超过 n 时按排名取前 n(排名优先)。现任当日不在候选
    (停牌/分数缺失)-> 移出目标(与 baseline 同规约,回测对无价腿记 0 冻结)。
    max_oneway(单边换手上限):在 buffer(或纯 top-n)结果上,若卖出腿 > max_oneway,
    逐次换回排名最高的被调出现任、牺牲排名最低的新进,直到达标(等权下每次交换
    改变单边 ~1/n)。两个参数都 None -> 与 select_basket 逐位等价(调用方不应走这里,
    防御性保留)。选择/调参只在 TRAIN 上做(见驱动器),避免对 test 二次偷看。
    """
    cand = _rank_candidates(score_row, adv_row, instruments)
    rank_of = {instruments[i]: pos + 1 for pos, (i, _s) in enumerate(cand)}
    if buffer_k is None:
        target = [instruments[i] for i, _ in cand[:n]]
    else:
        incumbents = [nm for nm in prev_weights if nm in rank_of]
        kept = sorted((nm for nm in incumbents if rank_of[nm] <= buffer_k),
                      key=lambda nm: (rank_of[nm], nm))[:n]
        target = list(kept)
        for i, _s in cand:  # 按排名补到 n 名(跳过已在的)
            if len(target) >= n:
                break
            nm = instruments[i]
            if nm not in target:
                target.append(nm)
    if max_oneway is not None and prev_weights:
        guard = 0
        while target and _oneway_sells(target, prev_weights) > max_oneway + 1e-12:
            guard += 1
            if guard > 4 * n + 4:
                break
            dropped = [nm for nm in prev_weights if nm not in target]
            entrants = [nm for nm in target if nm not in prev_weights]
            if not dropped or not entrants:
                break
            keep = min(dropped, key=lambda nm: (rank_of.get(nm, 10 ** 9), nm))
            worst = max(entrants, key=lambda nm: (rank_of.get(nm, 10 ** 9), nm))
            target.remove(worst)
            target.append(keep)
            target.sort(key=lambda nm: (rank_of.get(nm, 10 ** 9), nm))
    sel = [(i, score_row[i]) for i in range(len(score_row))
           if instruments[i] in set(target)]
    sel.sort(key=lambda kv: (-kv[1], instruments[kv[0]]))
    return _cap_weights(sel, adv_row, instruments, aum, cap_pct)


def select_for_variant(score_row: list, adv_row: list, instruments, prev_weights: dict,
                       n: int = BASKET_N, aum: float = AUM_CNY, cap_pct: float = PARTICIPATION_CAP,
                       buffer_k=None, max_oneway=None):
    """统一选择入口:两参数皆 None -> baseline top-n(逐位等价);否则 managed。"""
    if buffer_k is None and max_oneway is None:
        return select_basket(score_row, adv_row, instruments, n=n, aum=aum, cap_pct=cap_pct)
    return select_basket_managed(score_row, adv_row, instruments, prev_weights,
                                 n=n, aum=aum, cap_pct=cap_pct,
                                 buffer_k=buffer_k, max_oneway=max_oneway)


def _max_drawdown(equity: list) -> float:
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, 1.0 - e / peak)
    return mdd


def summarize(equity: list, periods_per_year: int = 52) -> dict:
    """Weekly equity curve (list of equity levels, len = n_rebalances+1) -> stats."""
    rets = [equity[k + 1] / equity[k] - 1.0 for k in range(len(equity) - 1) if equity[k] > 0]
    total = equity[-1] / equity[0] - 1.0 if equity and equity[0] > 0 else None
    if len(rets) >= 2 and statistics.pstdev(rets) > 0:
        sharpe = statistics.fmean(rets) / statistics.pstdev(rets) * math.sqrt(periods_per_year)
    else:
        sharpe = None
    return {"total_return": round(total, 6) if total is not None else None,
            "ann_sharpe": round(sharpe, 4) if sharpe is not None else None,
            "max_drawdown": round(_max_drawdown(equity), 6),
            "n_periods": len(rets)}


def backtest(score_mat: list, price_mat: list, adv_mat: list, eligible: list, instruments,
             reb_rows: list, row_lo: int = 0, row_hi: Optional[int] = None,
             n: int = BASKET_N, aum: float = AUM_CNY, cap_pct: float = PARTICIPATION_CAP,
             cost_bps: float = COST_BPS, circuit_breaker: bool = False,
             cb_halt_dd: float = CB_HALT_DD, cb_resume_dd: float = CB_RESUME_DD,
             buffer_k=None, max_oneway=None):
    """Long-only top-n weekly backtest over rebalance rows restricted to [row_lo, row_hi].

    Returns dict with equity curves (strategy net, benchmark net), per-period
    turnover, capacity-capped log, and summarize() stats for both legs.
    Cost convention: cost = Σ|Δw| * cost_bps/1e4 charged at each rebalance
    (buys and sells each charged cost_bps — single-sided 15bps ≈ A股佣金+印花税).

    circuit_breaker=True: 回撤熔断变体(研究诊断,非交易规则)。每个调仓时点先看当前
    净值相对历史峰值的回撤 DD:DD > cb_halt_dd -> 敞口减半(权重×0.5,其余现金);
    减半状态下 DD 恢复到 < cb_resume_dd -> 恢复全敞口。换手成本按缩放后权重计算
    (半敞口再平衡的减仓腿也计成本,诚实)。默认 False -> 输出与改动前逐字节相同。

    buffer_k / max_oneway(换手压缩变体):皆 None(默认)-> 每期 baseline top-n,
    输出与改动前逐字节相同;任一设置 -> 走 select_basket_managed,持仓记忆 = 上一期
    目标权重(prev_w,含参与率上限调整后的实际权重)。
    """
    if row_hi is None:
        row_hi = len(score_mat) - 1
    rows = [r for r in reb_rows if row_lo <= r <= row_hi]
    if len(rows) < 2:
        return None
    cost = cost_bps / 1e4
    eq_s, eq_b = [1.0], [1.0]
    prev_w: dict = {}
    prev_b: dict = {}
    turns: list = []
    capped_log: dict = {}
    peak = 1.0
    exposure = 1.0
    half_periods = 0
    exposure_path: list = []
    for k in range(len(rows) - 1):
        t, t2 = rows[k], rows[k + 1]
        w_new, capped, selected = select_for_variant(
            score_mat[t], adv_mat[t], instruments, prev_w,
            n=n, aum=aum, cap_pct=cap_pct, buffer_k=buffer_k, max_oneway=max_oneway)
        for c in capped:
            capped_log[c] = capped_log.get(c, 0) + 1
        if circuit_breaker:
            dd = 1.0 - eq_s[-1] / peak if peak > 0 else 0.0
            if exposure == 1.0 and dd > cb_halt_dd:
                exposure = 0.5
            elif exposure < 1.0 and dd < cb_resume_dd:
                exposure = 1.0
            if exposure < 1.0:
                half_periods += 1
            w_new = {nm: wi * exposure for nm, wi in w_new.items()}
        exposure_path.append(exposure)
        # strategy leg
        gross = 0.0
        for inst, wi in w_new.items():
            i = instruments.index(inst)
            p0, p1 = price_mat[t][i], price_mat[t2][i]
            r = (p1 / p0 - 1.0) if (p0 is not None and p1 is not None and p0 > 0) else 0.0
            gross += wi * r
        names = set(w_new) | set(prev_w)
        turn = sum(abs(w_new.get(nm, 0.0) - prev_w.get(nm, 0.0)) for nm in names)
        turns.append(turn)
        eq_s.append(eq_s[-1] * (1.0 + gross - turn * cost))
        peak = max(peak, eq_s[-1])
        prev_w = w_new
        # benchmark leg: equal-weight ALL eligible names with entry+exit prices
        b_idx = [i for i in range(len(instruments))
                 if eligible[t][i] and price_mat[t][i] is not None and price_mat[t][i] > 0]
        if b_idx:
            bw = {instruments[i]: 1.0 / len(b_idx) for i in b_idx}
            bg = 0.0
            for i in b_idx:
                p1 = price_mat[t2][i]
                r = (p1 / price_mat[t][i] - 1.0) if (p1 is not None and p1 > 0) else 0.0
                bg += bw[instruments[i]] * r
            bnames = set(bw) | set(prev_b)
            bturn = sum(abs(bw.get(nm, 0.0) - prev_b.get(nm, 0.0)) for nm in bnames)
            eq_b.append(eq_b[-1] * (1.0 + bg - bturn * cost))
            prev_b = bw
        else:
            eq_b.append(eq_b[-1])
    result = {"equity": eq_s, "bench_equity": eq_b,
              "strategy": summarize(eq_s), "benchmark": summarize(eq_b),
              "avg_turnover": round(statistics.fmean(turns), 4) if turns else None,
              "capped_names": dict(sorted(capped_log.items())),
              "n_selected_last": len(selected),
              "weights_last": prev_w}
    if circuit_breaker:
        result["circuit_breaker"] = {
            "halt_dd": cb_halt_dd, "resume_dd": cb_resume_dd,
            "half_exposure_periods": half_periods, "n_periods": len(rows) - 1,
            "exposure_path": exposure_path}
    if buffer_k is not None or max_oneway is not None:
        result["variant"] = {"buffer_k": buffer_k, "max_oneway": max_oneway}
    return result
