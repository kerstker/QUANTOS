"""预注册 MEAN-REVERSION 策略簇全生命周期驱动器(Profit Maximization spec §6 / W2 slice 1:
平台缺失的第 5 簇)。机构标准:先看 pre-registration,再看任何结果;每个变体评估都记为一次 trial;
绝不静默挑樱桃。与 tools/crowd_contra_variants.py 同一套 trial 纪律(预注册块 / 判定门槛先于结果 /
DSR trial 记账 / 诚实拒绝)。

簇定义(spec §6):Mean Reversion = 短期残差回归、价差回复 —— 盘口、短周期 bars、残差;
首要风险: 结构变化、趋势碾压。**数据诚实约束(预注册声明):本地无盘口/order-book、无分钟级
intraday bars——微观结构 MR(盘口价差回复、短周期 bars)按定义出范围,不检验、不声称。** 存在的
数据:~115 币日频 OHLCV(data/cache/crypto_funding.db,经显式 t+1d available-time 批量读)+ 资金费/OI
JSON。因此本簇在**日频 horizon** 构造:① 残差均值回归(币日收益 − 等权市场日收益的残差,k 日
累积,2–10 日回归窗)② 截面短期反转基线。

变体族(2026-07-24 预注册,先于任何评估写入 build_variant_specs;8 个 spec,全周度调仓 q=0.30):
  trial #0 mr_cs_rev_5d(基线:最简截面 5 日反转——残差版必须打败它才证明残差复杂度值得);
  mr_resid_rev_2d/3d/5d/10d(残差累积窗扫描);mr_resid_z_5d(残差被 trailing 20d 残差波动
  归一——波动归一稳健性);mr_cs_rev_z_5d(同归一作用于**plain** 反转——分离「残差性」与
  「波动归一」两个自由度);mr_resid_beta_5d(beta 调整残差:trailing 20d ts_beta 对市场,
  检验 beta=1 等权残差是否过粗)。

四条流水线阶段全部复用既有引擎、零新数学:
  1. IC 初筛:services/research/ic_panel.py(h=1 日频口径 + h=7 周度非重叠诚实 t-stat;判定用后者)。
  2. 证伪电池:与 tools/crypto_oi_verify.py **同一台 5 项机器检验、同一批通过阈值**
     (lag>0.5 / net@10bps>0.5 / 年份广度 / 子宇宙>0.3 / 对 carry 增量 IR>0.3),流构造复用
     tools/crowd_contra_variants.py::streams_cadence(周度 book 持有 + lag 时点检验)。适配(已记录):
     carry 参照流 = tools/crypto_carry_strategy.py::COMPOSITE 在**同一 115 币面板**上的日频 q=0.3
     价格流(verify 在 OI 宇宙上用 OI 版 CARRY;本簇宇宙不同,参照必须同宇宙,增量 IR 才诚实);
     拥挤门(global_ls z>3.0)**不接**——OI 持仓数据只覆盖 ~24 大币,115 币面板上不存在该证据
     (dry-run 仍过 fail-closed 预交易门;不接 ≠ 绕过,是证据不存在,已记录);quantile 稳健性扫描
     (verify 第 3 项诊断、非 5 项机器检验)省略——全族 q=0.30 固定,分位非本族变体轴。
  3. 容量:tools/capacity_research.py 同法 Almgren-Chriss + 参与率带上限(区间带,非单点;
     UNCALIBRATED_PRINCIPLED),ppy=52,毛收益纯价格(与干跑 cash_flow_fn=None 同口径)。
  4. 周度滚动干跑:services/execution/dry_run_pipeline.py::run_multi_period_dry_run(10bps、
     整手、fail-closed 预交易门;cash_flow_fn=None——本簇全是价格信号,非资金费条件 variant,
     按预注册不带资金费现金流;raw+vol-target 双曲线)。**变体不写 dry_run.db**——正典库只留
     官方驱动器的 5 条策略;数字只落本证据文件。

判定门槛(预注册,见 VERDICT_BAR;比 naive 初筛更严——这是**新簇**主张):CANDIDATE 当且仅当
(a) 周度非重叠 IC t-stat ≥ 2.5;(b) 证伪机器检验 ≥ 4/5;(c) 干跑 VT 夏普 ≥ 1.0 且 VT 最大回撤
≤ 0.20;(d) VT 夏普**严格大于**本次基线 mr_cs_rev_5d(残差必须为复杂度付费;基线自身豁免此条);
(e) 容量带可计算且诚实(data_quality OK 且 aum_low 非 None)。其余一律 REJECTED 并记录失败条。
硬失败(计算异常)短路,记录到达阶段;统计弱势不短路——4 个阶段对每个变体都跑完。

Trial 纪律:8 个候选经 run_registered_candidate_batch(register-then-evaluate)单批(全 7d 节奏,
ppy 52)注册进 experiment registry 并过评估器——DSR(按全 trial 总体泄气)逐候选收进 honesty 节。

**W10 研究预算(spec §14.1/§14.2,2026-07-25)**:本驱动器是 ResearchBudget 契约的模板采用者——
RESEARCH_BUDGET(max_trials=8=本族实际大小 + max_feature_count=1 + max_parameter_dimensions=5 +
allowed_data_sources)+ PARENT_HYPOTHESIS + SEARCH_SPACE 在注册点声明;注册 API 在任何落盘前
fail-closed 校验(族长超过声明 = BudgetExhausted,一字节不进台账),预算声明+逐维校验结果作为
additive 字段记入 trial_registration.budget_declaration(下次真实运行落进证据)。**诚实注记:本族
8 个 trial 已在预算契约落地前的制度下消耗完毕(2026-07-24 首跑,registry 2459→2467);预算是
前向约束——约束本族未来的任何扩员/重跑声明,不回溯改写既有 trial 历史。** compute 小时数注册处
不可计量,故不声明该维(只声明可验证维度);locked_oos 本族不声明(无独立锁定 OOS 窗)。

确定性:同输入 → 同数字;无 RNG;墙钟仅在 main 边界(registry 时间戳/generated_at)。
无网络、无真单、无凭证。实盘晋级 = 另一独立人工决策 = 非目标(本驱动器绝不碰 _STRATEGIES/mandate/
attestation/live 治理文件;CANDIDATE 只提名进 tools/forward_track_advance.py 前向 paper 台账)。
"""
from __future__ import annotations

