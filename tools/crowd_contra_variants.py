"""预注册 crowd_contra 变体族全生命周期驱动器(discovery → falsification → capacity → dry-run →
trial 注册 → 前向 paper 候选提名)。机构标准:先看 pre-registration,再看任何结果;每个变体评估都
记为一次 trial;绝不静默挑樱桃。

家族设计(2026-07-24 预注册,先于任何评估写入本文件):crowd_contra = cs_rank(neg(global_ls))
是平台唯一确认可部署 edge(IC t-stat 4.32、干跑 VT Sharpe ~1.38/回撤 ~11%、过证伪电池)。本驱动器
把「变体族」作为**一次**预注册实验跑完 16 个变体 + trial #0 基线重跑(族判定全部**相对**本次重跑的
基线,非绝对值),回答:量化分位/调仓节奏/正交 OI 信息复合,能否强化可部署 edge 集。

四条流水线阶段全部复用既有引擎、零新数学:
  1. IC 初筛:services/research/ic_panel.py(h=1 可比口径 + h=变体自身节奏的非重叠诚实 t-stat)。
  2. 证伪电池:与 tools/crypto_oi_verify.py **同一台 5 项机器检验、同一批通过阈值**
     (lag>0.5 / net@10bps>0.5 / 年份广度 / 子宇宙>0.3 / 对 carry 增量 IR>0.3)。适配(已记录):
     每变体按**自身分位+自身节奏**构造流(证伪应检验"实际交易的形态";verify 用的日频重平衡流
     对周度交易的基线本就是近似——本驱动器对基线同时跑 cadence=1 复现 verify 数字作锚);节奏变体的
     lag 检验仍是"信号日后 1 个自然日入场"(检验同_bar_ 时点假象的同一精神);quantile 稳健性扫描
     (verify 的第 3 项诊断、非 5 项机器检验之一)对分位变体按定义省略——分位即变体轴。
  3. 容量:tools/capacity_research.py 同法 Almgren-Chriss + 参与率带上限(区间带,非单点;
     UNCALIBRATED_PRINCIPLED),ppy 按变体节奏年化。
  4. 周度(=变体节奏)滚动干跑:与 tools/dry_run_crowd_contra_multi.py 同一条
     run_multi_period_dry_run(10bps、整手、fail-closed 预交易门 + 拥挤门 z>3.0 BLOCK 跳周、资金费
     现金流、raw+vol-target 双曲线)。**变体不写入 dry_run.db**——正典库只留官方驱动器的 5 条策略;
     变体数字只落本证据文件(诚实:不污染 console 干跑页与事件 outbox)。年化夏普按变体节奏重算
     (管线内部 _ann_sharpe 恒 √52,对 3d/14d 变体是错误年化——从返回曲线自重算,7d 时逐位一致)。

判定门槛(预注册,见 VERDICT_BAR):CANDIDATE 当且仅当 (a) 自身节奏 horizon IC t-stat ≥ 2;
(b) 证伪通过数 ≥ 本次基线通过数;(c) 干跑 VT 夏普 ≥ 0.8 × 本次基线 VT 夏普;(d) VT 最大回撤
≤ 1.5 × 本次基线。其余一律 REJECTED 并记录失败阶段。硬失败(计算异常)短路,记录到达阶段;
统计弱势不短路——4 个阶段对每个变体都跑完。

Trial 纪律:17 个候选经 run_registered_candidate_batch(既有的 register-then-evaluate 组合)按
节奏分 3 批(年化 ppy 122/52/26)注册进 experiment registry 并过评估器——DSR(Deflated Sharpe,
按全 trial 总体泄气)逐候选收进 honesty 节。批次顺序 7d → 3d → 14d:后批 trial 计数更多、泄气更狠
(保守方向,已记录)。

确定性:同输入 → 同数字;无 RNG;墙钟仅在 main 边界(registry 时间戳/generated_at)。
无网络、无真单、无凭证。实盘晋级 = 另一独立人工决策 = 非目标(本驱动器绝不碰 _STRATEGIES/mandate/
attestation/live 治理文件;CANDIDATE 只提名进 tools/forward_track_advance.py 前向 paper 台账)。
"""
from __future__ import annotations

import math
import os
import statistics
import time

from services.portfolio.crowding_gate import evaluate_crowding_gate, summarize_gate_decisions
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.factor_set import incremental_ir
from services.research.ic_panel import (aggregate_ic_series, compute_horizon_ic_series,
                                        compute_period_ic_series)
from services.research.realism.capacity_curve import capacity_band
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from quantos.contracts.alpha import AlphaDefinition
from services.alpha.config import AlphaFactoryConfig
from tools.cache_io import atomic_write_json
from tools.capacity_research import _adv, _AUM_GRID, _COEF_LO_HI, _HORIZON_GRID, _PARTICIP_GRID
from tools.crypto_oi_research import CARRY, _legs, build_panel
from tools.crypto_oi_verify import CROWD_CONTRA, _per_year
from tools.dry_run_crowd_contra_multi import _MAX_CROWDING_Z, _crowding_rank
from tools.research_dry_run_profile import research_dry_run_limits
from tools.search_trial_registry import (field_panel_dataset_version, make_search_id,
                                         production_experiment_registry)
from live.execution.crowding_score import crowding_zscore as _crowding_zscore  # 与干跑驱动器同一实现