import math
import os
import statistics
import time

from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.factor_set import incremental_ir
from services.research.ic_panel import (aggregate_ic_series, compute_horizon_ic_series,
                                        compute_period_ic_series)
from services.research.realism.capacity_curve import capacity_band
from services.research.research_budget import ResearchBudget
from services.risk.pre_trade.config import RiskLimits
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
from tools.crowd_contra_variants import _ann, _mdd, cadence_rows, streams_cadence
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE
from tools.crypto_oi_research import _legs
from tools.crypto_oi_verify import _per_year
from tools.ic_panel_registry import _build as _pit_build, _load_json
from tools.search_trial_registry import (field_panel_dataset_version, make_search_id,
                                         production_experiment_registry)

_OUT = os.path.join("evidence", "mean_reversion_cluster.json")
_AUM = 1_000_000.0
_COST_BPS = 10.0            # 与官方干跑/证伪 net@10bps 那条证据同一平费假设
_QUANTILE = 0.30            # 全族固定(预注册):分位非本族变体轴
_CADENCE_DAYS = 7           # 全族固定(预注册):周度调仓
_VOL_WINDOW = 20            # trailing 20d 残差/收益波动窗(mr_resid_z_5d / mr_cs_rev_z_5d)
_BETA_WINDOW = 20           # trailing 20d beta 窗(mr_resid_beta_5d)
_FAMILY = "mean_reversion_cluster_v1"
_SEARCH_KIND = "MEAN_REVERSION_CLUSTER_FAMILY"
_SOURCE = "tools.mean_reversion_cluster"
_UNIVERSE = "crypto_funding_115"

# ---------------------------------------------------------------------------
# PRE-REGISTRATION(2026-07-24,先于任何评估写入;改动本块 = 新实验,须换 _FAMILY 版本号)
# ---------------------------------------------------------------------------

_CLOSE = ("field", "close")


def _ret(k: int):
    """k 日简单收益:close/close[-k] − 1(全窗缺失 → None,fail-closed 传播)。"""
    return ("sub", ("div", _CLOSE, ("ts_delay", _CLOSE, k)), ("const", 1.0))


_RET1 = _ret(1)
# 日残差 = 币日收益 − 等权市场日收益(cs_demean:同一行截面去均值,PIT-safe)。
_RESID1 = ("cs_demean", _RET1)
# 等权市场日收益(按行广播):ret − (ret − mean) = mean。供 beta 变体做回归自变量。
_MKT1 = ("sub", _RET1, ("cs_demean", _RET1))
# beta 调整日残差:ret_i − β_i(20d)·mkt(ts_beta 读自身历史,无未来数据)。
_RESID_B1 = ("sub", _RET1, ("mul", ("ts_beta", _RET1, _MKT1, _BETA_WINDOW), _MKT1))


def _resid_rev(k: int):
    """残差均值回归信号:k 日累积残差取负做截面 rank —— 做多对市场跌得最多的、做空涨得最多的。"""
    return ("cs_rank", ("neg", ("ts_sum", _RESID1, k)))