_OUT = os.path.join("evidence", "crowd_contra_variants.json")
_DAY_MS = 86_400_000
_AUM = 1_000_000.0
_COST_BPS = 10.0            # 与官方 crowd 干跑同一平费假设(对上证伪 net@10bps 那条证据)
_FAMILY = "crowd_contra_variants_v1"
_SEARCH_KIND = "CROWD_CONTRA_VARIANT_FAMILY"
_SOURCE = "tools.crowd_contra_variants"

# ---------------------------------------------------------------------------
# PRE-REGISTRATION(2026-07-24,先于任何评估写入;改动本块 = 新实验,须换 _FAMILY 版本号)
# ---------------------------------------------------------------------------


def _zneg(field):
    return ("cs_zscore", ("neg", ("field", field)))


def zblend_expression(terms):
    """等权截面 z-score 复合(复刻 ic_panel.signal_composite 的 DSL 版):每个子项先 cs_zscore 再平均。
    terms = DSL 子表达式元组(每个都已是 z 尺度)。"""
    acc = terms[0]
    for term in terms[1:]:
        acc = ("add", acc, term)
    return ("div", acc, ("const", float(len(terms))))


# 预注册判定门槛。maxDD 取 vol-target(可部署形态)曲线,与 (c) 的 VT 夏普同口径。
VERDICT_BAR = {
    "min_t_stat": 2.0,
    "t_stat_horizon": "variant_own_cadence_non_overlapping",
    "min_falsification_passes": "baseline_this_run",
    "min_vt_sharpe_ratio_vs_baseline": 0.8,
    "max_drawdown_vt_ratio_vs_baseline": 1.5,
}


def _spec(vid, kind, expr, quantile, cadence_days, rationale, fields, role="variant"):
    return {"id": vid, "role": role, "kind": kind, "expr": expr, "quantile": quantile,
            "cadence_days": cadence_days, "rationale": rationale, "fields": tuple(fields)}


def build_variant_specs():
    """17 个预注册变体(trial #0 = 基线重跑;族判定相对基线)。每个一句话理由。"""
    g, tk, tp = "global_ls", "taker_ls", "top_ls"
    specs = [
        _spec("crowd_contra", "baseline", CROWD_CONTRA, 0.30, 7,
              "trial #0 基线重跑(部署口径 q=0.30/7d)——全族判定相对本次重跑的它,非绝对值",
              (g,), role="baseline"),
        # --- Q 扫描(周度):广度 vs 集中度权衡 ---
        _spec("crowd_contra_q20", "quantile", CROWD_CONTRA, 0.20, 7,
              "集中度↑(24 币 k≈5):edge 若住在最拥挤尾部则更纯;book 更噪——测尾部集中度",
              (g,)),
        _spec("crowd_contra_q25", "quantile", CROWD_CONTRA, 0.25, 7,
              "中间集中度(k≈6):q20 与基线 k≈7 之间的广度/集中度权衡点",
              (g,)),
        _spec("crowd_contra_q35", "quantile", CROWD_CONTRA, 0.35, 7,
              "广度↑(k≈8):若 edge 集中在最拥挤名,更宽腿会稀释它——q20 的反方向稳健性",
              (g,)),
        # --- 持有期扫描(q=0.30):信号 horizon 匹配 ---
        _spec("crowd_contra_3d", "cadence", CROWD_CONTRA, 0.30, 3,
              "信号 horizon 匹配:fade 若衰减快,更快调仓多收 alpha;换手/成本 ~2.3× —— 净值检验",
              (g,)),
        _spec("crowd_contra_14d", "cadence", CROWD_CONTRA, 0.30, 14,
              "慢持仓假说:拥挤度若慢变,持有 14d 损失小、换手/成本减半",
              (g,)),
        # --- 复合扫描(q=0.30/7d):正交 OI 信息是否加 IC ---
        _spec("crowd_taker_combo", "composite", zblend_expression((_zneg(g), _zneg(tk))), 0.30, 7,
              "taker_ls=主动吃单失衡,同一 fade 命题作用在正交 OI 指标上——信号内分散",
              (g, tk)),
        _spec("crowd_top_combo", "composite", zblend_expression((_zneg(g), _zneg(tp))), 0.30, 7,
              "top_contra 是已记录 verify 跑里协同集第 3 选(增量 IR 0.52)——两个 fade 信号复合",
              (g, tp)),
        _spec("crowd_taker_top_combo", "composite",
              zblend_expression((_zneg(g), _zneg(tk), _zneg(tp))), 0.30, 7,
              "等权三重:fade 命题跨三条持仓序列的最大跨指标分散",
              (g, tk, tp)),
        # --- 正交 分位×节奏 网格(补全交互)---
        _spec("crowd_contra_q25_3d", "grid", CROWD_CONTRA, 0.25, 3,
              "分位×节奏网格:集中度可能与持有期交互(集中 book 换手更快)——网格角揭示交互",
              (g,)),
        _spec("crowd_contra_q25_14d", "grid", CROWD_CONTRA, 0.25, 14,
              "分位×节奏网格(同上,交互对角)",
              (g,)),
        _spec("crowd_contra_q35_3d", "grid", CROWD_CONTRA, 0.35, 3,
              "分位×节奏网格(广度×快节奏角)",
              (g,)),
        _spec("crowd_contra_q35_14d", "grid", CROWD_CONTRA, 0.35, 14,
              "分位×节奏网格(广度×慢节奏角——成本最省形态)",
              (g,)),
        _spec("crowd_contra_q20_3d", "grid", CROWD_CONTRA, 0.20, 3,
              "分位×节奏网格(最集中×最快角——换手压力最大形态)",
              (g,)),
        _spec("crowd_contra_q20_14d", "grid", CROWD_CONTRA, 0.20, 14,
              "分位×节奏网格(最集中×最慢角)",
              (g,)),
        # --- 另两条有原则的复合 ---
        _spec("crowd_oi_delta_combo", "composite_extra",
              zblend_expression((_zneg(g), ("cs_zscore", ("neg", ("ts_delta", ("field", g), 7))))),
              0.30, 7,
              "同一持仓序列的 水平+动态 复合;delta 变体已是注册过的 IC 面板候选(oi_ls_delta_contra)",
              (g,)),
        _spec("crowd_carry_blend", "composite_extra",
              zblend_expression((_zneg(g), CARRY)), 0.30, 7,
              "两条已确认正交 edge 的**信号级**等权混合(book 级混合已研究:VT 1.20 < crowd 单条 1.38;"
              "信号级未测)——同宇宙同口径检验",
              (g, "funding")),
    ]
    ids = [s["id"] for s in specs]
    assert len(ids) == 17 and len(set(ids)) == 17, "预注册族必须是 17 个唯一 id"
    assert sum(1 for s in specs if s["role"] == "baseline") == 1
    return tuple(specs)


PRE_REGISTRATION = {
    "family": _FAMILY,
    "registered_before_evaluation": True,
    "date": "2026-07-24",
    "trial_count_planned": 17,
    "verdict_bar": dict(VERDICT_BAR),
    "adaptations": [
        "证伪电池按变体自身分位+自身节奏构造流(检验实际交易形态);verify 原 harness 的日频重平衡"
        " 流对周度基线是近似——基线同时跑 cadence=1 复现 data/cache/crypto_oi_verify.json 数字作锚。",
        "节奏变体 lag 检验保持'信号后 1 个自然日入场'(同一时点假象检验精神);成本按调仓日实际换手计。",
        "quantile 稳健性扫描(verify 第 3 项诊断,非 5 项机器检验)对分位变体按定义省略——分位即变体轴。",
        "干跑年化夏普按变体节奏重算(管线 _ann_sharpe 恒 √52,3d/14d 变体需 √122/√26);7d 时与管线逐位一致。",
        "容量 ppy 按变体节奏(52/122/26);AUM 网格/冲击系数带/参与率网格与 tools/capacity_research.py 相同。",
        "变体不写 dry_run.db(正典库只留官方 5 条策略);变体数字只落本证据文件。",
    ],
}

# ---------------------------------------------------------------------------
# 纯函数核心(可单测)
# ---------------------------------------------------------------------------


def cadence_rows(event_times, cadence_days: int):
    """决策行采样:首行,然后每行与上一决策行相距 >= cadence_days 天。
    cadence_days=7 时与 tools/dry_run_crowd_contra_multi.py 的 _WEEK_MS 采样逐位一致。"""
    step = int(cadence_days) * _DAY_MS
    rows, last = [], None
    for r in range(len(event_times)):
        if last is None or event_times[r] - last >= step:
            rows.append(r)
            last = event_times[r]
    return rows


def ppy_for_cadence(cadence_days: int) -> int:
    """年化期数:7d→52(与基线管线逐位一致),3d→122,14d→26。"""
    return int(round(365.25 / int(cadence_days)))


def streams_cadence(sig, px, fund_mat, q, rebalance_rows, lag=0):
    """变体节奏下的逐日 price-LS / economic / turnover 流。book 只在 rebalance_rows 更新、其余日持有;
    P&L 用 (r+lag) -> (r+1+lag)(lag=1 = 信号后一个自然日入场,镜像 crypto_oi_verify._streams 的
    时点假象检验);换手只在 book 实际变化日计。rebalance_rows=全部日行时逐位退化为 verify._streams。"""
    T = len(sig)
    rb = set(rebalance_rows)
    price_r, econ_r, turn_r = [], [], []
    book, prev = {}, {}
    for r in range(T - 1 - lag):
        if r in rb:
            book = _legs(sig[r], q)
        e0, e1 = r + lag, r + 1 + lag
        pr = fu = 0.0
        for i, wi in book.items():
            p0, p1 = px[e0][i], px[e1][i]
            f1 = fund_mat[e1][i] if fund_mat else None
            if p0 is None or p1 is None or p0 <= 0:
                continue
            pr += wi * (p1 / p0 - 1.0)
            if f1 is not None:
                fu += -wi * f1
        names = set(book) | set(prev)
        turn_r.append(sum(abs(book.get(i, 0.0) - prev.get(i, 0.0)) for i in names))
        price_r.append(pr)
        econ_r.append(pr + fu)
        prev = book
    return price_r, econ_r, turn_r


def _ann(sharpe_like_returns, ppy):
    """与 dry_run_pipeline._ann_sharpe 同式、ppy 可变(样本不足/零波动 → 0)。"""
    if len(sharpe_like_returns) < 2:
        return 0.0
    sd = statistics.pstdev(sharpe_like_returns)
    return round(statistics.fmean(sharpe_like_returns) / sd * math.sqrt(ppy), 4) if sd else 0.0


def _mdd(returns):
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        eq *= 1.0 + r
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return round(mdd, 4)