MR_CS_REV_5D = ("cs_rank", ("neg", _ret(5)))
MR_RESID_Z_5D = ("cs_rank", ("neg", ("div", ("ts_sum", _RESID1, 5),
                                   ("ts_std", _RESID1, _VOL_WINDOW))))
MR_CS_REV_Z_5D = ("cs_rank", ("neg", ("div", _ret(5), ("ts_std", _RET1, _VOL_WINDOW))))
MR_RESID_BETA_5D = ("cs_rank", ("neg", ("ts_sum", _RESID_B1, 5)))

# 预注册判定门槛(比 naive 初筛更严——新簇主张)。maxDD 取 vol-target(可部署形态)曲线,与 (c) 同口径。
VERDICT_BAR = {
    "min_t_stat": 2.5,
    "t_stat_horizon": "weekly_non_overlapping_h7",
    "min_falsification_passes": 4,
    "min_vt_sharpe": 1.0,
    "max_drawdown_vt": 0.20,
    "must_beat_baseline_vt_sharpe": True,   # 残差必须为复杂度付费;基线自身豁免此条
    "capacity_required": True,
}


def _spec(vid, kind, expr, rationale, role="variant"):
    return {"id": vid, "role": role, "kind": kind, "expr": expr,
            "quantile": _QUANTILE, "cadence_days": _CADENCE_DAYS, "rationale": rationale}


def build_variant_specs():
    """8 个预注册 spec(trial #0 = plain 反转基线;残差族判定相对它)。每个一句话理由。"""
    specs = [
        _spec("mr_cs_rev_5d", "baseline", MR_CS_REV_5D,
              "trial #0 基线:最简截面 5 日反转——残差版必须打败它才证明残差复杂度值得",
              role="baseline"),
        _spec("mr_resid_rev_2d", "horizon", _resid_rev(2),
              "回归窗快端:日频 MR 若衰减快,2 日残差最纯但换手/成本最高——成本压力最大的形态"),
        _spec("mr_resid_rev_3d", "horizon", _resid_rev(3),
              "2d 与 5d 之间的衰减/换手权衡点;spec 回归窗 2–10 日的次快格"),
        _spec("mr_resid_rev_5d", "horizon", _resid_rev(5),
              "回归窗中点(5 日 ≈ 一个交易周):信号 horizon 与周度调仓节奏匹配"),
        _spec("mr_resid_rev_10d", "horizon", _resid_rev(10),
              "回归窗慢端:残差若慢回归,10 日窗换手减半;若信号已衰减则稀释——两个方向都信息量大"),
        _spec("mr_resid_z_5d", "vol_norm", MR_RESID_Z_5D,
              "残差被 trailing 20d 残差波动归一再排名:高波币的同等残差不算同等偏离——波动归一稳健性"),
        _spec("mr_cs_rev_z_5d", "vol_norm", MR_CS_REV_Z_5D,
              "同一归一作用于 **plain** 5 日反转:分离「残差性」与「波动归一」两个自由度——"
              "若它也打败基线,则有效成分是归一而非残差"),
        _spec("mr_resid_beta_5d", "beta_adj", MR_RESID_BETA_5D,
              "beta 调整残差(trailing 20d ts_beta 对市场):beta=1 等权残差对高 beta 币过粗——"
              "检验市场剥离精度是否是有效成分"),
    ]
    ids = [s["id"] for s in specs]
    assert len(ids) == 8 and len(set(ids)) == 8, "预注册族必须是 8 个唯一 id"
    assert sum(1 for s in specs if s["role"] == "baseline") == 1
    return tuple(specs)