def verdict_for(variant_stages, baseline_stages, bar=None):
    """预注册判定(纯函数):CANDIDATE 当且仅当 4 条门槛全过;失败条名录入 failed_bars。
    variant_stages/baseline_stages = {"t_stat","falsification_passes","vt_sharpe","max_drawdown_vt"}。"""
    bar = bar or VERDICT_BAR
    failed = []
    t = variant_stages.get("t_stat")
    if t is None or t < bar["min_t_stat"]:
        failed.append("t_stat")
    fp, fb = variant_stages.get("falsification_passes"), baseline_stages.get("falsification_passes")
    if fp is None or fb is None or fp < fb:
        failed.append("falsification")
    vs, bs = variant_stages.get("vt_sharpe"), baseline_stages.get("vt_sharpe")
    if vs is None or bs is None or vs < bar["min_vt_sharpe_ratio_vs_baseline"] * bs:
        failed.append("vt_sharpe")
    vd, bd = variant_stages.get("max_drawdown_vt"), baseline_stages.get("max_drawdown_vt")
    if vd is None or bd is None or vd > bar["max_drawdown_vt_ratio_vs_baseline"] * bd:
        failed.append("max_drawdown")
    return {"verdict": "CANDIDATE" if not failed else "REJECTED", "failed_bars": failed}


# ---------------------------------------------------------------------------
# 四个流水线阶段
# ---------------------------------------------------------------------------


def stage_ic(panel, sig, cadence_days):
    """IC 初筛:h=1(与既有 4.32 记录可比的日频口径)+ h=变体节奏(非重叠抽样,诚实 t-stat)。
    判定用后者;两者都记。"""
    close = panel.matrices["close"]
    ics1 = compute_period_ic_series(sig, close)
    agg1 = aggregate_ic_series(ics1, event_times=panel.event_times[:len(ics1)])
    out = {"h1": agg1}
    if int(cadence_days) == 1:
        out["h_cadence"] = agg1
    else:
        icsh, starts = compute_horizon_ic_series(sig, close, int(cadence_days))
        out["h_cadence"] = aggregate_ic_series(
            icsh, event_times=[panel.event_times[i] for i in starts])
    return out


def stage_gauntlet(panel, subset_panels, spec, sig_full, carry_daily, ev_full):
    """与 crypto_oi_verify 同一台 5 项机器检验、同一批阈值,按变体自身 (q, cadence) 构造流。"""
    q, c = spec["quantile"], spec["cadence_days"]
    px, fm = panel.matrices["close"], panel.matrices["funding"]
    rows = cadence_rows(ev_full, c)
    pr, ec, tr = streams_cadence(sig_full, px, fm, q, rows, lag=0)
    out = {"base": {"price_sharpe": round(sharpe(pr), 3), "econ_sharpe": round(sharpe(ec), 3),
                    "avg_turnover_per_day": round(statistics.fmean(tr), 4),
                    "n_rebalances": len(rows)}}
    # 1) 年份广度
    out["per_year_price"] = _per_year(pr, ev_full)
    # 2) 成本扫描(10bps 是机器检验阈值那条)
    out["net_sharpe_by_fee_bps"] = {}
    for fee in (2, 5, 10, 20):
        net = [p - t * fee / 1e4 for p, t in zip(pr, tr)]
        out["net_sharpe_by_fee_bps"][fee] = round(sharpe(net), 3)
    # 3) 执行 lag(0/1/2 自然日;1 是机器检验那条)
    out["price_sharpe_by_lag"] = {}
    for lag in (0, 1, 2):
        pl, _e, _t = streams_cadence(sig_full, px, fm, q, rows, lag=lag)
        out["price_sharpe_by_lag"][lag] = round(sharpe(pl), 3)
    # 4) 子宇宙稳健(同一批 4 个子集,变体表达式在子截面重估)
    out["price_sharpe_by_subset"] = {}
    for name, (panel2, sig2) in subset_panels.items():
        rows2 = cadence_rows(panel2.event_times, c)
        pr2, _e, _t = streams_cadence(sig2, panel2.matrices["close"],
                                      panel2.matrices["funding"], q, rows2)
        out["price_sharpe_by_subset"][name] = {"n": len(panel2.instruments),
                                               "price_sharpe": round(sharpe(pr2), 3)}
    # 5) 对 carry 增量 IR(carry 参照流与 verify 同法:日频 q=0.3;变体侧用自身节奏流)
    inc = incremental_ir(pr, {"carry": tuple(carry_daily)}, min_overlap=120)
    out["incremental_ir_vs_carry"] = None if inc is None else round(inc, 3)

    checks = {
        "survives_1d_lag": out["price_sharpe_by_lag"].get(1, 0) > 0.5,
        "survives_10bps_cost": out["net_sharpe_by_fee_bps"].get(10, 0) > 0.5,
        "broad_across_years": (sum(1 for v in out["per_year_price"].values() if v > 0)
                               >= max(2, len(out["per_year_price"]) - 1)),
        "survives_subsets": bool(out["price_sharpe_by_subset"]) and all(
            v["price_sharpe"] > 0.3 for v in out["price_sharpe_by_subset"].values()),
        "adds_to_carry": (out["incremental_ir_vs_carry"] or 0) > 0.3,
    }
    out["refutation_checks"] = checks
    out["n_pass"] = sum(checks.values())
    return out