PRE_REGISTRATION = {
    "family": _FAMILY,
    "registered_before_evaluation": True,
    "date": "2026-07-24",
    "trial_count_planned": 8,
    "verdict_bar": dict(VERDICT_BAR),
    "data_scope": {
        "in_scope": "日频残差均值回归(2–10 日回归窗)+ 截面短期反转,115 币日频 OHLCV(t+1d available-time)",
        "out_of_scope": ("微观结构 MR(盘口价差回复、短周期 bars)——本地无 order-book/分钟级数据,"
                         "按定义不检验、不声称;spec『盘口、短周期 bars』一支留作数据到位后的后续 slice"),
    },
    "adaptations": [
        "carry 参照流 = crypto_carry_strategy.COMPOSITE 在同一 115 币面板上的日频 q=0.3 价格流"
        "(verify 在 OI 宇宙用 OI 版 CARRY;增量 IR 必须同宇宙才诚实)。",
        "拥挤门(global_ls z>3.0 BLOCK)不接:OI 持仓数据只覆盖 ~24 大币,115 币面板无此证据;"
        "dry-run 仍过 fail-closed 预交易门。不接 ≠ 绕过。",
        "quantile 稳健性扫描(verify 第 3 项诊断,非 5 项机器检验)省略——全族 q=0.30 固定,分位非变体轴。",
        "证伪 econ 流仍含资金费(与 verify 同法,供参照);5 项机器检验全用价格流,与 verify 一致;"
        "干跑 cash_flow_fn=None(全族价格信号,预注册声明非资金费条件)。",
        "容量毛收益纯价格(与干跑同口径);ppy=52;AUM 网格/冲击系数带/参与率网格与 capacity_research 相同。",
        "变体不写 dry_run.db(正典库只留官方 5 条策略);变体数字只落本证据文件。",
        "基线 mr_cs_rev_5d 豁免判定条 (d)(它是被比较对象);CANDIDATE 提名进前向 paper 不含基线"
        "(基线若独立过 (a)-(c)+(e) 也如实记录 verdict,但前向提名只给打败基线的残差版——"
        "否则前向钟去测一个已知更弱的形态,浪费前向预算)。",
    ],
}

# ---------------------------------------------------------------------------
# W10 研究预算声明(spec §14.1/§14.2):注册点 fail-closed 强制;改动 = 新实验,
# 须同步抬预算并记录。只声明注册方能验证的维度(compute_hours 不可计量 → 不声明;
# 无独立锁定 OOS 窗 → locked_oos_period 不声明)。
# ---------------------------------------------------------------------------

RESEARCH_BUDGET = ResearchBudget(
    max_trials=8,                       # 本族实际大小(预注册 8 spec;扩员 = 新实验须抬预算)
    max_feature_count=1,                # 全族只用 close 字段(预注册声明)
    max_parameter_dimensions=5,         # _definition_for params: family/kind/expr/quantile/cadence
    allowed_data_sources=("crypto_funding.db",),   # 主 PIT 存储;funding/rates JSON 为其衍生快照
)
PARENT_HYPOTHESIS = (
    "日频残差均值回归/截面短期反转在 115 币日频宇宙含有独立于 carry/crowd/trend 的可交易边;"
    "残差构造必须打败 plain 5 日反转基线,才证明残差复杂度值得(spec §6 第 5 簇假设)。"
)
SEARCH_SPACE = (
    "8 个预注册变体(q=0.30、7d 节奏固定,非变体轴):残差累积窗 k∈{2,3,5,10} 截面 rank"
    " + trailing 20d 波动归一{残差版,plain 版} + trailing 20d beta 调整残差;"
    "微观结构 MR(盘口/分钟级)按预注册声明出范围(本地无该数据)。"
)

# ---------------------------------------------------------------------------
# 纯函数核心(可单测)
# ---------------------------------------------------------------------------


def verdict_for(variant_stages, baseline_vt_sharpe, *, role="variant", bar=None):
    """预注册判定(纯函数):CANDIDATE 当且仅当 5 条门槛全过;失败条名录入 failed_bars。
    variant_stages = {"t_stat","falsification_passes","vt_sharpe","max_drawdown_vt",
                      "capacity_aum_low","capacity_data_quality"}。
    role="baseline" 豁免 vs_baseline_vt 条(它是被比较对象);None 一律 fail-closed。"""
    bar = bar or VERDICT_BAR
    failed = []
    t = variant_stages.get("t_stat")
    if t is None or t < bar["min_t_stat"]:
        failed.append("t_stat")
    fp = variant_stages.get("falsification_passes")
    if fp is None or fp < bar["min_falsification_passes"]:
        failed.append("falsification")
    vs = variant_stages.get("vt_sharpe")
    if vs is None or vs < bar["min_vt_sharpe"]:
        failed.append("vt_sharpe")
    dd = variant_stages.get("max_drawdown_vt")
    if dd is None or dd > bar["max_drawdown_vt"]:
        failed.append("max_drawdown")
    if role != "baseline" and (vs is None or baseline_vt_sharpe is None or vs <= baseline_vt_sharpe):
        failed.append("vs_baseline_vt")
    if not (variant_stages.get("capacity_data_quality") == "OK"
            and variant_stages.get("capacity_aum_low") is not None):
        failed.append("capacity")
    return {"verdict": "CANDIDATE" if not failed else "REJECTED", "failed_bars": failed}