def stage_capacity(panel, sig, spec):
    """容量带(与 tools/capacity_research.py 同法同网格;行采样/ppy 按变体节奏)。区间带,非单点。"""
    insts = panel.instruments
    close, volume, funding, ev = (panel.matrices["close"], panel.matrices["volume"],
                                  panel.matrices["funding"], panel.event_times)
    idx = {inst: j for j, inst in enumerate(insts)}
    adv = _adv(insts, close, volume, ev)
    rows = cadence_rows(ev, spec["cadence_days"])
    wk = []
    for r in rows:
        legs = _legs(sig[r], spec["quantile"])
        if legs:
            wk.append((r, {insts[i]: w for i, w in legs.items()}))
    gross, turnover, prevw = [], [], {}
    for k in range(len(wk) - 1):
        r, w = wk[k]
        rn = wk[k + 1][0]
        g = 0.0
        for inst, wi in w.items():
            j = idx[inst]
            p0, p1 = close[r][j], close[rn][j]
            if p0 and p1 and p0 > 0:
                g += wi * (p1 / p0 - 1.0)
            fr = funding[r][j]
            if fr is not None:
                g += -fr * wi
        gross.append(g)
        names = set(w) | set(prevw)
        turnover.append([(abs(w.get(n, 0.0) - prevw.get(n, 0.0)), adv.get(n, 0.0)) for n in names])
        prevw = w
    ppy = ppy_for_cadence(spec["cadence_days"])
    band = capacity_band(gross, turnover, _AUM_GRID, _COEF_LO_HI, ppy=ppy, sharpe_floor=0.3,
                         max_participation=0.10, volume_dispersion_max_ratio=1e6)
    if band.get("data_quality") == "EXTREME_VOLUME_DISPERSION":
        return {"aum_low": None, "aum_high": None, "calibrated_range": {"low": None, "high": None},
                "n_periods": len(gross), "data_quality": "EXTREME_VOLUME_DISPERSION"}
    sens = {}
    for p in _PARTICIP_GRID:
        for h in _HORIZON_GRID:
            b = capacity_band(gross, turnover, _AUM_GRID, _COEF_LO_HI, ppy=ppy, sharpe_floor=0.3,
                              max_participation=p, execution_horizon=h)
            sens[f"p{int(p*100)}_h{h}"] = b["aum_low"]
    vals = [v for v in sens.values() if v is not None]
    return {"aum_low": band["aum_low"], "aum_high": band["aum_high"],
            "impact_aum_low": band["impact_aum_low"], "impact_aum_high": band["impact_aum_high"],
            "participation_cap_aum": band["participation_cap_aum"], "n_periods": len(gross),
            "gross_sharpe": round(band["curves"]["conservative"][0]["net_sharpe"], 3),
            "binding": ("participation" if band["participation_cap_aum"] is not None and
                        (band["impact_aum_high"] is None or band["participation_cap_aum"] < band["impact_aum_high"])
                        else "impact"),
            "sensitivity_particip_horizon": sens,
            "calibrated_range": {"low": min(vals) if vals else None, "high": max(vals) if vals else None},
            "provenance": "UNCALIBRATED_PRINCIPLED", "ppy": ppy, "data_quality": "OK"}


def stage_dry_run(panel, spec, sig):
    """与 tools/dry_run_crowd_contra_multi.py 同一条 run_multi_period_dry_run(10bps、拥挤门、资金费、
    vol-target),参数化 (信号, q, cadence);不写 dry_run.db、不开 event bus。年化按变体节奏重算。"""
    _run, metrics = candidate_dry_run(panel, spec, sig)
    return metrics


def candidate_dry_run(panel, spec, sig, *, event_bus=None):
    """stage_dry_run 的核心,返回 (run, metrics):metrics 与 stage_dry_run 逐位相同;run(DryRunRun)
    暴露给需要持久化的调用方(tools/dry_run_candidate_multi.py 的权威候选干跑)。event_bus 默认 None
    (变体族路径零事件、逐字节不变);权威候选驱动器传入共享总线(与官方 crowd 驱动器同款接线,
    publish_completion_event=False——完成事件由 save_and_project_dry_run 事务性绑定)。
    run.detail 附加 final_weights/candidate_cadence_days(供调用方按节奏重算研究风控;变体族
    调用方丢弃 run,不受影响)。"""
    insts = panel.instruments
    close, fmat, gmat, ev = (panel.matrices["close"], panel.matrices["funding"],
                             panel.matrices["global_ls"], panel.event_times)
    q, c = spec["quantile"], spec["cadence_days"]
    rows = cadence_rows(ev, c)
    periods, weights_by_period, prices_by_period, rate_by_period = [], {}, {}, {}
    gate_decisions, prev_weights = [], None
    for r in rows:
        legs = _legs(sig[r], q)
        if not legs:
            continue
        t = int(ev[r])
        target = {insts[i]: w for i, w in legs.items()}
        crowd = {insts[i]: rank for i, rank in _crowding_rank(gmat, r, list(legs)).items()}
        z_map = {insts[i]: z for i in legs if (z := _crowding_zscore(gmat, r, i)) is not None}
        blocked = False
        if crowd:
            decision = evaluate_crowding_gate(
                target, crowd, average_warn_only=True,
                crowding_z_by_symbol=z_map, max_crowding_z=_MAX_CROWDING_Z)
            gate_decisions.append(decision)
            blocked = decision.status == "BLOCK"
        periods.append(t)
        idx = set(legs)
        if blocked and prev_weights is not None:
            weights_by_period[t] = dict(prev_weights)
            idx |= {i for i in range(len(insts)) if insts[i] in prev_weights}
        else:
            weights_by_period[t] = target
            prev_weights = target
        prices_by_period[t] = {insts[i]: close[r][i] for i in idx if close[r][i] and close[r][i] > 0}
        rate_by_period[t] = {insts[i]: (fmat[r][i] or 0.0) for i in idx}

    held_names = sorted({n for w in weights_by_period.values() for n in w})

    def cash_flow_fn(held, prices, period):
        rates = rate_by_period.get(period, {})
        return sum(-rates.get(i, 0.0) * q_ * prices.get(i, 0.0) for i, q_ in held.items())

    limits = research_dry_run_limits(frozenset(held_names))
    gate = PreTradeRiskGate(limits, kill_switch=KillSwitch())

    def ctx_factory(intent, prices, period):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=_AUM * 10, equity=_AUM * 10, day_pnl=0.0,
                                     peak_equity=_AUM * 10, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=period, est_daily_volume=1e12, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id=spec["id"], approved=True,
                                      approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    ppy = ppy_for_cadence(c)
    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period,
        gate=gate, ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id=spec["id"],
        aum=_AUM, cost_bps=_COST_BPS, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": ppy},
        event_bus=event_bus, publish_completion_event=False)
    # 供持久化调用方按节奏重算 VT 研究风控(管线内部 detail["cadence"] 恒 "weekly" 且
    # research_risk 用默认 config 跑 raw 序列——与官方 crowd 驱动器一样由调用方覆写)。
    run.detail["final_weights"] = dict(weights_by_period.get(periods[-1], {})) if periods else {}
    run.detail["candidate_cadence_days"] = int(c)
    raw_rets = [x["ret"] for x in run.detail["equity_curve"]]
    vt_rets = [x["ret"] for x in run.detail["equity_curve_vt"]]
    crowding = summarize_gate_decisions(gate_decisions)
    metrics = {"n_periods": run.pnl["n_periods"], "n_intents": run.n_intents,
            "n_approved": run.n_approved, "n_rejected": run.n_rejected,
            "total_return": run.pnl["total_return"], "total_funding": run.pnl["total_funding"],
            "reconciliation_residual": run.pnl["residual"],
            "sharpe_raw": _ann(raw_rets, ppy), "max_drawdown_raw": _mdd(raw_rets),
            "sharpe_vt": _ann(vt_rets, ppy), "max_drawdown_vt": _mdd(vt_rets),
            "pipeline_sharpe_ppy52": run.pnl["sharpe"], "pipeline_sharpe_vt_ppy52": run.pnl["sharpe_vt"],
            "crowding_gate": crowding, "ppy": ppy, "cost_bps": _COST_BPS}
    return run, metrics


# ---------------------------------------------------------------------------
# trial 注册(register-then-evaluate,既有组合;按节奏分 3 批,ppy 各按节奏年化)
# ---------------------------------------------------------------------------


def _definition_for(spec):
    return AlphaDefinition(
        alpha_id=spec["id"], name=spec["id"],
        factor_ids=tuple(sorted(set(spec["fields"]) | {"close"})),
        universe="crypto_oi_majors",
        params={"family": _FAMILY, "kind": spec["kind"], "expr": ax.serialize(spec["expr"]),
                "quantile": repr(spec["quantile"]), "cadence_days": str(spec["cadence_days"])})


def _register_family_trials_legacy_diagnostic(
    specs,
    signals,
    panel,
    *,
    registry: ExperimentRegistry,
    recorded_at: int,
    batch_runner,
):
    """17 个候选按节奏分组(7d/3d/14d → ppy 52/122/26)经 run_registered_candidate_batch 注册并过
    评估器。注册先于本驱动器的四阶段流水线(纪律);DSR 等调整口径收进返回 dict。"""
    dataset_version = field_panel_dataset_version(
        panel, source_versions={"family": _FAMILY, "universe": "crypto_oi_majors"})
    out = {"dataset_version": dataset_version, "trial_count_before": registry._store.trial_count(),
           "batches": [], "candidates": {}}
    for cadence in (7, 3, 14):
        group = [s for s in specs if s["cadence_days"] == cadence]
        if not group:
            continue
        candidates = tuple(
            (_definition_for(spec),
             tuple(ax.panel_to_periods(signals[spec["id"]], panel, fwd_horizon=cadence,
                                       non_overlapping=True)))
            for spec in group)
        search_id = make_search_id(
            source=_SOURCE, search_kind=_SEARCH_KIND, dataset_version=dataset_version,
            recorded_at=recorded_at,
            params={"family": _FAMILY, "cadence_days": cadence, "candidate_count": len(candidates)})
        batch = batch_runner(
            candidates, registry=registry, search_id=search_id, search_kind=_SEARCH_KIND,
            search_source=_SOURCE, dataset_version=dataset_version, recorded_at=recorded_at,
            periods_per_year=ppy_for_cadence(cadence),
            config=AlphaFactoryConfig(compute_regime_evidence=True))
        report = batch.get("report", {})
        for spec in group:
            entry = report.get(spec["id"], {})
            ev = entry.get("evaluation")
            out["candidates"][spec["id"]] = {
                "search_id": search_id,
                "evaluator_verdict": ev.verdict.value if ev else None,
                "state": entry.get("state"),
                "deflated_sharpe": None if ev is None else round(ev.deflated_sharpe, 4),
                "ic_tstat": None if ev is None else ev.ic_tstat,
                "net_sharpe": None if ev is None else round(ev.net_sharpe, 4),
                "n_periods": None if ev is None else ev.n_periods,
            }
        out["batches"].append({"cadence_days": cadence, "search_id": search_id,
                               "n_candidates": len(candidates),
                               "registered_candidate_count": batch.get("registered_candidate_count"),
                               "recorded_run_ids": list(batch.get("recorded_run_ids", ())), })
    out["trial_count_after"] = registry._store.trial_count()
    out["trial_count_delta"] = out["trial_count_after"] - out["trial_count_before"]
    return out