def stream_correlation_matrix(price_streams):
    """变体日频价格流两两 Pearson 相关(honesty 节:同族 book 相关性,DSR 定性依据)。
    price_streams = {id: stream};返回 {id: {id: rho}}(对角 1.0;重叠 < 120 → None)。"""
    ids = sorted(price_streams)
    out = {}
    for a in ids:
        row = {}
        for b in ids:
            if a == b:
                row[b] = 1.0
                continue
            xs, ys = tuple(price_streams[a]), tuple(price_streams[b])
            n = min(len(xs), len(ys))
            if n < 120:
                row[b] = None
                continue
            xa, ya = xs[:n], ys[:n]
            ma, mb = statistics.fmean(xa), statistics.fmean(ya)
            da = sum((x - ma) ** 2 for x in xa) ** 0.5
            db = sum((y - mb) ** 2 for y in ya) ** 0.5
            row[b] = None if da <= 0 or db <= 0 else round(
                sum((x - ma) * (y - mb) for x, y in zip(xa, ya)) / (da * db), 4)
        out[a] = row
    return out


# ---------------------------------------------------------------------------
# 四个流水线阶段
# ---------------------------------------------------------------------------


def stage_ic(panel, sig):
    """IC 初筛:h=1(日频口径)+ h=7(周度非重叠,诚实 t-stat;判定用后者;两者都记)。"""
    close = panel.matrices["close"]
    ics1 = compute_period_ic_series(sig, close)
    out = {"h1": aggregate_ic_series(ics1, event_times=panel.event_times[:len(ics1)])}
    ics7, starts = compute_horizon_ic_series(sig, close, 7)
    out["h7"] = aggregate_ic_series(ics7, event_times=[panel.event_times[i] for i in starts])
    return out


def stage_gauntlet(panel, subset_panels, spec, sig_full, carry_daily, ev_full):
    """与 crypto_oi_verify 同一台 5 项机器检验、同一批阈值;流构造 = streams_cadence(周度 book)。"""
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
    # 5) 对 carry 增量 IR(carry 参照流 = 同宇宙 COMPOSITE 日频 q=0.3 价格流)
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
    """容量带(与 tools/capacity_research.py 同法同网格;毛收益纯价格,与干跑同口径)。区间带,非单点。"""
    insts = panel.instruments
    close, volume, ev = panel.matrices["close"], panel.matrices["volume"], panel.event_times
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
        gross.append(g)
        names = set(w) | set(prevw)
        turnover.append([(abs(w.get(n, 0.0) - prevw.get(n, 0.0)), adv.get(n, 0.0)) for n in names])
        prevw = w
    ppy = 52
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
    """同一条 run_multi_period_dry_run(10bps、整手、fail-closed 预交易门、vol-target);
    cash_flow_fn=None(价格信号,预注册声明);不接拥挤门(115 币面板无 OI 证据,已记录)。
    不写 dry_run.db、不开 event bus。"""
    insts = panel.instruments
    close, ev = panel.matrices["close"], panel.event_times
    q, c = spec["quantile"], spec["cadence_days"]
    rows = cadence_rows(ev, c)
    periods, weights_by_period, prices_by_period = [], {}, {}
    for r in rows:
        legs = _legs(sig[r], q)
        if not legs:
            continue
        t = int(ev[r])
        periods.append(t)
        weights_by_period[t] = {insts[i]: w for i, w in legs.items()}
        prices_by_period[t] = {insts[i]: close[r][i] for i in legs
                               if close[r][i] and close[r][i] > 0}

    held_names = sorted({n for w in weights_by_period.values() for n in w})
    limits = RiskLimits(
        allowed_instruments=frozenset(held_names), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7, max_single_name_pct=1e7,
        max_turnover=1e7, max_participation_rate=1e7, max_instrument_vol=5.0, daily_loss_limit=1e14,
        max_drawdown=1.0)
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

    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period,
        gate=gate, ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id=spec["id"],
        aum=_AUM, cost_bps=_COST_BPS, cash_flow_fn=None,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52},
        event_bus=None, publish_completion_event=False)
    raw_rets = [x["ret"] for x in run.detail["equity_curve"]]
    vt_rets = [x["ret"] for x in run.detail["equity_curve_vt"]]
    return {"n_periods": run.pnl["n_periods"], "n_intents": run.n_intents,
            "n_approved": run.n_approved, "n_rejected": run.n_rejected,
            "total_return": run.pnl["total_return"],
            "reconciliation_residual": run.pnl["residual"],
            "sharpe_raw": _ann(raw_rets, 52), "max_drawdown_raw": _mdd(raw_rets),
            "sharpe_vt": _ann(vt_rets, 52), "max_drawdown_vt": _mdd(vt_rets),
            "pipeline_sharpe_ppy52": run.pnl["sharpe"], "pipeline_sharpe_vt_ppy52": run.pnl["sharpe_vt"],
            "ppy": 52, "cost_bps": _COST_BPS}