def register_family_trials(*_args, **_kwargs):
    """Reject the legacy selection-bearing registration API before all work."""
    quarantine_legacy_backtest("tools.crowd_contra_variants.register_family_trials")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _subset_universes(instruments):
    top4 = {"BTC", "ETH", "BNB", "SOL"}
    return {"drop_BTC": [c for c in instruments if c != "BTC"],
            "drop_top4": [c for c in instruments if c not in top4],
            "even_idx": instruments[::2], "odd_idx": instruments[1::2]}


def _fmt_money(v):
    if v is None:
        return "  n/a "
    return f"${v/1e9:.2f}B" if v >= 1e9 else f"${v/1e6:.1f}M"


def _main(registry: ExperimentRegistry) -> None:
    # This search nominates candidates from the quarantined V1 execution
    # scores.  Registering more trials would contaminate the governed ledger,
    # so block before panel construction or trial registration.
    quarantine_legacy_backtest("crowd_contra_variants_selection")
    recorded_at = int(time.time() * 1000)
    specs = build_variant_specs()
    panel, instruments = build_panel()
    ev = panel.event_times
    print(f"面板: {len(instruments)} 币 × {len(ev)} 日 "
          f"({time.strftime('%Y-%m-%d', time.gmtime(ev[0]/1000))} → "
          f"{time.strftime('%Y-%m-%d', time.gmtime(ev[-1]/1000))})", flush=True)

    # 信号(全面板 + 4 证伪子集面板,子集面板全族共享只建一次)
    signals = {}
    for spec in specs:
        signals[spec["id"]] = ax.evaluate(spec["expr"], panel)
    subset_panels = {}
    for name, sub in _subset_universes(instruments).items():
        if len(sub) >= 5:
            subset_panels[name] = build_panel(only=set(sub))[0]
    subset_signals = {spec["id"]: {name: ax.evaluate(spec["expr"], p2)
                                   for name, p2 in subset_panels.items()}
                      for spec in specs}
    # carry 参照流(与 verify 同法:日频 q=0.3)
    carry_sig = ax.evaluate(CARRY, panel)
    carry_daily, _ce, _ct = streams_cadence(carry_sig, panel.matrices["close"],
                                            panel.matrices["funding"], 0.3,
                                            cadence_rows(ev, 1))

    # ---- Step 0: trial 注册(先于本驱动器四阶段,register-then-evaluate)----
    print("=== trial 注册(17 候选 × 3 节奏批)===", flush=True)
    registration = register_family_trials(specs, signals, panel, registry=registry,
                                          recorded_at=recorded_at)
    print(f"  trial_count {registration['trial_count_before']} → {registration['trial_count_after']} "
          f"(Δ {registration['trial_count_delta']})", flush=True)

    # ---- 四阶段流水线(每变体全跑;硬失败短路记阶段)----
    results = {}
    for spec in specs:
        vid = spec["id"]
        print(f"\n--- {vid} (q={spec['quantile']}, {spec['cadence_days']}d, {spec['kind']}) ---",
              flush=True)
        stages, stage_reached, error = {}, "none", None
        try:
            stage_reached = "ic"
            stages["ic"] = stage_ic(panel, signals[vid], spec["cadence_days"])
            stage_reached = "falsification"
            stages["falsification"] = stage_gauntlet(
                panel, {n: (p2, subset_signals[vid][n]) for n, p2 in subset_panels.items()},
                spec, signals[vid], carry_daily, ev)
            stage_reached = "capacity"
            stages["capacity"] = stage_capacity(panel, signals[vid], spec)
            stage_reached = "dry_run"
            stages["dry_run"] = stage_dry_run(panel, spec, signals[vid])
            stage_reached = "complete"
        except Exception as exc:  # 硬失败:短路,记到达阶段(统计弱势不短路)
            error = f"{type(exc).__name__}: {exc}"
        results[vid] = {"spec": {k: (ax.serialize(v) if k == "expr" else v)
                                 for k, v in spec.items() if k != "rationale"},
                        "stages": stages, "stage_reached": stage_reached, "error": error}
        dr = stages.get("dry_run") or {}
        ga = stages.get("falsification") or {}
        icc = (stages.get("ic") or {}).get("h_cadence") or {}
        print(f"  IC(h={spec['cadence_days']}): mean={icc.get('mean_ic')} t={icc.get('t_stat')} · "
              f"证伪 {ga.get('n_pass')}/5 · VT 夏普 {dr.get('sharpe_vt')} · 回撤 {dr.get('max_drawdown_vt')}"
              + (f" · ERROR {error}" if error else ""), flush=True)

    # ---- 判定(相对本次基线)----
    def _judge_inputs(vid):
        st = results[vid]["stages"]
        return {"t_stat": ((st.get("ic") or {}).get("h_cadence") or {}).get("t_stat"),
                "falsification_passes": (st.get("falsification") or {}).get("n_pass"),
                "vt_sharpe": (st.get("dry_run") or {}).get("sharpe_vt"),
                "max_drawdown_vt": (st.get("dry_run") or {}).get("max_drawdown_vt")}

    baseline_inputs = _judge_inputs("crowd_contra")
    verdicts = {}
    for spec in specs:
        vid = spec["id"]
        if results[vid]["stage_reached"] != "complete":
            verdicts[vid] = {"verdict": "REJECTED", "failed_bars": ["hard_failure"],
                             "stage_reached": results[vid]["stage_reached"]}
            continue
        v = verdict_for(_judge_inputs(vid), baseline_inputs)
        v["stage_reached"] = "complete"
        verdicts[vid] = v

    # ---- 锚定交叉检验:基线 cadence=1 应复现 crypto_oi_verify.json 的机器检验 ----
    anchor_spec = dict(specs[0])
    anchor = stage_gauntlet(panel, {n: (p2, ax.evaluate(CROWD_CONTRA, p2))
                                    for n, p2 in subset_panels.items()},
                            {**anchor_spec, "quantile": 0.3, "cadence_days": 1},
                            ax.evaluate(CROWD_CONTRA, panel), carry_daily, ev)

    n_candidates = sum(1 for v in verdicts.values() if v["verdict"] == "CANDIDATE")
    candidates = [vid for vid, v in verdicts.items()
                  if v["verdict"] == "CANDIDATE" and vid != "crowd_contra"]
    payload = {
        "schema_version": 1,
        "evidence_class": "RESEARCH_VARIANT_FAMILY_PREREGISTERED",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": recorded_at // 1000,
        "pre_registration": {**PRE_REGISTRATION,
                             "variants": [{"id": s["id"], "role": s["role"], "kind": s["kind"],
                                           "expression": ax.serialize(s["expr"]),
                                           "quantile": s["quantile"], "cadence_days": s["cadence_days"],
                                           "rationale": s["rationale"]} for s in specs]},
        "dataset": {"universe": "crypto_oi_majors", "n_instruments": len(instruments),
                    "instruments": instruments, "n_event_times": len(ev),
                    "date_start": time.strftime("%Y-%m-%d", time.gmtime(ev[0] / 1000)),
                    "date_end": time.strftime("%Y-%m-%d", time.gmtime(ev[-1] / 1000)),
                    "dataset_version": registration["dataset_version"]},
        "trial_registration": registration,
        "results": results,
        "baseline_inputs": baseline_inputs,
        "verdicts": verdicts,
        "candidates": candidates,
        "harness_anchor_check": {
            "note": "基线 cadence=1 证伪应复现 data/cache/crypto_oi_verify.json 的 5 项机器检验",
            "refutation_checks": anchor["refutation_checks"], "n_pass": anchor["n_pass"],
            "price_sharpe_lag0": anchor["base"]["price_sharpe"],
            "net_sharpe_10bps": anchor["net_sharpe_by_fee_bps"].get(10),
            "incremental_ir_vs_carry": anchor["incremental_ir_vs_carry"]},
        "honesty": {
            "trial_count": 17,
            "trial_count_including_baseline": True,
            "registry_trial_count_before": registration["trial_count_before"],
            "registry_trial_count_after": registration["trial_count_after"],
            "multiple_comparison_note": (
                "17 次试验同属一个信号族、book 高度相关(同一 global_ls 主轴)——有效独立赌注数远小于 17;"
                "DSR(评估器按全 trial 总体泄气,Deflated Sharpe)逐候选见 trial_registration.candidates;"
                "有效独立 N 无既有机器 → 诚实陈述而非编造。判定门槛已预注册(pre_registration.verdict_bar),"
                "族判定相对本次重跑基线,非绝对值。"),
            "dsr_batching_note": (
                "注册按节奏分 3 批(7d→3d→14d):后批评估时可见 trial 更多、DSR 泄气更狠(保守方向);"
                "7d 批 8 候选、3d 批 5、14d 批 4。"),
            "caveats": [
                "对已知历史的执行现实回测,非前向/实盘;是否送前向 paper-track 是另一独立决策。",
                "干跑变体不落 dry_run.db(不污染正典库);数字以本文件为准。",
                "容量带 UNCALIBRATED_PRINCIPLED(冲击系数无法校准,区间带非单点)。",
                "基线证伪按周度交易口径重跑,与 crypto_oi_verify.json 的日频口径数字不逐位一致属预期"
                "(见 harness_anchor_check 的日频锚定复现)。",
                "近期资金费压缩(1.78bp/日 vs 全历史 4.81)属 carry 维度;crowd_contra 是价格方向信号,"
                "其 funding 现金流项较小,但复合变体 crowd_carry_blend 直接暴露于该压缩。",
            ],
        },
    }
    atomic_write_json(_OUT, payload, indent=2)

    print("\n=== 判定汇总(相对本次基线)===", flush=True)
    print(f"基线: t={baseline_inputs['t_stat']} 证伪 {baseline_inputs['falsification_passes']}/5 "
          f"VT {baseline_inputs['vt_sharpe']} DD {baseline_inputs['max_drawdown_vt']}", flush=True)
    for spec in specs:
        vid = spec["id"]
        v = verdicts[vid]
        ji = _judge_inputs(vid)
        print(f"  {vid:24s} {v['verdict']:9s} t={ji['t_stat']} 证伪{ji['falsification_passes']}"
              f" VT={ji['vt_sharpe']} DD={ji['max_drawdown_vt']} "
              f"failed={v['failed_bars'] or '-'}", flush=True)
    print(f"\nCANDIDATES({n_candidates - (1 if verdicts['crowd_contra']['verdict'] == 'CANDIDATE' else 0)}"
          f" 非基线): {candidates or 'NONE(诚实零候选)'}", flush=True)
    print(f"-> {_OUT}", flush=True)


def main() -> None:
    # Block before opening the production experiment registry.  The inner
    # guard remains as defense in depth for direct orchestration calls.
    quarantine_legacy_backtest("crowd_contra_variants_selection")
    with production_experiment_registry() as registry:
        _main(registry)


if __name__ == "__main__":
    main()