# ---------------------------------------------------------------------------
# trial 注册(register-then-evaluate,既有组合;单批,ppy 52)
# ---------------------------------------------------------------------------


def _definition_for(spec):
    return AlphaDefinition(
        alpha_id=spec["id"], name=spec["id"],
        factor_ids=("close",),                    # MR 信号只用价格字段(预注册声明)
        universe=_UNIVERSE,
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
    """8 个候选单批(全 7d 节奏,ppy 52)经 run_registered_candidate_batch 注册并过评估器。
    注册先于本驱动器的四阶段流水线(纪律);DSR 等调整口径收进返回 dict。
    W10 §14.2:RESEARCH_BUDGET + PARENT_HYPOTHESIS + SEARCH_SPACE 随批声明——注册 API
    在任何落盘前 fail-closed 校验(族长/特征数/参数维/数据源),预算声明与逐维校验
    结果作为 additive 字段收进 out["budget_declaration"](随证据落盘)。"""
    dataset_version = field_panel_dataset_version(
        panel, source_versions={"family": _FAMILY, "universe": _UNIVERSE})
    out = {"dataset_version": dataset_version, "trial_count_before": registry._store.trial_count()}
    candidates = tuple(
        (_definition_for(spec),
         tuple(ax.panel_to_periods(signals[spec["id"]], panel, fwd_horizon=7, non_overlapping=True)))
        for spec in specs)
    search_id = make_search_id(
        source=_SOURCE, search_kind=_SEARCH_KIND, dataset_version=dataset_version,
        recorded_at=recorded_at,
        params={"family": _FAMILY, "cadence_days": _CADENCE_DAYS, "candidate_count": len(candidates)})
    batch = batch_runner(
        candidates, registry=registry, search_id=search_id, search_kind=_SEARCH_KIND,
        search_source=_SOURCE, dataset_version=dataset_version, recorded_at=recorded_at,
        periods_per_year=52,
        config=AlphaFactoryConfig(compute_regime_evidence=True),
        budget=RESEARCH_BUDGET, parent_hypothesis=PARENT_HYPOTHESIS,
        search_space=SEARCH_SPACE, data_source="crypto_funding.db")
    out["budget_declaration"] = batch.get("budget_declaration")
    report = batch.get("report", {})
    out["candidates"] = {}
    for spec in specs:
        entry = report.get(spec["id"], {})
        ev = entry.get("evaluation")
        out["candidates"][spec["id"]] = {
            "evaluator_verdict": ev.verdict.value if ev else None,
            "state": entry.get("state"),
            "deflated_sharpe": None if ev is None else round(ev.deflated_sharpe, 4),
            "ic_tstat": None if ev is None else ev.ic_tstat,
            "net_sharpe": None if ev is None else round(ev.net_sharpe, 4),
            "n_periods": None if ev is None else ev.n_periods,
        }
    out["search_id"] = search_id
    out["n_candidates"] = len(candidates)
    out["registered_candidate_count"] = batch.get("registered_candidate_count")
    out["recorded_run_ids"] = list(batch.get("recorded_run_ids", ()))
    out["trial_count_after"] = registry._store.trial_count()
    out["trial_count_delta"] = out["trial_count_after"] - out["trial_count_before"]
    return out


def register_family_trials(*_args, **_kwargs):
    """Reject the legacy selection-bearing registration API before all work."""
    quarantine_legacy_backtest("tools.mean_reversion_cluster.register_family_trials")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _mr_panel(only=None):
    """115 币日频 close+volume+funding 面板(PIT):复用 tools/ic_panel_registry._build，
    Binance close 仅在 t+1d 决策时点可用且旧 open-time vintage 被拒绝。funding 供 carry
    参照流与证伪 econ 流(与 verify 同法)。"""
    instruments = sorted(_load_json("crypto_funding_snapshot.json")["instruments"])
    if only is not None:
        instruments = [i for i in instruments if i in only]
    panel = _pit_build(
        "crypto_funding.db",
        instruments,
        fields=("close", "volume"),
        decision_lag=86_400_000,
        minimum_knowledge_lag=86_400_000,
    )
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in _load_json("crypto_funding_rates.json").items()}
    panel.matrices["funding"] = [[funding.get(i, {}).get(t) for i in panel.instruments]
                                 for t in panel.event_times]
    return panel


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
    quarantine_legacy_backtest("tools.mean_reversion_cluster")
    recorded_at = int(time.time() * 1000)
    specs = build_variant_specs()
    panel = _mr_panel()
    instruments = list(panel.instruments)
    ev = panel.event_times
    print(f"面板: {len(instruments)} 币 × {len(ev)} 日 "
          f"({time.strftime('%Y-%m-%d', time.gmtime(ev[0]/1000))} → "
          f"{time.strftime('%Y-%m-%d', time.gmtime(ev[-1]/1000))})", flush=True)

    # 信号(全面板 + 4 证伪子集面板,子集面板全族共享只建一次)
    signals = {spec["id"]: ax.evaluate(spec["expr"], panel) for spec in specs}
    subset_panels = {}
    for name, sub in _subset_universes(instruments).items():
        if len(sub) >= 5:
            subset_panels[name] = _mr_panel(only=set(sub))
    subset_signals = {spec["id"]: {name: ax.evaluate(spec["expr"], p2)
                                   for name, p2 in subset_panels.items()}
                      for spec in specs}
    # carry 参照流(同宇宙 COMPOSITE,日频 q=0.3 价格流;与 verify 同构)
    carry_sig = ax.evaluate(CARRY_COMPOSITE, panel)
    carry_daily, _ce, _ct = streams_cadence(carry_sig, panel.matrices["close"],
                                            panel.matrices["funding"], 0.3,
                                            cadence_rows(ev, 1))

    # ---- Step 0: trial 注册(先于本驱动器四阶段,register-then-evaluate)----
    print("=== trial 注册(8 候选 × 单批 ppy52)===", flush=True)
    registration = register_family_trials(specs, signals, panel, registry=registry,
                                          recorded_at=recorded_at)
    print(f"  trial_count {registration['trial_count_before']} → {registration['trial_count_after']} "
          f"(Δ {registration['trial_count_delta']})", flush=True)

    # ---- 四阶段流水线(每变体全跑;硬失败短路记阶段)----
    results, price_streams = {}, {}
    for spec in specs:
        vid = spec["id"]
        print(f"\n--- {vid} ({spec['kind']}) ---", flush=True)
        stages, stage_reached, error = {}, "none", None
        try:
            stage_reached = "ic"
            stages["ic"] = stage_ic(panel, signals[vid])
            stage_reached = "falsification"
            stages["falsification"] = stage_gauntlet(
                panel, {n: (p2, subset_signals[vid][n]) for n, p2 in subset_panels.items()},
                spec, signals[vid], carry_daily, ev)
            price_streams[vid] = tuple(streams_cadence(
                signals[vid], panel.matrices["close"], panel.matrices["funding"],
                spec["quantile"], cadence_rows(ev, spec["cadence_days"]))[0])
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
        icc = (stages.get("ic") or {}).get("h7") or {}
        print(f"  IC(h7): mean={icc.get('mean_ic')} t={icc.get('t_stat')} · "
              f"证伪 {ga.get('n_pass')}/5 · VT 夏普 {dr.get('sharpe_vt')} · 回撤 {dr.get('max_drawdown_vt')}"
              + (f" · ERROR {error}" if error else ""), flush=True)

    # ---- 判定(预注册 5 条;基线豁免 vs_baseline_vt)----
    def _judge_inputs(vid):
        st = results[vid]["stages"]
        return {"t_stat": ((st.get("ic") or {}).get("h7") or {}).get("t_stat"),
                "falsification_passes": (st.get("falsification") or {}).get("n_pass"),
                "vt_sharpe": (st.get("dry_run") or {}).get("sharpe_vt"),
                "max_drawdown_vt": (st.get("dry_run") or {}).get("max_drawdown_vt"),
                "capacity_aum_low": (st.get("capacity") or {}).get("aum_low"),
                "capacity_data_quality": (st.get("capacity") or {}).get("data_quality")}

    baseline_inputs = _judge_inputs("mr_cs_rev_5d")
    verdicts = {}
    for spec in specs:
        vid = spec["id"]
        if results[vid]["stage_reached"] != "complete":
            verdicts[vid] = {"verdict": "REJECTED", "failed_bars": ["hard_failure"],
                             "stage_reached": results[vid]["stage_reached"]}
            continue
        v = verdict_for(_judge_inputs(vid), baseline_inputs["vt_sharpe"], role=spec["role"])
        v["stage_reached"] = "complete"
        verdicts[vid] = v

    candidates = [vid for vid, v in verdicts.items()
                  if v["verdict"] == "CANDIDATE" and vid != "mr_cs_rev_5d"]
    corr = stream_correlation_matrix(price_streams)
    off_diag = [rho for a, row in corr.items() for b, rho in row.items() if a < b and rho is not None]
    payload = {
        "schema_version": 1,
        "evidence_class": "RESEARCH_CLUSTER_PREREGISTERED",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": recorded_at // 1000,
        "pre_registration": {**PRE_REGISTRATION,
                             "variants": [{"id": s["id"], "role": s["role"], "kind": s["kind"],
                                           "expression": ax.serialize(s["expr"]),
                                           "quantile": s["quantile"], "cadence_days": s["cadence_days"],
                                           "rationale": s["rationale"]} for s in specs]},
        "dataset": {"universe": _UNIVERSE, "n_instruments": len(instruments),
                    "instruments": instruments, "n_event_times": len(ev),
                    "date_start": time.strftime("%Y-%m-%d", time.gmtime(ev[0] / 1000)),
                    "date_end": time.strftime("%Y-%m-%d", time.gmtime(ev[-1] / 1000)),
                    "dataset_version": registration["dataset_version"]},
        "trial_registration": registration,
        "results": results,
        "baseline_inputs": baseline_inputs,
        "verdicts": verdicts,
        "candidates": candidates,
        "honesty": {
            "trial_count": 8,
            "trial_count_including_baseline": True,
            "registry_trial_count_before": registration["trial_count_before"],
            "registry_trial_count_after": registration["trial_count_after"],
            "variant_stream_correlation": corr,
            "variant_stream_correlation_offdiag_mean": (round(statistics.fmean(off_diag), 4)
                                                        if off_diag else None),
            "variant_stream_correlation_offdiag_max": (round(max(off_diag), 4)
                                                       if off_diag else None),
            "multiple_comparison_note": (
                "8 次试验同属一个信号族(同一残差主轴 + k 窗扫描 + 归一/beta 变体),book 高度相关——"
                "有效独立赌注数远小于 8;DSR(评估器按全 trial 总体泄气,Deflated Sharpe)逐候选见 "
                "trial_registration.candidates;有效独立 N 无既有机器 → 诚实陈述而非编造。判定门槛已预注册"
                "(pre_registration.verdict_bar),且比 naive 初筛更严(新簇主张:t≥2.5、VT≥1.0、DD≤20%、"
                "必须打败 plain 反转基线、容量必须可算)。"),
            "no_intraday_data_note": (
                "微观结构 MR(盘口价差回复、短周期 bars)按定义出范围:本地无 order-book/分钟级数据。"
                "本簇一切结论只覆盖日频 horizon;spec『首要风险:结构变化、趋势碾压』在日频形态下由 "
                "lag/成本/年份广度/子宇宙四项机器检验 + vs_baseline_vt 复杂度税部分覆盖,趋势碾压的"
                "直接证据 = 与 trend/carry 的相关与增量 IR(adds_to_carry 条)。"),
            "caveats": [
                "对已知历史的执行现实回测,非前向/实盘;是否送前向 paper-track 是另一独立决策。",
                "干跑变体不落 dry_run.db(不污染正典库);数字以本文件为准。",
                "容量带 UNCALIBRATED_PRINCIPLED(冲击系数无法校准,区间带非单点)。",
                "无既有 MR 评估制品可锚定(本平台首次 MR 评估);机器与 crowd_contra 变体族共享且被其测试钉死。",
                "残差定义 = 币日收益 − 等权市场日收益(beta=1);mr_resid_beta_5d 检验 beta 剥离精度的增量。",
                "前向 paper 台账(tools/forward_track_advance.py)跑在 OI 大币宇宙(~14–24 币),"
                "与本研究的 115 币宇宙不同——若注册 CANDIDATE,前向证据是窄宇宙口径,已记录。",
            ],
        },
    }
    atomic_write_json(_OUT, payload, indent=2)

    print("\n=== 判定汇总(预注册 5 条;基线豁免 vs_baseline_vt)===", flush=True)
    print(f"基线: t={baseline_inputs['t_stat']} 证伪 {baseline_inputs['falsification_passes']}/5 "
          f"VT {baseline_inputs['vt_sharpe']} DD {baseline_inputs['max_drawdown_vt']}", flush=True)
    for spec in specs:
        vid = spec["id"]
        v = verdicts[vid]
        ji = _judge_inputs(vid)
        print(f"  {vid:20s} {v['verdict']:9s} t={ji['t_stat']} 证伪{ji['falsification_passes']}"
              f" VT={ji['vt_sharpe']} DD={ji['max_drawdown_vt']} "
              f"cap={_fmt_money(ji['capacity_aum_low'])} failed={v['failed_bars'] or '-'}", flush=True)
    print(f"\nCANDIDATES({len(candidates)} 非基线): {candidates or 'NONE(诚实零候选)'}", flush=True)
    print(f"-> {_OUT}", flush=True)


def main() -> None:
    quarantine_legacy_backtest("tools.mean_reversion_cluster")
    with production_experiment_registry() as registry:
        _main(registry)


if __name__ == "__main__":
    main()
